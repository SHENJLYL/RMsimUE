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


def independent_footprint_witnesses(checked, points):
    """Direct source triangle tests, independent of the prepared raster masks."""
    proof = checked['enhancement_proof']
    repo = checked['package'].parents[2]
    xy = np.asarray([[p[0],-p[1]] for p in points], dtype=np.float64)
    witnesses = [[] for _ in points]
    def cross2(a,b):
        return a[...,0]*b[...,1]-a[...,1]*b[...,0]
    def query(vertices, faces, kind, path, margin, material_ids=None):
        tri = vertices[faces][:,:,[0,2]].astype(np.float64)
        if not len(tri): return
        lo,hi = tri.min(axis=1),tri.max(axis=1)
        for pi,point in enumerate(xy):
            near = np.flatnonzero(np.all(point>=lo-margin,axis=1)&np.all(point<=hi+margin,axis=1))
            if not len(near): continue
            t = tri[near]
            area = cross2(t[:,1]-t[:,0],t[:,2]-t[:,0])
            active = np.abs(area)>1e-8
            t=t[active];near=near[active];area=area[active]
            if not len(near):continue
            w1=cross2(point-t[:,0],t[:,2]-t[:,0])/area
            w2=cross2(t[:,1]-t[:,0],point-t[:,0])/area
            inside=(w1>=0)&(w2>=0)&(w1+w2<=1)
            distance=np.full(len(t),np.inf)
            for a,b in ((0,1),(1,2),(2,0)):
                edge=t[:,b]-t[:,a];den=(edge*edge).sum(axis=1)
                frac=np.clip(np.divide(((point-t[:,a])*edge).sum(axis=1),den,out=np.zeros_like(den),where=den>0),0,1)
                distance=np.minimum(distance,np.linalg.norm(point-(t[:,a]+frac[:,None]*edge),axis=1))
            hit=np.flatnonzero(inside|(distance<=margin))
            for h in hit[:max(0,8-len(witnesses[pi]))]:
                witnesses[pi].append(dict(category=kind,source_file=path,triangle_index_in_selected_array=int(near[h]),
                    exact_projected_inside=bool(inside[h]),distance_to_edge_m=float(distance[h]),buffer_m=margin))
    layouts={}
    for record in proof['source_geometry']:
        kind=record.get('category')
        if kind is None:continue
        path=repo/record['file']
        if _sha(path)!=record['sha256']:raise ValueError('independent footprint source hash differs')
        with np.load(path) as archive:
            v=archive['vertices'].astype(np.float64);f=archive['triangles']
            if kind=='road_rail':
                query(v,f[np.isin(archive['material_ids'],proof['hard_material_indices'])],kind,record['file'],1.0)
            elif kind=='static_world':
                query(v,f,kind,record['file'],.5)
            elif kind=='instanced_PHYS_COLLIDABLE':
                layout_file=record['transform_source']
                if layout_file not in layouts:layouts[layout_file]=json.loads((repo/layout_file).read_text(encoding='utf-8-sig'))
                entry=layouts[layout_file][record['model']]
                if entry['pos_inst']:raise ValueError('footprint adapter expected stored affine matrix')
                radius=float(np.linalg.norm(v,axis=1).max())
                for row in entry['instances']:
                    tm=np.asarray(row,dtype=np.float64).reshape(3,4)
                    r=radius*np.linalg.norm(tm[:,:3],ord=2)+.5
                    if not np.any(np.linalg.norm(xy-tm[[0,2],3],axis=1)<=r):continue
                    query(v@tm[:,:3].T+tm[:,3],f,kind,record['file'],.5)
    return witnesses


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--foliage-base', type=Path, help='validate a staged foliage package against the canonical map sources')
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('use a fresh native sample fixture file')
    checked = validate_foliage(args.package, args.foliage_base)
    definition, package = checked['definition'], checked['package']
    metadata_file = package/'heightfield.json'
    height = json.loads(metadata_file.read_text(encoding='utf-8'))
    if height['cell_m'] != .5 or height['origin_ros_m'] != [0,0,0]:
        raise ValueError('this fixed Flanders fixture requires the original half-metre grid and origin')
    height_path = package/height['height_file']
    if _sha(height_path) != height['source_samples_sha256']:
        raise ValueError('original source height bytes differ')
    hdata = np.fromfile(height_path, dtype='<u2').reshape(height['height'], height['width'])
    enhanced = checked['enhancement_proof'] is not None
    source_points = checked['enhancement_proof']['samples'] if enhanced else None
    point_coordinates = [tuple(p['ros_xy_m']) for p in source_points] if enhanced else POINTS_ROS
    witnesses = independent_footprint_witnesses(checked,point_coordinates) if enhanced else None
    fields = []
    for index, (x,y) in enumerate(point_coordinates):
        gx, gy = x/.5, -y/.5
        ix, iy = math.floor(gx), math.floor(gy)
        fx, fy = gx-ix, gy-iy
        hc = hdata[iy:iy+2,ix:ix+2].astype(np.float64)
        value = (hc[0,0]*(1-fx)+hc[0,1]*fx)*(1-fy)+(hc[1,0]*(1-fx)+hc[1,1]*fx)*fy
        z = height['minimum_m']+value*height['range_m']/65535
        corners = [[ix*50,iy*50],[(ix+1)*50,iy*50],[ix*50,(iy+1)*50],[(ix+1)*50,(iy+1)*50]]
        expected = ('zero' if source_points[index]['hard'] else 'positive') if enhanced else ('zero' if index==0 else 'positive')
        if enhanced and (bool(witnesses[index]) != (expected=='zero')):
            raise ValueError('prepared point surface differs from direct original geometry: '+source_points[index]['id'])
        fields.append(dict(id=source_points[index]['id'] if enhanced else ('spawn' if index==0 else 'field_'+str(index)), ros_xy_m=[x,y],
            world_cm=[x*100,-y*100,z*100], source_grid_xy=[gx,gy], fractions=[fx,fy],
            corner_world_xy_cm=corners, expected_grass=expected,
            independent_source_footprint_witnesses=witnesses[index] if enhanced else None,layers=[]))
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
        if len(point['layers'])!=len(definition['layers']) or ((point['expected_grass']=='zero') != (nonzero==0)):
            raise ValueError('source field predicate differs from audited original weights')
        point['nonzero_layer_count'] = nonzero
    report = dict(schema='wt_grass_native_sample_fixture_v1', status='passed',
        definition_sha256=checked['definition_sha256'], source_level_sha256=definition['source_level_sha256'],
        heightfield_sha256=_sha(metadata_file), source_height_sha256=_sha(height_path),
        world_to_weight_uv=definition['weight_world_uv'],
        source_weights={item['id']:dict(file=item['file'],sha256=item['sha256']) for item in definition['weight_textures']},
        source_sampling='uncompressed linear RGBA; normalized UV bilinear LOD0 with clamp; source height grid vertices',
        byte_tolerance=1, points=fields, generator_sha256=_sha(__file__))
    if enhanced:
        report['enhancement_proof_sha256']=definition['enhancement_proof']['sha256']
        report['spatial_predicates']='direct original X/Z triangle point/edge distance tests; separate from prepared mask rasterization'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='passed',fixture=str(args.output.resolve()),sha256=_sha(args.output),
        points=[dict(id=p['id'],world_cm=p['world_cm'],nonzero_layers=p['nonzero_layer_count'])
                for p in fields]),indent=2))


if __name__ == '__main__':
    main()
