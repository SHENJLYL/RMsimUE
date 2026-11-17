"""Offline UE Editor preparation of independent convex contact/query proxies.

Run in a disposable commandlet after the live simulator is closed:
  -run=pythonscript -script=<this file>
  -CollisionRig=<ue_mesh_rig.json> -CollisionOutput=<fresh result.json>
  [-CollisionPart=body] [-CollisionHullCount=16] [-CollisionHullVerts=64]
  [-CollisionHullPrecision=100000] [-CollisionWheelWidthM=0.044]

Never edits the visual mesh assets or rig JSON. Copies belong under
/Game/RMSim/Collision/<rig id>/. Wheels prefer supplied source convex vertices;
legacy packages fall back to an explicitly dimensioned analytic cylinder.
The small C++ SetConvexCollision authoring helper must cook those exact vertices.
All non-wheel parts are processed, including the four independently exported
suspension carriers in the 11-part contact-physics rig.
"""
import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sys

import unreal
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'assets'))
from wheel_contact_geometry import source_vertices, geometry_hash


def argument(command,name,default=None):
    match=re.search(r'-'+re.escape(name)+r'=(?:"([^"]+)"|(\S+))',command)
    return (match.group(1) or match.group(2)) if match else default


def points(vectors):
    return [[float(v.x),float(v.y),float(v.z)] for v in vectors]


def bounds(mesh):
    box=mesh.get_bounding_box()
    return dict(lo=points([box.min])[0],hi=points([box.max])[0])


def source_asset_file(path):
    return Path(unreal.Paths.project_content_dir()).resolve()/(path.split('.')[0][len('/Game/'):]+'.uasset')


def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def wheel_vertices(radius_m,width_m,sides=32):
    # Local wheel shaft is ROS Y / UE -Y. The cylinder is symmetric on that axis.
    return [unreal.Vector(radius_m*100*math.cos(i*math.tau/sides),sign*width_m*50,
                          radius_m*100*math.sin(i*math.tau/sides))
            for sign in (-1,1) for i in range(sides)]


def inspect_hulls(mesh):
    if not hasattr(unreal.MapImportLibrary,'describe_convex_collision'):
        raise RuntimeError('MapImportLibrary.describe_convex_collision is required: FKConvexElem vertex data is protected in editor Python')
    description=json.loads(unreal.MapImportLibrary.describe_convex_collision(mesh))
    hulls=description.get('hulls',[])
    if not hulls or description.get('hull_count')!=len(hulls):
        raise RuntimeError('native collision inspection reports no/invalid convex hulls')
    records=[]
    for hull in hulls:
        vertices=hull['vertices_cm']
        if len(vertices)<4 or any(not math.isfinite(v) for point in vertices for v in point):
            raise ValueError('invalid convex hull vertex data')
        world=vertices
        records.append(dict(vertices=world,vertex_count=len(world),
            bounds_cm=dict(lo=[min(v[i] for v in world) for i in range(3)],hi=[max(v[i] for v in world) for i in range(3)])))
    all_points=[v for h in records for v in h['vertices']]
    union=dict(lo=[min(v[i] for v in all_points) for i in range(3)],hi=[max(v[i] for v in all_points) for i in range(3)])
    return records,union


