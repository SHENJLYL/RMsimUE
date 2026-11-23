"""Apply the authorized exterior-only tree/teaching-wing composition update."""
from pathlib import Path
import collections
import copy
import hashlib
import json
import math
import os
import numpy as np

ROOT = Path('D:/simulator')
LAYOUT = ROOT / 'assets/maps/rmuc2027/arena/sketchupbox-school-gym/layout.json'
BUNDLE = ROOT / 'assets/maps/rmuc2027/arena/sketchupbox-school-gym/ue-import-05-fidelity'
OUT = ROOT / 'artifacts/fixes/school-view-fidelity-20261119/exterior-tune-01'
EXPECTED_LAYOUT_SHA = '6590eaa4eb33e88b65be01f5f57652dd7c65f0569ef75b542373131925b93f5d'

def sha(data):
    return hashlib.sha256(data).hexdigest()

def rotation_ros_to_ue(degrees):
    roll, pitch, yaw = np.radians(degrees) / 2
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    # Exact RosCoordinates::Rotation convention, R_ue=S R_ros S.
    x = -(sr * cp * cy - cr * sp * sy)
    y = cr * sp * cy + sr * cp * sy
    z = -(cr * cp * sy - sr * sp * cy)
    w = cr * cp * cy + sr * sp * sy
    return np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                     [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                     [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])

def instance_bbox(instance):
    center = np.array(instance['center'], dtype=float) * [1, -1, 1]
    half = np.abs(rotation_ros_to_ue(instance['rotation_deg'])) @ (np.array(instance['size']) / 2)
    return center-half, center+half

def disjoint(a, b):
    return bool(np.any(a[1] <= b[0]) or np.any(b[1] <= a[0]))

before = LAYOUT.read_bytes()
if sha(before) != EXPECTED_LAYOUT_SHA:
    raise RuntimeError('Active layout changed before this scoped edit')
if OUT.exists():
    raise FileExistsError('Use a fresh exterior edit evidence directory')
original = json.loads(before)
layout = copy.deepcopy(original)
groups = {g['id']: g for g in layout['groups']}
assert len(layout['native_meshes']) == 23
assert sum(len(g['instances']) for g in layout['groups']) == 53
assert len(groups['RMExteriorTree']['instances']) == 22
assert len(groups['RMExteriorBuildings']['instances']) == 4
prepared = json.loads((BUNDLE/'prepared.json').read_text())
meshes = {m['asset_name']: m for m in prepared['meshes']}
# Native geometry checker proved every exterior vertex at exact source float32.
def native_bounds(mesh_id):
    m = meshes[mesh_id]
    return [np.array(np.float32(np.array(m[key])*100), dtype=float)/100
            for key in ['bounds_min_m', 'bounds_max_m']]
tree_min, tree_max = native_bounds('RMExteriorTree')
tree_size = tree_max-tree_min
tree_origin = (tree_min+tree_max)/2
# Mean XY of source trunk vertices within 1mm of its lowest source point,
# projected onto the model's exact bottom plane; preserved source audit evidence.
root_anchor = np.array([.7821011126041412, .11047977060079574, 0.])

def tree_root(instance):
    scale = np.array(instance['size'])/tree_size
    center = np.array(instance['center'])*[1,-1,1]
    return center + rotation_ros_to_ue(instance['rotation_deg']) @ ((root_anchor-tree_origin)*scale)

school_boxes = [instance_bbox(inst) for g in layout['groups'] if g['id'].startswith('RMSchool')
                for inst in g['instances']]
school_box = [np.min([box[0] for box in school_boxes], axis=0),
              np.max([box[1] for box in school_boxes], axis=0)]
# Read actual ground faces, not its highest AABB point (which is a planter curb).
vertices, faces = [], []
material = None
for line in (BUNDLE/'RMExteriorGround.obj').open('r', encoding='utf-8'):
    parts = line.split()
    if not parts:
        continue
    if parts[0] == 'v':
        vertices.append([float(x) for x in parts[1:4]])
    elif parts[0] == 'usemtl':
        material = parts[1]
    elif parts[0] == 'f':
        ids = [int(x.split('/')[0])-1 for x in parts[1:]]
        if len(ids) != 3:
            raise ValueError('Ground preparation must be triangulated')
        faces.append((ids, material))
