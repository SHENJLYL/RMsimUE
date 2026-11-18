"""Blender: export the seven source RndGrass LOD0 meshes through the scene converter.

Original source inputs are read only. Outputs are a foliage supplement; no UE
assets, main scene manifest, or generated instances are modified.
"""
from __future__ import annotations
import argparse
import collections
import json
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_wt_scene import Builder, bounds, read, save, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    source = args.source.resolve()
    output = args.output.resolve()
    if output == source.parent or output.is_relative_to(source.parent):
        raise ValueError('prepared output must be separate from source extraction')
    if output.exists():
        raise ValueError('use a fresh output directory')
    manifest = read(source)
    if manifest['schema'] != 'wt_foliage_source_v1':
        raise ValueError('unexpected source foliage schema')
    output.mkdir(parents=True)
    (output / 'meshes').mkdir()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.unit_settings.system = 'METRIC'
    bpy.context.scene.unit_settings.scale_length = 1
    builder = Builder.__new__(Builder)
    builder.output = output
    builder.known = {}
    builder.journal = output / 'mesh-journal.jsonl'
    builder.materials = {}
    result = []
    for spec in manifest['models']:
        path = source.parent / spec['file']
        if sha(path) != spec['sha256']:
            raise ValueError('source NPZ hash differs: ' + spec['id'])
        with np.load(path) as archive:
            data = {key: archive[key].copy() for key in archive.files}
        if data['triangles'].shape != (spec['triangles'], 3):
            raise ValueError('source triangle count differs')
        v, f, n = (data[key] for key in ('vertices', 'triangles', 'normals'))
        dot = np.sum(np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]) * n[f].mean(axis=1), axis=1)
        flip = dot < -1e-10
        data['triangles'][flip] = f[flip][:, [0, 2, 1]]
        used = spec['used_material_ids']
        slots = [builder.register(spec['materials'][index]) for index in used]
        converted = builder.converted(data, {index: slot for slot, index in enumerate(used)})
        part = builder.mesh(spec['id'], converted, slots)
        part.update(source_npz_file=str(path), source_npz_sha256=spec['sha256'],
                    source_resource_sha256=spec['source_sha256'],
                    source_group_sha256=spec['source_group_sha256'],
                    source_coordinates='Dagor metres, Y up',
                    output_coordinates='ROS metres, Z up; Dagor(x,y,z) -> (x,-z,y)',
                    faces_reoriented_to_source_normals=int(flip.sum()),
                    material=spec['materials'][used[0]], collision=False,
                    source_bounds_dagor_m=spec['bounds_dagor_m'])
        result.append(part)
    roundtrips = []
    for part in result:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.fbx(filepath=str(output / part['file']))
        objects = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
        if len(objects) != 1:
            raise ValueError('FBX must roundtrip one source mesh')
        obj = objects[0]
        points = np.array([obj.matrix_world @ vertex.co for vertex in obj.data.vertices])
        obj.data.calc_loop_triangles()
        triangles = len(obj.data.loop_triangles)
        actual = bounds(points)
        error = max(float(np.abs(np.asarray(actual[key]) - part['bounds_m'][key]).max()) for key in ('lo', 'hi'))
        if triangles != part['triangles'] or error > 1e-5 or len(obj.data.uv_layers) < 1:
            raise ValueError('source foliage FBX roundtrip differs: ' + part['id'])
        roundtrips.append(dict(id=part['id'], triangles=triangles, vertices=len(points),
                              uv_layers=len(obj.data.uv_layers), max_bound_error_m=error))
    report = dict(schema='wt_foliage_mesh_preparation_v1', status='passed',
                  source_file=str(source), source_sha256=sha(source), models=result,
                  source_models=len(result), source_triangles=sum(item['triangles'] for item in result),
                  roundtrips=roundtrips,
                  fbx_export=dict(axis_forward='X', axis_up='Z', metric_scale_length=1,
                                  apply_unit_scale=True, apply_scale_options='FBX_SCALE_UNITS'),
                  source_inputs_unchanged=all(sha(source.parent / item['file']) == item['sha256']
                                              for item in manifest['models']),
                  generated_instances=0)
    if not report['source_inputs_unchanged']:
        raise ValueError('source inputs changed')
    save(output / 'mesh-preparation.json', report)
    print('WT_FOLIAGE_MESHES', json.dumps(dict(status=report['status'], models=len(result),
          triangles=report['source_triangles'], source_sha256=report['source_sha256'],
          output=str(output))), flush=True)


if __name__ == '__main__':
    main()
