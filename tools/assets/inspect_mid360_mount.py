"""Measure the original Mid-360 optical dome without editing the source model."""
import argparse, hashlib, json, sys
from pathlib import Path
import bpy
import numpy as np
from mathutils import Matrix, Vector

p=argparse.ArgumentParser();p.add_argument('--source',type=Path,default=Path('E:/WarThunderModding/war_thunder_sentry'))
p.add_argument('--output',type=Path,required=True);args=p.parse_args(sys.argv[sys.argv.index('--')+1:])
source=args.source/'aim_sentry.blend';before=hashlib.sha256(source.read_bytes()).hexdigest()
rig=json.loads((args.source/'rig_manifest.json').read_text());scale=1/float(rig['linear_scale'])
bpy.ops.wm.open_mainfile(filepath=str(source),load_ui=False,use_scripts=False)
obj=bpy.data.objects['radar_mid360'];slot=next(i for i,s in enumerate(obj.material_slots) if 'blue_glass' in s.name)
faces=[f for f in obj.data.polygons if f.material_index==slot]
ids=sorted({v for f in faces for v in f.vertices})
points=np.array([list((obj.matrix_world@obj.data.vertices[v].co)*scale) for v in ids])
a=np.column_stack((2*points,np.ones(len(points))));solution=np.linalg.lstsq(a,np.sum(points*points,axis=1),rcond=None)[0]
center=solution[:3];radius=float(np.sqrt(solution[3]+center@center));residual=np.linalg.norm(points-center,axis=1)-radius
# The colored dome contains several boundary rings and seam vertices, not one planar rim.
# Area-weighted source face normals recover the cap axis without that false planar-rim assumption.
normal_matrix=obj.matrix_world.to_3x3().inverted().transposed()
z=np.sum([np.array(normal_matrix@f.normal)*f.area for f in faces],axis=0);z=z/np.linalg.norm(z)
if np.dot(z,points.mean(axis=0)-center)<0:z=-z
# An independent covariance axis checks the surface-normal estimate.
_,_,axes=np.linalg.svd(points-points.mean(axis=0),full_matrices=False)
axis_agreement=float(abs(axes[-1]@z))
bound=points;boundary=ids
x=np.array([1.,0.,0.]);x=x-z*(x@z);x=x/np.linalg.norm(x);y=np.cross(z,x)
rotation=Matrix(np.column_stack((x,y,z)).tolist());rpy=list(rotation.to_euler('XYZ'))
yaw_pivot=np.array(rig['yaw_pivot_m'])*scale
all_points=np.array([list((obj.matrix_world@v.co)*scale) for v in obj.data.vertices])
report={'source_blend_sha256':before,'source_object':obj.name,'source_parent':obj.parent.name,'source_linear_scale':1/scale,
 'optical_center_base_m':center.tolist(),'mount_parent_frame':'yaw_link','origin_in_parent':{'xyz':(center-yaw_pivot).tolist(),'rpy':rpy},
 'optical_axis_base':z.tolist(),'sphere_radius_m':radius,'sphere_max_fit_error_m':float(np.max(np.abs(residual))),
 'axis_covariance_alignment':axis_agreement,'fit_acceptance_tolerance_m':0.0001,
 'optical_faces':len(faces),'optical_vertices':len(ids),'surface_vertices_checked':len(ids),
 'sensor_geometry_bounds_m':{'lo':all_points.min(axis=0).tolist(),'hi':all_points.max(axis=0).tolist()},
 'yaw_pivot_base_m':yaw_pivot.tolist(),'scope':'Geometric optical-dome center and axis. Sensor X chosen by projecting robot +X onto optical-axis plane; not measured factory optical/IMU calibration.'}
print('MID360_FIT_DIAGNOSTIC',json.dumps(report),flush=True)
for i,s in enumerate(obj.material_slots):
 ff=[f for f in obj.data.polygons if f.material_index==i];vv=sorted({v for f in ff for v in f.vertices})
 pp=np.array([list((obj.matrix_world@obj.data.vertices[v].co)*scale) for v in vv])
 if len(pp):print('MATERIAL_REGION',s.name,len(ff),pp.min(axis=0).tolist(),pp.max(axis=0).tolist(),flush=True)
assert obj.parent.name=='bone_turret'
assert abs(radius-.022)<0.0001 and report['sphere_max_fit_error_m']<0.0001
assert axis_agreement>.995
assert hashlib.sha256(source.read_bytes()).hexdigest()==before
args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print('MID360_MOUNT',json.dumps(report),flush=True)
