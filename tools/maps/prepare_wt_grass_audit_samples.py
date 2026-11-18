"""Prepare source-PNG expectations for independent native grass weight readback.

Run with ordinary Python (NumPy/Pillow), before the separate UE reload audit.
The foliage package and original source samples are read only.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import sys
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'unreal'))
from wt_grass import validate_foliage, _sha

POINTS_ROS = ((1028.0, -1020.0), (1074.25, -1011.75),
              (893.75, -1029.25), (1019.25, -1217.75))


def bilinear_channel(array, px, py, channel):
    x0, y0 = math.floor(px), math.floor(py)
    fx, fy = px-x0, py-y0
    h, w = array.shape[:2]
    x1, y1 = min(w-1, x0+1), min(h-1, y0+1)
    x0, y0 = max(0, min(w-1, x0)), max(0, min(h-1, y0))
    top = float(array[y0,x0,channel])*(1-fx)+float(array[y0,x1,channel])*fx
    bottom = float(array[y1,x0,channel])*(1-fx)+float(array[y1,x1,channel])*fx
    return top*(1-fy)+bottom*fy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('use a fresh native sample fixture file')
    checked = validate_foliage(args.package)
    definition, package = checked['definition'], checked['package']
    metadata_file = package/'heightfield.json'
    height = json.loads(metadata_file.read_text(encoding='utf-8'))
    if height['cell_m'] != .5 or height['origin_ros_m'] != [0,0,0]:
        raise ValueError('this fixed Flanders fixture requires the original half-metre grid and origin')
    height_path = package/height['height_file']
    if _sha(height_path) != height['source_samples_sha256']:
        raise ValueError('original source height bytes differ')
    hdata = np.fromfile(height_path, dtype='<u2').reshape(height['height'], height['width'])
    fields = []
    for index, (x,y) in enumerate(POINTS_ROS):
        gx, gy = x/.5, -y/.5
        ix, iy = math.floor(gx), math.floor(gy)
        fx, fy = gx-ix, gy-iy
        hc = hdata[iy:iy+2,ix:ix+2].astype(np.float64)
        value = (hc[0,0]*(1-fx)+hc[0,1]*fx)*(1-fy)+(hc[1,0]*(1-fx)+hc[1,1]*fx)*fy
        z = height['minimum_m']+value*height['range_m']/65535
        corners = [[ix*50,iy*50],[(ix+1)*50,iy*50],[ix*50,(iy+1)*50],[(ix+1)*50,(iy+1)*50]]
        fields.append(dict(id='spawn' if index==0 else 'field_'+str(index), ros_xy_m=[x,y],
            world_cm=[x*100,-y*100,z*100], source_grid_xy=[gx,gy], fractions=[fx,fy],
            corner_world_xy_cm=corners, expected_grass='zero' if index==0 else 'positive',
            layers=[]))
    for texture in definition['weight_textures']:
        filename = checked['base']/texture['file']
        data = np.asarray(Image.open(filename))
        if data.dtype != np.uint8 or data.shape != (4096,4096,4):
            raise ValueError('unexpected source weight PNG')
        for layer in definition['layers']:
            if layer['weight_texture'] != texture['id']:
                continue
            for point in fields:
                values = [bilinear_channel(data,xy[0]/50-.5,4096-xy[1]/50-.5,layer['weight_channel'])
                          for xy in point['corner_world_xy_cm']]
                quantized = [max(0,min(255,round(v))) for v in values]
                fx,fy = point['fractions']
                interpolated = ((quantized[0]*(1-fx)+quantized[1]*fx)*(1-fy)
                                +(quantized[2]*(1-fx)+quantized[3]*fx)*fy)
                point['layers'].append(dict(name=layer['id'], weight_texture=texture['id'],
                    weight_channel=layer['weight_channel'], expected_corners_float=values,
                    expected_corners_byte=quantized, expected_interpolated_byte=interpolated))
    for point in fields:
        point['layers'].sort(key=lambda x:x['name'])
        nonzero = sum(layer['expected_interpolated_byte']>0 for layer in point['layers'])
        if len(point['layers'])!=13 or ((point['expected_grass']=='zero') != (nonzero==0)):
            raise ValueError('source field predicate differs from audited original weights')
        point['nonzero_layer_count'] = nonzero
    report = dict(schema='wt_grass_native_sample_fixture_v1', status='passed',
        definition_sha256=checked['definition_sha256'], source_level_sha256=definition['source_level_sha256'],
        heightfield_sha256=_sha(metadata_file), source_height_sha256=_sha(height_path),
        world_to_weight_uv=definition['weight_world_uv'],
        source_weights={item['id']:dict(file=item['file'],sha256=item['sha256']) for item in definition['weight_textures']},
        source_sampling='uncompressed linear RGBA; normalized UV bilinear LOD0 with clamp; source height grid vertices',
        byte_tolerance=1, points=fields, generator_sha256=_sha(__file__))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='passed',fixture=str(args.output.resolve()),sha256=_sha(args.output),
        points=[dict(id=p['id'],world_cm=p['world_cm'],nonzero_layers=p['nonzero_layer_count'])
                for p in fields]),indent=2))


if __name__ == '__main__':
    main()
