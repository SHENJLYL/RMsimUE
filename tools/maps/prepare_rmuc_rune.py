"""Derive the RMUC2027 Rune split from the checksum-pinned full-map CAD tessellation."""
import argparse
import json
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_rmuc2027 import array, digest, read_glb, rmuc_visual_material


GLB_SHA256 = '09ca8e9d088e1945faf095cb90206fe330bd97ab6f57bd3fb2cf3b9645ed7b61'
COLLISION_GLB_SHA256 = '9a36ee13d6284f60e7da1f2631af15472e9611bb7b2bad36ea84d92d87e47911'
TRANSLATION_M = np.array([-0.000007152952372635468, -1.6243433957331543, 1.6403436655816601])
PIVOT_M = np.array([0., 0., 2.599])
AXIS = np.array([1., -1., 0.]) / np.sqrt(2.)
TANGENT = np.array([1., 1., 0.]) / np.sqrt(2.)
ANGLE = 2. * np.pi / 5.
OUTPOST_PIVOT_M = np.array([3.099616, 3.894873, 1.490])
RUNE_TRIM_MATERIALS = (0, 2, 7, 11)
RUNE_BEARINGS = ((.204, .300, .105), (.263, .290, .173))
OUTPOST_BEARING = (-.021, .016, .195)


def scene_nodes(doc, group):
    def visit(index, parent):
        node = doc['nodes'][index]
        matrix = parent @ np.array(node.get('matrix', np.eye(4).ravel('F'))).reshape(4, 4, order='F')
        if node.get('name', '').startswith(group + '_') and 'mesh' in node:
            yield node, matrix
        for child in node.get('children', []):
            yield from visit(child, matrix)

    for root in doc['scenes'][doc.get('scene', 0)]['nodes']:
        yield from visit(root, np.eye(4))


def source_parts(doc, blob, node, matrix):
    offset = 0
    for primitive in doc['meshes'][node['mesh']]['primitives']:
        p = array(doc, blob, primitive['attributes']['POSITION']) @ matrix[:3, :3].T
        p += matrix[:3, 3] + TRANSLATION_M
        n = array(doc, blob, primitive['attributes']['NORMAL']) @ np.linalg.inv(matrix[:3, :3])
        n /= np.linalg.norm(n, axis=1)[:, None]
        f = array(doc, blob, primitive['indices']).astype(np.int32).reshape(-1, 3)
        if np.linalg.det(matrix[:3, :3]) < 0.:
            f = f[:, [0, 2, 1]]
        yield p, n, f, primitive['material'], offset
        offset += len(p)


def welded_components(parts):
    points = np.concatenate([p for p, _, _, _, _ in parts])
    _, vertices = np.unique(np.rint(points * 100000).astype(np.int64), axis=0, return_inverse=True)
    parent = list(range(int(vertices.max()) + 1))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for p, n, f, material, offset in parts:
        for a, b, c in vertices[f + offset].tolist():
            ancestor = root(a)
            parent[root(b)] = ancestor
            parent[root(c)] = ancestor

    components = np.array([root(i) for i in range(len(parent))])[vertices]
    return points, components, len(parent)


def moving_vertices(parts):
    # Preserve whole CAD components. Crossbeam, motors, connectors and uprights stay fixed.
    points, components, count = welded_components(parts)
    delta = points - PIVOT_M
    along = delta @ AXIS
    radius = np.hypot(delta @ TANGENT, delta[:, 2])
    low = np.full(count, np.inf)
    high = np.full(count, -np.inf)
    outer = np.zeros(count)
    np.minimum.at(low, components, along)
    np.maximum.at(high, components, along)
    np.maximum.at(outer, components, radius)
    red = (low >= .25) & (high <= .36) & (outer <= .98)
    blue = (high <= -.25) & (low >= -.36) & (outer <= .98)
    return (red[components] | blue[components])


def outpost_rotor(parts, pivot, axis):
    points, components, count = welded_components(parts)
    height = (points - pivot) @ axis
    radius = np.linalg.norm(points - pivot - np.outer(height, axis), axis=1)
    min_r = np.full(count, np.inf)
    low = np.full(count, np.inf)
    high = np.full(count, -np.inf)
    np.minimum.at(min_r, components, radius)
    np.minimum.at(low, components, height)
    np.maximum.at(high, components, height)
    # Three armor modules, six light bars and attached low-poly hardware are separate solids.
    discrete = (min_r > .230) & (low > -.19) & (high < .15)
    size = np.bincount(components, minlength=count)
    assert np.count_nonzero(discrete & (size > 4000)) == 3
    # The three supports share a welded mesh with the fixed top housing in this tessellation.
    # Partition their outboard triangles while keeping the central cap and lower structure.
    support = (components == size.argmax()) & (radius > .178) & (height > -.22) & (height < .146)
    return discrete[components], support