vertices = np.array(np.float32(np.array(vertices)*100),dtype=float)/100
ground_min, ground_max = native_bounds('RMExteriorGround')
ground_triangles = []
for instance in groups['RMExteriorGround']['instances']:
    scale = np.array(instance['size'])/(ground_max-ground_min)
    rot = rotation_ros_to_ue(instance['rotation_deg'])
    world = ((vertices-(ground_min+ground_max)/2)*scale) @ rot.T + np.array(instance['center'])*[1,-1,1]
    for ids, mat in faces:
        triangle = world[ids]
        if np.ptp(triangle[:,2]) < 1.e-7:
            a, b, c = triangle[:,:2]
            matrix = np.stack([b-a,c-a],axis=1)
            if abs(np.linalg.det(matrix)) > 1.e-10:
                ground_triangles.append((triangle, mat, np.linalg.inv(matrix)))

def ground_at(point):
    hits = []
    for triangle, mat, inverse in ground_triangles:
        u,v = inverse @ (np.asarray(point[:2])-triangle[0,:2])
        if u >= -1.e-7 and v >= -1.e-7 and u+v <= 1+1.e-7:
            hits.append((float(triangle[0,2]),mat))
    if not hits:
        raise ValueError('Tree foot is outside every real ground surface')
    return max(hits)

changes = []
old_trees = copy.deepcopy(groups['RMExteriorTree']['instances'])
for index, instance in enumerate(groups['RMExteriorTree']['instances']):
    old_root = tree_root(instance)
    instance['size'] = (np.array(instance['size'])*1.5).tolist()
    box = instance_bbox(instance)
    if not disjoint(box, school_box):
        # End-row canopies need a small outward shift to clear the real roof eaves.
        center_ue = np.array(instance['center'])*[1,-1,1]
        half_x = (box[1][0]-box[0][0])/2
        if center_ue[0] < school_box[0][0]:
            instance['center'][0] = float(school_box[0][0]-half_x-.35)
        elif center_ue[0] > school_box[1][0]:
            instance['center'][0] = float(school_box[1][0]+half_x+.35)
        else:
            raise RuntimeError('Unexpected canopy overlap beyond the two end rows')
    foot = tree_root(instance)
    surface_z, surface_mat = ground_at(foot)
    if surface_mat != 'ExtGrass':
        # Keep the existing rooted location if scaling moved the anchor off lawn.
        instance['center'][0] += float(old_root[0]-foot[0])
        instance['center'][1] -= float(old_root[1]-foot[1])
        foot = tree_root(instance)
        surface_z, surface_mat = ground_at(foot)
    if surface_mat != 'ExtGrass':
        raise RuntimeError('Near tree foot did not remain on original lawn')
    instance['center'][2] += float(surface_z-foot[2])
    if not disjoint(instance_bbox(instance), school_box):
        raise RuntimeError('Near tree canopy overlaps the complete source gym AABB')
    changes.append({'kind':'near_tree','index':index,'before':old_trees[index],'after':copy.deepcopy(instance)})

for index, instance in enumerate(groups['RMExteriorBuildings']['instances']):
    prior = copy.deepcopy(instance)
    # Two source wings are already identified by their signed ROS Y positions.
    instance['center'][1] += 18. if instance['center'][1] > 0 else -18.
    changes.append({'kind':'teaching_wing','index':index,'before':prior,'after':copy.deepcopy(instance)})

for ue_y in [-24.,-8.,8.,24.]:
    instance = {'center':[-65.,-ue_y,0.], 'size':(tree_size*1.25).tolist(),'rotation_deg':[0,0,0]}
    foot = tree_root(instance)
    surface_z, surface_mat = ground_at(foot)
    if surface_mat != 'ExtGrass':
        raise RuntimeError('Proposed far tree root is not on real grass')
    instance['center'][2] += float(surface_z-foot[2])
    if not disjoint(instance_bbox(instance),school_box):
        raise RuntimeError('Far tree overlaps source gym')
    groups['RMExteriorTree']['instances'].append(instance)
    changes.append({'kind':'far_tree','index':len(groups['RMExteriorTree']['instances'])-1,'after':copy.deepcopy(instance)})