def main(command=None):
    if command is None:command=unreal.SystemLibrary.get_command_line()
    rig_file=argument(command,'CollisionRig');output_file=argument(command,'CollisionOutput')
    if not rig_file or not output_file:raise ValueError('CollisionRig and fresh CollisionOutput are required')
    rig_path=Path(rig_file).resolve(strict=True);output=Path(output_file).resolve()
    if output.exists() or output==rig_path:raise ValueError('refusing to overwrite input or existing collision evidence')
    rig=json.loads(rig_path.read_text(encoding='utf-8-sig'))
    if rig.get('kind')!='rm_ue_mesh_rig' or not re.fullmatch('[a-z][a-z0-9_]*',rig.get('id','')):
        raise ValueError('unsupported mesh rig')
    count=int(argument(command,'CollisionHullCount','16'));verts=int(argument(command,'CollisionHullVerts','64'))
    precision=int(argument(command,'CollisionHullPrecision','100000'));width=float(argument(command,'CollisionWheelWidthM','0.044'))
    if not (1<=count<=128 and 8<=verts<=128 and 10000<=precision<=10000000 and math.isfinite(width) and 0<width<1):
        raise ValueError('invalid collision preparation parameters')
    selection=argument(command,'CollisionPart')
    selected=[part for part in rig['parts'] if selection is None or part['name']==selection]
    if not selected:raise ValueError('CollisionPart is not present in the rig')
    wheel={w['part']:w for w in rig.get('wheels',[])}
    if any(p['name'] in wheel for p in selected) and not hasattr(unreal.MapImportLibrary,'set_convex_collision'):
        raise RuntimeError('MapImportLibrary.set_convex_collision editor helper is required to cook exact cylinder hulls; use CollisionPart=body to inspect VHACD first')
    # This editor module can be reflected but not initialized in a bare Python commandlet.
    unreal.load_module('StaticMeshEditor')
    editor=unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    if editor is None:
        editor=unreal.StaticMeshEditorSubsystem()
    if editor is None:raise RuntimeError('StaticMeshEditorSubsystem unavailable')
    report=dict(schema='vehicle_convex_collision_preparation_v1',status='running',rig=str(rig_path),rig_sha256=digest(rig_path),
        engine=unreal.SystemLibrary.get_engine_version(),started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        parameters=dict(hull_count=count,max_hull_vertices=verts,hull_precision=precision,legacy_cylinder_width_m=width,legacy_cylinder_sides=32),
        parts=[],runtime_query_verified=False,runtime_contact_verified=False,rig_modified=False,
        limitations=['convex approximation requires ground/obstacle query validation; hull generation alone does not prove mobility',
                     'rigid convex tyres do not model rubber deformation or individual rollers and are not calibrated contact-force models'])
    def record():
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    record()
    try:
        for part in selected:
            name=part['name'];source_path=part['asset']
            if not re.fullmatch('[A-Za-z][A-Za-z0-9_]*',name) or not source_path.startswith('/Game/RMSim/Imported/'):
                raise ValueError('invalid source part identity/path')
            source=unreal.load_asset(source_path)
            if not isinstance(source,unreal.StaticMesh):raise ValueError('missing source StaticMesh '+source_path)
            before_hash=digest(source_asset_file(source_path))
            # A changed source mesh gets a new derived path; never reuse an old-scale
            # collision copy solely because its readable part name is the same.
            geometry=source_vertices(wheel[name].get('collision_vertices_m')) if name in wheel else None
            geometry_sha=geometry_hash(geometry)
            recipe=(f'sourcehull_{geometry_sha[:16]}' if geometry is not None else
                    f'c{count}v{verts}p{precision}' if name not in wheel else f'cylinder32w{width:g}'.replace('.','_'))
            destination=f'/Game/RMSim/Collision/{rig["id"]}/{name}_{before_hash[:12]}_{recipe}'
            mesh=unreal.load_asset(destination)
            if mesh is None:mesh=unreal.EditorAssetLibrary.duplicate_asset(source_path,destination)
            if not isinstance(mesh,unreal.StaticMesh) or mesh==source:raise RuntimeError('cannot create independent collision mesh')
            if name in wheel:
                radius=float(wheel[name]['radius_m'])
                tyre_width=float(wheel[name].get('width_m',width))
                if not math.isfinite(radius) or not 0<radius<1:raise ValueError('invalid measured tyre radius')
                if not math.isfinite(tyre_width) or not 0<tyre_width<1:raise ValueError('invalid measured tyre width')
                axis=wheel[name]['axis']
                if axis not in ([0,1,0],[0,-1,0]):raise ValueError('analytic wheel adapter currently requires local Y axle')
                vertices_ue=([unreal.Vector(p[0]*100,-p[1]*100,p[2]*100) for p in geometry]
                        if geometry is not None else wheel_vertices(radius,tyre_width))
                if geometry is not None and abs(max(math.hypot(p[0],p[2]) for p in geometry)-radius)>radius*.02:
                    raise ValueError('source wheel hull radius differs from declared rolling radius by over 2 percent')
                result=unreal.MapImportLibrary.set_convex_collision(mesh,vertices_ue)
                # UE treats a bool success return with one out parameter as an optional
                # out-value: success returns Error string; failure returns None.
                if not isinstance(result,str) or result:
                    raise RuntimeError('analytic cylinder cook failed: '+repr(result))
                method='source_wheel_convex_hull' if geometry is not None else 'analytic_32_sided_cylinder'
            else:
                if not editor.set_convex_decomposition_collisions(mesh,count,verts,precision):
                    raise RuntimeError('VHACD failed '+name)
                method='source_mesh_convex_decomposition'
            body=mesh.get_editor_property('body_setup')
            body.set_editor_property('collision_trace_flag',unreal.CollisionTraceFlag.CTF_USE_SIMPLE_AND_COMPLEX)
            hulls,union=inspect_hulls(mesh)
            if name in wheel:
                if len(hulls)!=1:raise ValueError('wheel must have exactly one convex hull')
                expected=points(vertices_ue)
                if any(abs(union[edge][i]-fn(p[i] for p in expected))>0.001
                       for edge,fn in [('lo',min),('hi',max)] for i in range(3)):
                    raise ValueError('cooked wheel bounds differ from supplied geometry')
            if not unreal.EditorAssetLibrary.save_loaded_asset(mesh,only_if_is_dirty=False):raise RuntimeError('collision copy save failed')
            if digest(source_asset_file(source_path))!=before_hash:raise RuntimeError('source visual asset changed during collision generation')
            original=bounds(source)
            excess=max([0.0]+[original['lo'][i]-union['lo'][i] for i in range(3)]+[union['hi'][i]-original['hi'][i] for i in range(3)])
            report['parts'].append(dict(name=name,visual_asset=source_path,visual_uasset_sha256=before_hash,
                source_fbx_sha256=part.get('source_fbx_sha256'),
                collision_geometry_sha256=geometry_sha,
                collision_asset=mesh.get_path_name(),collision_uasset_sha256=digest(source_asset_file(mesh.get_path_name())),
                method=method,hull_count=len(hulls),hulls=hulls,union_bounds_cm=union,visual_bounds_cm=original,
                hull_bounds_excess_cm=excess,source_visual_unchanged=True))
            record();unreal.log(f'VEHICLE_CONVEX_PREPARED {name} hulls={len(hulls)}')
        report.update(status='prepared_pending_runtime_query_validation',finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
        unreal.log('VEHICLE_COLLISION_PREPARATION_COMPLETE '+str(output))
    except Exception as error:
        report.update(status='failed',error=str(error));raise
    finally:record()


if __name__=='__main__':main()