def build_mesh(output, name, parts):
    positions, normals, faces, material_names = [], [], [], []
    slot_ids = {}
    offset = 0
    for p, n, f, slot in parts:
        if len(f) == 0:
            continue
        ids, used = np.unique(f, return_inverse=True)
        positions.append(p[ids])
        normals.append(n[ids])
        faces.append(used.reshape(-1, 3) + offset)
        material_names.append(np.full(len(f), slot_ids.setdefault(slot, len(slot_ids)), dtype=np.int32))
        offset += len(ids)
    p = np.concatenate(positions).astype(np.float32)
    n = np.concatenate(normals).astype(np.float32)
    f = np.concatenate(faces).astype(np.int32)
    mesh = bpy.data.meshes.new(name)
    mesh.vertices.add(len(p))
    mesh.vertices.foreach_set('co', p.ravel())
    mesh.loops.add(f.size)
    mesh.loops.foreach_set('vertex_index', f.ravel())
    mesh.polygons.add(len(f))
    mesh.polygons.foreach_set('loop_start', np.arange(0, f.size, 3, dtype=np.int32))
    mesh.polygons.foreach_set('loop_total', np.full(len(f), 3, dtype=np.int32))
    for slot in slot_ids:
        mesh.materials.append(bpy.data.materials.get(slot) or bpy.data.materials.new(slot))
    mesh.polygons.foreach_set('material_index', np.concatenate(material_names))
    mesh.update()
    mesh.normals_split_custom_set_from_vertices(n.tolist())
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    file = output / (name + '.fbx')
    bpy.ops.export_scene.fbx(filepath=str(file), use_selection=True, object_types={'MESH'},
        axis_forward='X', axis_up='Z', apply_unit_scale=True, apply_scale_options='FBX_SCALE_UNITS',
        use_mesh_modifiers=True, mesh_smooth_type='OFF', add_leaf_bones=False, bake_anim=False, path_mode='AUTO')
    bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.meshes.remove(mesh)
    return dict(name=name, file=file.name, sha256=digest(file), triangles=len(f),
        bounds_m=[p.min(axis=0).tolist(), p.max(axis=0).tolist()], materials=list(slot_ids))


def checked_source(source):
    if digest(source) != GLB_SHA256:
        raise ValueError('Field device split requires the pinned RMUC2027 full-map.glb')
    return read_glb(source)


def init_blender(output):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.unit_settings.system = 'METRIC'
    bpy.context.scene.unit_settings.scale_length = 1.
    output.mkdir(parents=True, exist_ok=True)


def turned_bearing(origin, axis, start, end, radius, slot, sides=40):
    # Separate smooth barrel normals from the two end faces.
    basis = np.cross(axis, [1., 0., 0.])
    basis /= np.linalg.norm(basis)
    angle = np.arange(sides) * (2. * np.pi / sides)
    radial = np.outer(np.cos(angle), basis) + np.outer(np.sin(angle), np.cross(axis, basis))
    low, high = origin + start * axis, origin + end * axis
    bottom = low + radius * radial
    top = high + radius * radial
    positions = np.vstack((bottom, top, bottom, top, low, high))
    normals = np.vstack((radial, radial, np.tile(-axis, (sides, 1)), np.tile(axis, (sides, 1)), -axis, axis))
    faces = []
    for i in range(sides):
        j = (i + 1) % sides
        faces.extend(((i, j, j+sides), (i, j+sides, i+sides),
                      (4*sides, j+2*sides, i+2*sides), (4*sides+1, i+3*sides, j+3*sides)))
    return positions, normals, np.array(faces), slot


