"""Blender: prepare varied UE grass cards with original source tilt parameters.

This geometry is an enhancement adapter, with recorded metre dimensions and UV
tiling. The seven original RndGrass meshes remain unchanged in their source pack.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import sys
import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_wt_scene import Builder, bounds, read, save, sha

# Nominal sizes are deliberately short; native GrassType adds bounded variation.
CARDS = {'generic_grass_small_a': (.36, .24),
         'generic_grass_tall_a': (.30, .35),
         'generic_grass_tall_b': (.32, .28)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(sys.argv[sys.argv.index('--')+1:])
    if a.output.exists():
        raise ValueError('use a fresh output directory')
    source = read(a.source)
    if source['schema'] != 'wt_small_grass_source_v1' or source['status'] != 'passed':
        raise ValueError('verified source texture extraction required')
    a.output.mkdir(parents=True)
    (a.output/'meshes').mkdir()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.unit_settings.system = 'METRIC'
    bpy.context.scene.unit_settings.scale_length = 1
    builder = Builder.__new__(Builder)
    builder.output = a.output
    builder.known = {}
    builder.journal = a.output/'mesh-journal.jsonl'
    builder.materials = {}
    rows = []
    for spec in source['models']:
        name = spec['id']; width, height = CARDS[name]
        parameters = {p['name']: p['value'] for p in spec['parameters']}
        if 'normal_texture' not in spec:
            raise ValueError('version 2 source normal/illumination texture is required')
        tilt_add = float(parameters['vertical_angle_add'])
        tilt_mul = float(parameters['vertical_angle_mul'])
        vertices, uv, triangles, normals, planes = [], [], [], [], []
        for plane in range(3):
            angle = plane*math.pi/3 + (0.0, .18, -.13)[plane]
            sample = (.17, .53, .89)[plane]
            tilt = tilt_add + tilt_mul*sample
            blade_height = height*(.90, 1.13, .78)[plane]
            along = np.asarray([math.cos(angle), math.sin(angle), 0.0])
            lean = np.asarray([-math.sin(angle), math.cos(angle), 0.0])
            center = np.asarray([.015*math.cos(plane*2.4), .015*math.sin(plane*2.4), 0.0])
            side = along*(width*.5*(.86, 1.05, .94)[plane])
            tip = lean*(blade_height*math.sin(tilt)) + np.asarray([0, 0, blade_height*math.cos(tilt)])
            tip_normal = np.cross(along, tip)
            tip_normal /= np.linalg.norm(tip_normal)
            offset = len(vertices)
            vertices.extend([center-side, center+side, center+side+tip, center-side+tip])
            # Blender -> UE flips V. Source GPU has base V=1, tip V=0.
            # One complete original atlas per UE card avoids a repeated tuft
            # within every small clump. This is an explicit geometry adaptation.
            uv.extend([[0,0], [1,0], [1,1], [0,1]])
            normals.extend([[0,0,1], [0,0,1], tip_normal.tolist(), tip_normal.tolist()])
            triangles.extend([[offset,offset+1,offset+2], [offset,offset+2,offset+3]])
            planes.append(dict(azimuth_rad=angle, source_tilt_rad=tilt, source_random_sample=sample,
                               plane_length_m=blade_height, top_z_m=float(tip[2])))
        data = dict(vertices=np.asarray(vertices, dtype=np.float32),
            triangles=np.asarray(triangles, dtype=np.int32), uvs=np.asarray(uv, dtype=np.float32),
            normals=np.asarray(normals, dtype=np.float32), material_ids=np.zeros(6, dtype=np.int32))
        slot = builder.register(dict(shader='ue_small_grass_card', parameters='', texture_slots=[spec['texture']]))
        part = builder.mesh('ue_'+name, data, [slot])
        part.update(geometry_adapter=dict(schema='wt_ue_grass_crossed_card_v1', version=2, planes=3,
            width_m=width, height_m=height, source_tile_tc_x=2, uv_origin='source GPU base V=1, tip V=0',
            ue_tile_tc_x=1, texture_address='clamp', plane_variations=planes,
            source_tilt_expression='vertical_angle_add + vertical_angle_mul * random',
            source_tilt_reference='grass_generate_inc.dshl:254-257',
            vertex_normals='roots use source ground-up; tips follow the tilted card normal',
            normal_reference='grass_generate_inc.dshl:525-529',
            variation_scope='three deterministic tilt/height/azimuth samples per UE clump, with native per-instance random scale/yaw',
            coordinates='ROS metres, Z up', source_model_type=name), collision=False)
        rows.append(part)
    checks = []
    for part in rows:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.fbx(filepath=str(a.output/part['file']))
        objects = [o for o in bpy.context.scene.objects if o.type == 'MESH']
        if len(objects) != 1:
            raise ValueError('expected one card mesh')
        obj = objects[0]; obj.data.calc_loop_triangles()
        actual = bounds(np.asarray([obj.matrix_world@v.co for v in obj.data.vertices]))
        error = max(float(np.max(np.abs(np.asarray(actual[k])-part['bounds_m'][k]))) for k in ('lo','hi'))
        if len(obj.data.loop_triangles) != 6 or error > 1e-5 or len(obj.data.uv_layers) != 1:
            raise ValueError('card FBX roundtrip differs')
        normal_matrix = obj.matrix_world.to_3x3().inverted().transposed()
        root_normal_dots = []
        for loop in obj.data.loops:
            world_position = obj.matrix_world @ obj.data.vertices[loop.vertex_index].co
            if abs(world_position.z) < 1e-5:
                normal = (normal_matrix @ obj.data.corner_normals[loop.index].vector).normalized()
                root_normal_dots.append(float(normal.z))
        if not root_normal_dots or min(root_normal_dots) < .999:
            raise ValueError('source ground-up root normals lost in FBX roundtrip')
        checks.append(dict(id=part['id'], triangles=6, uv_channels=1, max_bound_error_m=error,
                           root_normal_min_dot_up=min(root_normal_dots), custom_normals=True))
    report = dict(schema='wt_ue_small_grass_mesh_v1', version=2, status='passed',
        source_sha256=sha(a.source), models=rows, roundtrips=checks,
        source_geometry_claim='explicit UE enhancement; no original stored grass mesh substituted',
        exporter_sha256=sha(Path(__file__)))
    save(a.output/'mesh-preparation.json', report)
    print(json.dumps(dict(status='passed', models=len(rows), triangles=18,
        report_sha256=sha(a.output/'mesh-preparation.json'))))


if __name__ == '__main__':
    main()
