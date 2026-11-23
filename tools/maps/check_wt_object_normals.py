"""Offline source/UV checks for the narrowly scoped layered object normal adapter."""
import argparse
import ast
import hashlib
import json
import math
from pathlib import Path
import sys
import numpy as np


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def literal(text, name):
    for node in ast.parse(text).body:
        if isinstance(node, ast.Assign) and any(isinstance(x, ast.Name) and x.id == name for x in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError('missing source literal: ' + name)


def unit(v):
    return np.asarray(v, dtype=float) / np.linalg.norm(v)


def source_frame(local, N, point_dx, point_dy, uv_dx, uv_dy):
    a = np.cross(N, point_dy)
    b = np.cross(point_dx, N)
    T = a * uv_dx[0] + b * uv_dy[0]
    B = a * uv_dx[1] + b * uv_dy[1]
    grad = max(float(T @ T), float(B @ B))
    if grad <= 1e-20:
        return N.copy()
    return local[2] * N + (local[0] * T + local[1] * B) / math.sqrt(grad)


def rnm_world(base, detail, N):
    t = base + N
    u = -detail + N * (2 * float(detail @ N))
    return unit(t * (float(t @ u) / max(float(t @ N), 1e-6)) - u)


def run(root, output):
    sys.path.insert(0, str(root/'tools/unreal'))
    import wt_object_normals as adapter
    code_path = root/'tools/unreal/wt_materials.py'
    legacy = literal(code_path.read_text(encoding='utf8'), 'OBJECT_CODE')
    current = adapter.surface_code(legacy)
    checks, details = {}, {}
    def check(name, value):
        checks[name] = bool(value)
        if not value:
            raise AssertionError(name)
    check('only_layered_normal_statement_replaced',
          current[len(adapter.HELPERS):].replace(adapter.SOURCE_BLEND, adapter.LEGACY_BLEND) == legacy)
    check('no_custom_double_two_sided_sign', 'Parameters.TwoSidedSign' not in adapter.SOURCE_BLEND)
    check('all_three_uv_frames_present', all(x in adapter.SOURCE_BLEND for x in
          ('dp1perp,UV0)', 'dp1perp,UV2*Tiles.xy)', 'dp1perp,UV2*Tiles.zw)')))
    N = np.array([0., 0., 1.])
    d = np.array([.45, .2, math.sqrt(1-.45**2-.2**2)])
    errors, cases = [], []
    for angle in (0, 90, 180):
        t = math.radians(angle)
        R = np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])
        for mirror in (False, True):
            M = np.diag([-1., 1.]) @ R if mirror else R
            actual = rnm_world(N, source_frame(d, N, np.array([-1.,0,0]),
                               np.array([0,-1.,0]), M[:,0], M[:,1]), N)
            # Independently expected inverse UV rotation/reflection on local XY.
            wanted = np.r_[M.T @ d[:2], d[2]]
            error = float(np.max(np.abs(actual-wanted)))
            errors.append(error)
            cases.append(dict(rotation_deg=angle, mirrored_u=mirror,
                              world_normal=actual.tolist(), max_error=error))
    check('analytic_0_90_180_and_mirrored_frames', max(errors) < 2e-14)
    details['analytic_uv_cases'] = cases
    base = unit([.15, -.25, 1])
    detail = unit([.35, .12, 1])
    direct = rnm_world(base, detail, N)
    # Standard source tangent-space RNM is an independent equivalent at N=(0,0,1).
    t = base + np.array([0.,0.,1.])
    u = detail * np.array([-1.,-1.,1.])
    wanted = unit(t * (float(t @ u)/t[2]) - u)
    check('world_rnm_matches_source_tangent_reference', np.max(abs(direct-wanted)) < 2e-14)
    angle = math.radians(31)
    R = np.array([[math.cos(angle),0,math.sin(angle)],[0,1,0],
                  [-math.sin(angle),0,math.cos(angle)]])
    check('rnm_rotation_covariant',
          np.max(abs(rnm_world(R@base,R@detail,R@N)-R@direct)) < 2e-14)
    matrices = [np.eye(3), R.T, np.diag([1.,-1.,1.])@R.T]
    roundtrips = []
    for frame in matrices:
        world = unit([.3,.4,.8])
        tangent = frame @ world
        for side in (-1, 1):
            final = (tangent @ frame) * side
            roundtrips.append(float(np.max(abs(final-world*side))))
    check('ue_tangent_roundtrip_and_one_backface_flip', max(roundtrips) < 2e-14)
    details['ue_roundtrip_max_error'] = max(roundtrips)
    dz = source_frame(d,N,np.array([-1.,0,0]),np.array([0,-1.,0]),np.zeros(2),np.zeros(2))
    check('degenerate_uv_finite_geometric_fallback', np.array_equal(dz,N) and np.isfinite(dz).all())
    a = source_frame(d,N,np.array([-1.,0,0]),np.array([0,-1.,0]),np.array([2.,0]),np.array([0,.5]))
    b = source_frame(d,N,np.array([-100.,0,0]),np.array([0,-100.,0]),np.array([2.,0]),np.array([0,.5]))
    check('metres_to_centimetres_frame_invariant', np.max(abs(a-b)) < 2e-14)
    source_root = root/'artifacts/maps/wt_existing/avg_vlaanderen/models-all-07'
    source_records = []
    for name in ('normandy_village_house_2_floor_c','american_train_depot','af_factory_small_building_e'):
        p = source_root/name
        metadata = json.loads((p/'model.json').read_text(encoding='utf8'))
        data = np.load(p/'mesh.npz')
        selected = np.isin(data['material_ids'], [i for i,x in enumerate(metadata['materials'])
                                                if x['shader'] == 'rendinst_layered'])
        f = data['triangles'][selected]
        v = data['vertices'].astype(float)
        e1,e2 = v[f[:,1]]-v[f[:,0]],v[f[:,2]]-v[f[:,0]]
        tangents,dets = [],[]
        for key in ('uv0','uv2'):
            uv = data[key][:,:2].astype(float).copy()
            uv[:,1] = 1-uv[:,1]  # decoder NPZ is Blender-bottom-left; native UE is source-top-left
            a,b = uv[f[:,1]]-uv[f[:,0]],uv[f[:,2]]-uv[f[:,0]]
            det = a[:,0]*b[:,1]-a[:,1]*b[:,0]
            tangent = (e1*b[:,1,None]-e2*a[:,1,None])/np.where(abs(det)>1e-12,det,1)[:,None]
            tangent /= np.maximum(np.linalg.norm(tangent,axis=1)[:,None],1e-12)
            tangents.append(tangent);dets.append(det)
        valid = (abs(dets[0])>1e-10)&(abs(dets[1])>1e-10)&(np.linalg.norm(np.cross(e1,e2),axis=1)>1e-10)
        angles = np.degrees(np.arccos(np.clip(np.sum(tangents[0]*tangents[1],axis=1),-1,1)))
        source_records.append(dict(model=name,npz_sha256=sha(p/'mesh.npz'),
            metadata_sha256=sha(p/'model.json'),source_layered_triangles=len(f),valid_uv_triangles=int(valid.sum()),
            u_direction_difference_gt30_deg=int(((angles>30)&valid).sum()),
            mirrored_uv0_vs_uv2=int(((dets[0]*dets[1]<0)&valid).sum()),
            direction_angle_percentiles=np.percentile(angles[valid],[0,50,95,100]).tolist()))
    check('actual_train_depot_all_layered_uvs_rotated_and_mirrored',
          source_records[1]['u_direction_difference_gt30_deg']==140 and source_records[1]['mirrored_uv0_vs_uv2']==140)
    check('actual_normandy_and_factory_have_rotated_detail_uvs',
          source_records[0]['u_direction_difference_gt30_deg']==3567 and source_records[2]['u_direction_difference_gt30_deg']==814)
    data = dict(schema='wt_layered_object_normal_offline_v1',status='passed',native_validation='pending',
        source_contract=adapter.contract(legacy),checks=checks,check_count=len(checks),details=details,
        original_uv_witnesses=source_records,
        scripts_sha256={x.name:sha(x) for x in [Path(__file__),code_path,root/'tools/unreal/wt_object_normals.py']})
    output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists():
        raise ValueError('use a fresh evidence output')
    output.write_text(json.dumps(data,indent=2,allow_nan=False)+'\n',encoding='utf8')
    print(json.dumps(dict(status='passed',checks=len(checks),output=str(output),sha256=sha(output))))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    run(args.root.resolve(),args.output.resolve())