def prepare(source, output, include_outposts=False):
    doc, blob = checked_source(source)
    fixed, rotor = [], []
    source_triangles = 0
    matches = 0
    hub_mark_triangles = [0, 0]
    outposts = {'red': [], 'blue': []}
    bearings = []
    for node, matrix in scene_nodes(doc, 'centre-platform'):
        parts = list(source_parts(doc, blob, node, matrix))
        is_gantry = node['name'] == 'centre-platform_0_source_1354629_1'
        matches += int(is_gantry)
        moving = moving_vertices(parts) if is_gantry else None
        outpost = include_outposts and 'source_2603236_' in node['name']
        if outpost:
            team_sign = 1 if matrix[0, 3] > 0 else -1
            team = 'red' if team_sign == 1 else 'blue'
            pivot = OUTPOST_PIVOT_M * [team_sign, team_sign, 1]
            discrete, support = outpost_rotor(parts, pivot, matrix[:3, 1])
            # The welded cap was separated just outside r=.178 m around the lower
            # shelf. A coaxial bearing bridges the rotating arm roots through the full turn.
            bearings.append(turned_bearing(pivot, matrix[:3, 1], *OUTPOST_BEARING, 'RM27_M01'))
        for p, n, f, material, offset in parts:
            source_triangles += len(f)
            cad_material = f"RM27_M{rmuc_visual_material(node['name'], material):02d}"
            if outpost:
                vertices = f + offset
                mask = discrete[vertices[:, 0]] | support[vertices].all(axis=1)
                fixed.append((p, n, f[~mask], cad_material))
                outposts[team].append((p - pivot, n, f[mask], cad_material))
                continue
            mask = moving[f[:, 0] + offset] if is_gantry else np.zeros(len(f), dtype=bool)
            fixed_faces = f[~mask]
            if is_gantry and material in (2, 4):
                delta = p[fixed_faces].mean(axis=1) - PIVOT_M
                axial = delta @ AXIS
                radius = np.hypot(delta @ TANGENT, delta[:, 2])
                hub_mark = (np.abs(np.abs(axial) - .491) < .002) & (radius < .045)
                for team, side in enumerate((axial > 0., axial < 0.)):
                    mark = hub_mark & side
                    hub_mark_triangles[team] += int(mark.sum())
                    fixed.append((p, n, fixed_faces[mark], ('RuneHubRed', 'RuneHubBlue')[team]))
                fixed_faces = fixed_faces[~hub_mark]
            fixed.append((p, n, fixed_faces, cad_material))
            if not mask.any():
                continue
            local = p - PIVOT_M
            spinning = f[mask]
            centers = local[spinning].mean(axis=1)
            axial = centers @ AXIS
            angles = np.arctan2(centers @ TANGENT, centers[:, 2])
            plate = np.floor(angles / ANGLE + .5).astype(int) % 5
            # Separate each arm’s front trim so the referee can light each face independently.
            lamp = (np.abs(axial) >= .324) & (material in RUNE_TRIM_MATERIALS)
            rotor.append((local, n, spinning[~lamp], cad_material))
            for team, face in [('Red', axial > 0.), ('Blue', axial < 0.)]:
                for index in range(5):
                    rotor.append((local, n, spinning[lamp & face & (plate == index)],
                        f'Rune{team}{index}_M{material:02d}'))
    if matches != 1 or hub_mark_triangles != [54, 54]:
        raise ValueError('Expected one CAD gantry and two 54-triangle hub marks')

    # Fit the sleeve between the beam's 220 mm face and the two rotors' 266 mm
    # inboard faces. The outer flange overlaps the measured 154.6 mm arm roots.
    bearings.extend(turned_bearing(PIVOT_M, AXIS * side, lo, hi, radius, 'RM27_M01')
                    for side in (-1., 1.) for lo, hi, radius in RUNE_BEARINGS)
    support_triangles = sum(len(f) for _, _, f, _ in bearings)
    fixed.extend(bearings)
    init_blender(output)
    if include_outposts:
        assets = [build_mesh(output, 'rune_outposts_fixed_centre_platform', fixed)]
        assets.extend(build_mesh(output, f'outpost_{team}_rotor', parts) for team, parts in outposts.items())
        rune_triangles = sum(len(f) for _, _, f, _ in rotor)
        assert rune_triangles + sum(asset['triangles'] for asset in assets) == source_triangles + support_triangles
        assert min(asset['triangles'] for asset in assets) > 20_000
        report = {'source_sha256': GLB_SHA256, 'source_product': 2603236, 'source_triangles': source_triangles,
            'rune_triangles': rune_triangles, 'neutral_hub_mark_triangles': sum(hub_mark_triangles),
            'red_pivot_m': OUTPOST_PIVOT_M.tolist(),
            'visual_bearing_triangles': support_triangles,
            'visual_bearing_profile_m': {'rune': RUNE_BEARINGS, 'outpost': OUTPOST_BEARING}, 'assets': assets}
        (output / 'outpost-source.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print('RMUC_OUTPOST_PREPARED', json.dumps(report), flush=True)
        return
    fixed_report = build_mesh(output, 'rune_fixed_centre_platform', fixed)
    rotor_report = build_mesh(output, 'rune_rotor', rotor)
    assert all(any(slot.startswith(f'Rune{team}{i}_M') for slot in rotor_report['materials'])
        for team in ('Red', 'Blue') for i in range(5))
    assert fixed_report['triangles'] + rotor_report['triangles'] == source_triangles + support_triangles
    assert 100_000 < rotor_report['triangles'] < source_triangles
    report = {'source_sha256': GLB_SHA256, 'source_occurrence': 'centre-platform_0_source_1354629_1',
        'pivot_m': PIVOT_M.tolist(), 'axis': AXIS.tolist(), 'source_triangles': source_triangles,
        'neutral_hub_mark_triangles': sum(hub_mark_triangles),
        'rune_paints': {f'M{i:02d}': doc['materials'][i]['pbrMetallicRoughness']['baseColorFactor']
            for i in RUNE_TRIM_MATERIALS},
        'visual_bearing_triangles': support_triangles, 'visual_bearing_profile_m': {'rune': RUNE_BEARINGS},
        'assets': [fixed_report, rotor_report]}
    (output / 'rune-source.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('RMUC_RUNE_PREPARED', json.dumps(report), flush=True)


def dart_gate_components(parts, team):
    points, components, count = welded_components(parts)
    sign = 1. if team == 'red' else -1.
    roll = np.deg2rad(1.5)
    pitch = np.deg2rad(15.)
    up = np.array([0., -np.sin(roll), np.cos(roll)])
    across = np.array([0., np.cos(roll), np.sin(roll)])
    rail = np.sin(pitch) * np.array([1., 0., 0.]) + np.cos(pitch) * up
    normal = np.cos(pitch) * np.array([1., 0., 0.]) - np.sin(pitch) * up
    red = points * [sign, sign, 1.] - [13., -6.70, 0.]
    local = red @ np.column_stack((rail, across, normal))
    lo, hi = np.full((count, 3), np.inf), np.full((count, 3), -np.inf)
    np.minimum.at(lo, components, local)
    np.maximum.at(hi, components, local)
    extent = hi - lo
    guides = (extent[:, 0] > 1.48) & (extent[:, 0] < 1.51) & (extent[:, 1] > .027) & (extent[:, 1] < .029) & (extent[:, 2] < .006)
    assert guides.sum() == 2
    at_sides = (hi[:, 1] < -.285) | (lo[:, 1] > .33)
    carriage = np.isfinite(lo[:, 0]) & (lo[:, 0] > 1.039) & (hi[:, 0] < 1.317) & (lo[:, 2] > -.672) & (hi[:, 2] < -.585) & at_sides
    blocks = carriage & (extent[:, 0] > .123) & (extent[:, 0] < .133) & (extent[:, 1] > .100) & (extent[:, 2] > .03)
    assert blocks.sum() == 4 and not np.any(carriage & guides)
    lo_z, hi_z = np.full(count, np.inf), np.full(count, -np.inf)
    np.minimum.at(lo_z, components, points[:, 2])
    np.maximum.at(hi_z, components, points[:, 2])
    gate_parts = (lo_z > 1.1) & (hi_z > 1.8)
    moving = (gate_parts | carriage)[components]

    # The static CAD reproduces the rulebook's full open pose. Fit the lower stop
    # to the actual placement plane while retaining each carriage on its guide.
    faces = np.concatenate([f + offset for p, n, f, m, offset in parts])
    source_face_points = red[faces]
    upward = np.cross(source_face_points[:, 1] - source_face_points[:, 0],
                      source_face_points[:, 2] - source_face_points[:, 0])
    face_height = source_face_points @ up
    area = np.linalg.norm(upward, axis=1) * .5
    deck = (np.ptp(face_height, axis=1) < .0002) & (np.abs(face_height.mean(axis=1) - .0288) < .0003) & (upward @ up > area * 1.9) & (area > .00001)
    deck_points = np.unique(faces[deck])
    assert .59 < area[deck].sum() < .61
    deck_z = np.median(red[deck_points] @ up)
    deck_bounds = red[deck_points] @ np.column_stack(([1., 0., 0.], across, up))
    bottom_at_open = np.min(red[gate_parts[components]] @ up)
    closed_floor_gap_m = .008
    travel = (bottom_at_open - deck_z - closed_floor_gap_m) / (rail @ up)

    rail_ends = [float(lo[guides, 0].max()), float(hi[guides, 0].min())]
    open_blocks = [float(lo[blocks, 0].min()), float(hi[blocks, 0].max())]
    closed_blocks = [x - travel for x in open_blocks]
    assert rail_ends[0] + .010 < closed_blocks[0] and open_blocks[1] < rail_ends[1] - .010
    # CAD steel plate and its two wheel bogies. Surrounding stand rails stay fixed.
    XYZ = red @ np.column_stack(([1., 0., 0.], across, up))
    corners = XYZ[faces]
    low_tri, high_tri = corners.min(axis=1), corners.max(axis=1)
    tray_plate = ((low_tri >= [-.228, -.271, .0077]) & (high_tri <= [.899, .331, .033])).all(axis=1)
    tray_wheels = ((low_tri[:, 0] >= .812) & (high_tri[:, 0] <= .901) &
                   (low_tri[:, 2] >= -.178) & (high_tri[:, 2] <= .0465) &
                   (((low_tri[:, 1] >= -.313) & (high_tri[:, 1] <= -.259)) |
                    ((low_tri[:, 1] >= .259) & (high_tri[:, 1] <= .372))))
    # The bevel on the loading end continues past the 1 m mounting area.
    # Keep its upper skin, underside and recess with the original moving deck.
    loading_end = ((low_tri >= [.7934, -.312, -.004]) &
                   (high_tri <= [.9806, .372, .051])).all(axis=1)
    tray = tray_plate | tray_wheels | loading_end
    assert 14_000 < (loading_end & ~tray_plate & ~tray_wheels).sum() < 16_000
    assert np.all(tray[deck]) and not np.any(tray & moving[faces[:, 0]])
    assert 29_000 < tray.sum() < 35_000
    return moving, tray, {
        'tray_triangles': int(tray.sum()), 'tray_axis_red': [1., 0., 0.], 'tray_extension_m': 1.0,
        'rail_axis_red': rail.tolist(), 'travel_m': travel,
        'rail_interval_m': rail_ends,
        'carriage_open_interval_m': open_blocks, 'carriage_closed_interval_m': closed_blocks,
        'rail_component_count': int(guides.sum()), 'carriage_block_count': int(blocks.sum()),
        'carriage_part_count': int(carriage.sum()),
        'closed_floor_gap_m': closed_floor_gap_m,
        'deck_up': up.tolist(), 'deck_y_axis': across.tolist(),
        'deck_bounds_local_m': [deck_bounds.min(0).tolist(), deck_bounds.max(0).tolist()],
        'station_reference_red_m': [13., -6.70, 0.],
        'deck_area_m2': float(area[deck].sum()),
    }


def prepare_dart(source, output):
    doc, blob = checked_source(source)
    init_blender(output)
    assets = []
    counts = {}
    kinematics = {}
    for team in ('red', 'blue'):
        group = f'{team}-ramp-dart-station'
        gate_node = f'{group}_0_source_3749800_7'
        fixed, gate, tray = [], [], []
        source_triangles = 0
        matches = 0
        for node, matrix in scene_nodes(doc, group):
            parts = list(source_parts(doc, blob, node, matrix))
            is_gate_source = node['name'] == gate_node
            matches += int(is_gate_source)
            if is_gate_source:
                moving, tray_faces, kinematics[team] = dart_gate_components(parts, team)
            first_face = 0
            for p, n, f, material, offset in parts:
                source_triangles += len(f)
                cad_material = f"RM27_M{rmuc_visual_material(node['name'], material):02d}"
                mask = moving[f[:, 0] + offset] if is_gate_source else np.zeros(len(f), dtype=bool)
                tray_mask = tray_faces[first_face:first_face + len(f)] if is_gate_source else np.zeros(len(f), dtype=bool)
                first_face += len(f)
                fixed.append((p, n, f[~(mask | tray_mask)], cad_material))
                gate.append((p, n, f[mask], cad_material))
                tray.append((p, n, f[tray_mask], cad_material))
        if matches != 1:
            raise ValueError(f'Expected one CAD station occurrence for {team}')
        fixed_report = build_mesh(output, f'dart_{team}_fixed', fixed)
        gate_report = build_mesh(output, f'dart_{team}_gate', gate)
        tray_report = build_mesh(output, f'dart_{team}_tray', tray)
        assert fixed_report['triangles'] + gate_report['triangles'] + tray_report['triangles'] == source_triangles
        assert 27_000 < gate_report['triangles'] < 38_000
        assert 2.58 < gate_report['bounds_m'][1][2] < 2.60
        assets.extend((fixed_report, gate_report, tray_report))
        counts[team] = source_triangles
    assert abs(kinematics['red']['travel_m'] - kinematics['blue']['travel_m']) < .001
    report = {'source_sha256': GLB_SHA256, 'source_product': 3749800,
        'source_pose': 'full_open', 'source_triangles': counts, 'assets': assets, 'guide_kinematics': kinematics}
    (output / 'dart-source.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('RMUC_DART_PREPARED', json.dumps(report), flush=True)


def prepare_visibility(source, output):
    if digest(source) != COLLISION_GLB_SHA256:
        raise ValueError('Visibility split requires the pinned RMUC2027 full-map-collision.glb')
    doc, blob = read_glb(source)
    fixed_rune, fixed_all = [], []
    source_triangles = 0
    rune_triangles = 0
    outpost_triangles = 0
    for node, matrix in scene_nodes(doc, 'centre-platform'):
        parts = list(source_parts(doc, blob, node, matrix))
        is_gantry = node['name'] == 'centre-platform_0_source_1354629_1'
        is_outpost = 'source_2603236_' in node['name']
        rune = moving_vertices(parts) if is_gantry else None
        if is_outpost:
            sign = 1 if matrix[0, 3] > 0 else -1
            pivot = OUTPOST_PIVOT_M * [sign, sign, 1]
            discrete, support = outpost_rotor(parts, pivot, matrix[:3, 1])
        for p, n, f, _, offset in parts:
            source_triangles += len(f)
            rune_fixed = f[~rune[f[:, 0] + offset]] if is_gantry else f
            rune_triangles += len(f) - len(rune_fixed)
            both_fixed = rune_fixed
            if is_outpost:
                ids = rune_fixed + offset
                both_fixed = rune_fixed[~(discrete[ids[:, 0]] | support[ids].all(axis=1))]
                outpost_triangles += len(rune_fixed) - len(both_fixed)
            fixed_rune.append((p, n, rune_fixed, 'RM27_M01'))
            fixed_all.append((p, n, both_fixed, 'RM27_M01'))

    assert rune_triangles == 381_346 and outpost_triangles == 63_098
    init_blender(output)
    rune_report = build_mesh(output, 'centre_rune_visibility', fixed_rune)
    all_report = build_mesh(output, 'centre_rune_outposts_visibility', fixed_all)
    assert rune_report['triangles'] + rune_triangles == source_triangles
    assert all_report['triangles'] + rune_triangles + outpost_triangles == source_triangles
    report = {'collision_source_sha256': COLLISION_GLB_SHA256, 'source_triangles': source_triangles,
        'rune_removed_triangles': rune_triangles, 'outposts_removed_triangles': outpost_triangles,
        'assets': [rune_report, all_report]}
    (output / 'visibility-source.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('RMUC_CENTRE_VISIBILITY_PREPARED', json.dumps(report), flush=True)


def base_shell_indices(parts, specifications, origin):
    points, components, count = welded_components(parts)
    minima = np.full((count, 3), np.inf)
    maxima = np.full((count, 3), -np.inf)
    np.minimum.at(minima, components, points)
    np.maximum.at(maxima, components, points)
    # Each of the three connected CAD shells spans two side faces.
    ids = np.flatnonzero(np.isclose(minima[:, 2], .481392, atol=.000010)
                         & np.isclose(maxima[:, 2], 1.083122, atol=.000010))
    assert len(ids) == 3
    centres = np.array([points[components == i].mean(axis=0) for i in ids])
    off = centres - origin
    directions = np.array([p['slide_axis_ros'][:2] for p in specifications])
    order = (off[:, :2] @ directions.T).argmax(axis=0)
    assert len(set(order)) == 3
    labels = np.zeros(count, dtype=np.int8)
    labels[ids[order]] = [p['index'] for p in specifications]
    # Each corner carries two tapered rows of five detached CAD fasteners.
    present = np.isfinite(minima[:, 0])
    centres = np.zeros_like(minima)
    centres[present] = (minima[present] + maxima[present]) / 2.
    relative = centres[:, :2] - origin[:2]
    z = centres[:, 2]
    tiny = present & ((maxima - minima).max(axis=1) < .012) & (z > .62) & (z < 1.02)
    for spec, axis in zip(specifications, directions):
        radial = axis / np.linalg.norm(axis)
        transverse = np.array([-radial[1], radial[0]])
        screws = tiny & (np.abs(relative @ transverse) < .017) & (
            np.abs(relative @ radial + .335 * z - .603) < .010)
        assert np.count_nonzero(screws) == 10 and np.all(labels[screws] == 0)
        labels[screws] = spec['index']
    return labels[components]


def base_dart_head(parts, origin):
    points, components, count = welded_components(parts)
    lo = np.full((count, 3), np.inf)
    hi = np.full((count, 3), -np.inf)
    np.minimum.at(lo, components, points)
    np.maximum.at(hi, components, points)
    # Whole solids of the central carriage above the stationary pedestal.
    # Its lower housing starts above the six solids forming the rail support.
    horizontal_extent = np.maximum(np.abs(lo[:, :2] - origin[:2]), np.abs(hi[:, :2] - origin[:2]))
    centred = (horizontal_extent < [.106, .120]).all(axis=1)
    extent = hi - lo
    housing = ((horizontal_extent < [.125, .120]).all(axis=1) & (lo[:, 2] >= 1.170)
               & (hi[:, 2] < 1.382) & (extent[:, 0] > .025) & (extent[:, 1] > .01))
    fasteners = np.zeros(count, dtype=bool)
    lens = np.zeros(count, dtype=bool)
    for _, _, triangles, material, offset in parts:
        if material in (40, 41):
            fasteners[components[triangles[:, 0] + offset]] = True
        elif material == 22:
            lens[components[triangles[:, 0] + offset]] = True
    assert np.count_nonzero(housing & lens) == 1
    shell_fasteners = centred & (extent[:, 0] < .014) & (lo[:, 2] > 1.193) & (hi[:, 2] < 1.278)
    # The repeated rail screws sit along the guide bed and remain fixed.
    rail_fasteners = ((lo[:, 2] > 1.203) & (hi[:, 2] < 1.212)) | (
        (lo[:, 2] > 1.215) & (hi[:, 2] < 1.224)) | ((lo[:, 2] > 1.242) & (hi[:, 2] < 1.251))
    return (housing | (shell_fasteners & ~rail_fasteners) | (centred & fasteners & (lo[:, 2] > 1.235)))[components]


def armor_box(centre, axes, dimensions, slot):
    # Flat faces with separate normals preserve the hard edges seen in Figure 4-12.
    axes = np.asarray(axes)
    half = np.asarray(dimensions) * .5
    points, normals, faces = [], [], []
    for i in range(3):
        j, k = (i + 1) % 3, (i + 2) % 3
        for sign in (-1, 1):
            corners = np.array([centre + sign * half[i] * axes[i]
                                + u * half[j] * axes[j] + v * half[k] * axes[k]
                                for u, v in ((-1, -1), (1, -1), (1, 1), (-1, 1))])
            n = len(points)
            points.extend(corners)
            normals.extend([sign * axes[i]] * 4)
            faces.extend(((n, n+1, n+2), (n, n+2, n+3)) if sign > 0
                         else ((n, n+2, n+1), (n, n+3, n+2)))
    return np.array(points), np.array(normals), np.array(faces), slot


def base_inner_structure(origin, side, lower):
    # The source contains the closed cover and upper armor. The opened inner frame is
    # reconstructed from Figure 4-12, with face sizes shared with native hit queries.
    team = 1 if side == 'red' else -1
    # Seat the internal column below the deck and into the underside of the CAD crown.
    parts = [armor_box(origin + [0., 0., .555], np.eye(3), [.17, .17, .66], 'RM27_M01')]
    incline = np.deg2rad(lower['tilt_up_deg'])
    w, h = lower['face_width_m'], lower['face_height_m']
    for i in range(3):
        angle = np.pi + i * 2. * np.pi / 3.
        radial = np.array([team * np.cos(angle), team * np.sin(angle), 0.])
        normal = radial * np.cos(incline) + np.array([0., 0., np.sin(incline)])
        across = np.cross([0., 0., 1.], normal)
        across /= np.linalg.norm(across)
        up = np.cross(normal, across)
        point = origin + lower['center_radius_m'] * radial
        point[2] = lower['center_z_m']
        axes = [normal, across, up]
        parts.append(armor_box(point - .025*normal, axes, [.04, w+.095, h+.10], 'RM27_M09'))
        parts.append(armor_box(point - .006*normal, axes, [.012, w, h],
                               'RM27_M02' if side == 'red' else 'RM27_M25'))
        for edge in (-1., 1.):
            parts.append(armor_box(point - .020*normal + edge*(w*.5+.024)*across,
                                   axes, [.04, .020, h+.10], 'RM27_M01'))
            parts.append(armor_box(point - .021*normal + edge*(h*.5+.022)*up,
                                   axes, [.029, w+.075, .020], 'RM27_M37'))
    return parts


def base_slide_mount(origin, slide_axis, moving):
    outward = np.array(slide_axis) * [1., 1., 0.]
    outward /= np.linalg.norm(outward)
    up = np.array([0., 0., 1.])
    tangent = np.cross(up, outward)
    axes = [outward, tangent, up]

    def piece(radial, height, dimensions, material):
        centre = origin + radial * outward + .12 * tangent
        centre[2] = height
        return armor_box(centre, axes, dimensions, material)

    if moving:
        # The slide bar enters the stationary guide and seats in the shell shoe.
        return [piece(.154, .521, [.478, .065, .042], 'RM27_M37'),
                piece(.371, .531, [.088, .106, .102], 'RM27_M01')]
    # Existing plinth anchors the housing. A flange and upper lip make the load path visible.
    return [piece(.230, .425, [.22, .092, .228], 'RM27_M01'),
            piece(.215, .322, [.246, .131, .024], 'RM27_M37'),
            piece(.222, .524, [.202, .117, .026], 'RM27_M37')]


def prepare_base_armor(source, output, kinematics):
    description = json.loads(kinematics.read_text(encoding='utf-8'))
    collision_source = source.with_name('full-map-collision.glb')
    if digest(collision_source) != COLLISION_GLB_SHA256:
        raise ValueError('Base armor split requires the pinned full-map-collision.glb beside full-map.glb')
    init_blender(output)
    assets = []
    totals = {}
    for variant, (doc, blob) in (('visual', checked_source(source)), ('query', read_glb(collision_source))):
        combined_fixed = []
        source_triangles, moving_triangles, detector_triangles = 0, 0, 0
        matches = set()
        for node, matrix in scene_nodes(doc, 'base'):
            if 'source_4774244_' not in node['name']:
                raise ValueError(f'Unexpected CAD base occurrence {node["name"]}')
            origin = matrix[:3, 3] + TRANSLATION_M
            side = 'red' if origin[0] > 0 else 'blue'
            assert side not in matches and np.allclose(origin, description['origins_m'][side], atol=1e-9)
            matches.add(side)
            specs = sorted((p for p in description['panels'] if p['side'] == side), key=lambda p: p['index'])
            parts = list(source_parts(doc, blob, node, matrix))
            label = base_shell_indices(parts, specs, origin)
            dart = base_dart_head(parts, origin)
            assert not np.any(dart & (label != 0))
            source_triangles += sum(len(f) for _, _, f, _, _ in parts)

            def base_slot(material):
                team_material = 25 if side == 'blue' and material == 2 else material
                return f'RM27_M{team_material:02d}' if variant == 'visual' else 'RM27_M01'

            detector = []
            for p, n, f, material, offset in parts:
                slot = base_slot(material)
                is_dart = dart[f[:, 0] + offset]
                combined_fixed.append((p, n, f[(label[f[:, 0] + offset] == 0) & ~is_dart], slot))
                detector.append((p, n, f[is_dart], slot))
            suffix = '_query' if variant == 'query' else ''
            item = build_mesh(output, f'base_{side}_dart_head{suffix}', detector)
            assert item['triangles'] == (9796 if variant == 'query' else 13425)
            detector_triangles += item['triangles']
            item['query'] = variant == 'query'
            assets.append(item)
            for spec in specs:
                pivot = np.array(spec['pivot_m'])
                mobile = [(p - pivot, n, f[label[f[:, 0] + offset] == spec['index']],
                           base_slot(material))
                          for p, n, f, material, offset in parts]
                source_panel_triangles = sum(len(f) for _, _, f, _ in mobile)
                assert 5000 < source_panel_triangles < 6800
                if variant == 'visual':
                    mobile.extend((p - pivot, n, f, slot) for p, n, f, slot in
                                  base_slide_mount(origin, spec['slide_axis_ros'], moving=True))
                name = spec['visual_mesh'] + ('_query' if variant == 'query' else '')
                item = build_mesh(output, name, mobile)
                item['visual_support_triangles'] = item['triangles'] - source_panel_triangles
                item['query'] = variant == 'query'
                assets.append(item)
                moving_triangles += source_panel_triangles
        assert matches == {'red', 'blue'}
        fixed = build_mesh(output, f'base_fixed_{variant}', combined_fixed)
        assert fixed['triangles'] + moving_triangles + detector_triangles == source_triangles
        fixed['query'] = variant == 'query'
        assets.append(fixed)
        totals[variant] = dict(source=source_triangles, fixed=fixed['triangles'], moving=moving_triangles,
                               dart_heads=detector_triangles)

    inner = [part for side, origin in description['origins_m'].items()
             for part in base_inner_structure(np.array(origin), side, description['lower_armor'])]
    supports = [part for spec in description['panels'] for part in
                base_slide_mount(np.array(description['origins_m'][spec['side']]), spec['slide_axis_ros'], moving=False)]
    inner_report = build_mesh(output, 'base_inner_armor', inner + supports)
    inner_report['visual_support_triangles'] = sum(len(f) for _, _, f, _ in supports)
    assets.append(inner_report)
    report = {'source_sha256': GLB_SHA256, 'query_source_sha256': COLLISION_GLB_SHA256,
              'source_product': 4774244, 'kinematics_sha256': digest(kinematics),
              'triangle_totals': totals, 'assets': assets}
    (output / 'base-armor-source.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('RMUC_BASE_ARMOR_PREPARED', json.dumps(totals), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--kinematics', type=Path)
    device = parser.add_mutually_exclusive_group()
    device.add_argument('--dart-only', action='store_true')
    device.add_argument('--outposts-only', action='store_true')
    device.add_argument('--visibility-only', action='store_true')
    device.add_argument('--base-armor-only', action='store_true')
    arguments = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    if arguments.visibility_only:
        prepare_visibility(arguments.source, arguments.output)
    elif arguments.base_armor_only:
        if arguments.kinematics is None:
            parser.error('--base-armor-only requires --kinematics')
        prepare_base_armor(arguments.source, arguments.output, arguments.kinematics)
    elif arguments.dart_only:
        prepare_dart(arguments.source, arguments.output)
    else:
        prepare(arguments.source, arguments.output, arguments.outposts_only)