checks = []
for index, instance in enumerate(groups['RMExteriorTree']['instances']):
    foot = tree_root(instance)
    surface_z, surface_mat = ground_at(foot)
    box = instance_bbox(instance)
    # Positive separation along at least one axis proves the AABBs disjoint.
    separations = np.maximum(school_box[0]-box[1], box[0]-school_box[1])
    assert surface_mat == 'ExtGrass' and abs(foot[2]-surface_z)<1.e-7
    assert disjoint(box,school_box)
    checks.append({'index':index,'kind':'near' if index<22 else 'far',
                   'rotation_ros_deg':instance['rotation_deg'],'root_world_ue_m':foot.tolist(),
                   'ground_surface_material':surface_mat,'ground_surface_z_m':surface_z,
                   'root_ground_error_m':float(foot[2]-surface_z),
                   'tree_bbox_world_ue_m':[box[0].tolist(),box[1].tolist()],
                   'separating_axis_clearance_m':float(np.max(separations)),
                   'source_gym_bbox_disjoint':True})
allowed = {'RMExteriorTree','RMExteriorBuildings'}
for before_group,after_group in zip(original['groups'],layout['groups']):
    if before_group['id'] not in allowed:
        assert before_group == after_group, 'Unrelated group changed'
for key in original:
    if key != 'groups':
        assert original[key] == layout[key], 'Unrelated top-level setting changed'
assert len(layout['native_meshes']) == 23
assert sum(len(g['instances']) for g in layout['groups']) == 57
OUT.mkdir(parents=True)
(OUT/'layout.before.json').write_bytes(before)
after = (json.dumps(layout,indent=2,ensure_ascii=False)+'\n').encode('utf-8')
(OUT/'layout.candidate.json').write_bytes(after)
report = {'status':'prepared','authorization':'root task for exterior-only field visibility improvement',
          'before_sha256':sha(before),'after_sha256':sha(after),
          'native_assets':23,'instances_before':53,'instances_after':57,
          'near_trees':22,'far_trees_added':4,'total_trees':26,'teaching_wings_moved':4,
          'source_gym_bbox_world_ue_m':[school_box[0].tolist(),school_box[1].tolist()],
          'all_26_roots_on_actual_grass':True,'all_tree_bboxes_disjoint_from_source_gym':True,
          'maximum_root_ground_error_m':max(abs(c['root_ground_error_m']) for c in checks),
          'minimum_separating_axis_clearance_m':min(c['separating_axis_clearance_m'] for c in checks),
          'ground_group_unchanged':True,'source_gym_groups_unchanged':True,'lighting_not_modified':True,
          'root_anchor_source_ue_m':root_anchor.tolist(),'root_transform':'Exact native bbox-centred scaled transform with RosCoordinates S R S rotation.',
          'changes':changes,'tree_ground_checks':checks,
          'source_files_sha256':{'prepared.json':sha((BUNDLE/'prepared.json').read_bytes()),
                                 'RMExteriorGround.obj':sha((BUNDLE/'RMExteriorGround.obj').read_bytes())},
          'script_sha256':sha(Path(__file__).read_bytes()),
          'scope':'Geometry/layout checks only; no UE session or rendering performed by this script.'}
(OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
if sha(LAYOUT.read_bytes()) != sha(before):
    raise RuntimeError('Layout changed concurrently; candidate retained without activation')
working = LAYOUT.with_name('layout.exterior-tune-01.tmp')
working.write_bytes(after)
os.replace(working, LAYOUT)
if sha(LAYOUT.read_bytes()) != sha(after):
    raise RuntimeError('Post-write active layout hash differs')
report['status']='applied'
(OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print('EXTERIOR_TUNE_APPLIED',json.dumps({k:report[k] for k in ['status','native_assets','instances_after','total_trees','all_26_roots_on_actual_grass','maximum_root_ground_error_m','minimum_separating_axis_clearance_m','after_sha256']}),flush=True)
