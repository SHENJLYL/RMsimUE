"""Read-only geometric profiles of actual RMUC road meshes for contact solver design."""
import argparse,json,math
from pathlib import Path
import sys
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--preview',required=True)
a=p.parse_args(sys.argv[sys.argv.index('--')+1:]);out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
bpy.ops.wm.open_mainfile(filepath=str(Path(a.preview).resolve()),load_ui=False,use_scripts=False)
trees={}
for obj in bpy.context.scene.objects:
 if obj.type!='MESH' or obj.name not in ('red_road','blue_road','floor'):continue
 v=[obj.matrix_world@x.co for x in obj.data.vertices];faces=[list(f.vertices) for f in obj.data.polygons]
 trees[obj.name]=BVHTree.FromPolygons(v,faces,all_triangles=True)
def height(x,y):
 hits=[]
 for name,t in trees.items():
  point,normal,index,distance=t.ray_cast(Vector((x,y,3)),Vector((0,0,-1)),5)
  if point is not None:hits.append((point.z,name,tuple(normal)))
 return max(hits,key=lambda h:h[0]) if hits else (float('nan'),'missing',(0,0,0))
xs=np.arange(-2,10.61,.02);ys=np.arange(3.2,7.61,.02)
z=np.array([[height(x,y)[0] for x in xs] for y in ys],dtype='<f4')
np.savez_compressed(out/'red-road-heights.npz',x=xs,y=ys,z=z)
profiles=[]
for axis,value in [('x',v) for v in np.arange(3.4,7.41,.2)]+[('y',v) for v in np.arange(-1.8,10.41,.2)]:
 coords=xs if axis=='x' else ys
 points=[height(s,value) if axis=='x' else height(value,s) for s in coords]
 heights=np.array([v[0] for v in points]);slope=np.diff(heights)/.02
 significant=np.where(np.abs(slope)>.05)[0]
 extrema=np.where(np.diff(np.sign(slope))!=0)[0]
 profiles.append(dict(axis=axis,fixed=float(value),max_slope_deg=float(np.degrees(np.arctan(np.max(np.abs(slope))))),
  extrema_count=len(extrema),height_min=float(np.nanmin(heights)),height_max=float(np.nanmax(heights))))
(out/'profile-index.json').write_text(json.dumps(dict(objects=list(trees),profiles=profiles),indent=2)+'\n',encoding='utf-8')
print('GRID',z.shape,'saved',str(out),flush=True)
# Fine profiles through the corrugated section and both wheel tracks of the actual 1x rig.
fine=np.arange(5.6,9.41,.001)
tracks=np.array([6.4-.1373,6.4,6.4+.1373])
profiles=np.array([[height(x,y)[0] for x in fine] for y in tracks])
normals=np.array([[height(x,y)[2] for x in fine] for y in tracks])
radius=.0425;halfbase=.137264
offsets=np.arange(-radius,radius+.0001,.0005)
rolling=[]
for h in profiles:
    centres=np.max(np.array([np.interp(fine+dx,fine,h)+math.sqrt(max(0,radius*radius-dx*dx)) for dx in offsets]),axis=0)
    rolling.append(centres)
rolling=np.array(rolling)
back=np.array([np.interp(fine-halfbase,fine,h) for h in rolling])
front=np.array([np.interp(fine+halfbase,fine,h) for h in rolling])
normal_pitch=np.arctan2(front-back,2*halfbase)
centre=(front+back)/2-radius
floor_clearance=[]
for body_min in (.00998,.03962,.065):
    # Rigid axial underside envelope at each support-derived pose; no suspension/friction claim.
    offsets_body=np.arange(-.251,.252,.002)
    clearance=np.min(np.array([centre[1]+body_min+np.tan(normal_pitch[1])*dx-
        np.interp(fine+dx,fine,profiles[1]) for dx in offsets_body]),axis=0)
    floor_clearance.append(clearance)
np.savez_compressed(out/'wave-profile.npz',x=fine,y=tracks,height=profiles,normals=normals,wheel_center_z=rolling,
    axle_pitch_rad=normal_pitch,chassis_zero_z=centre,clearance_candidates=floor_clearance)
wave=(fine>6.4)&(fine<8.75)
dh=np.diff(profiles[1]);extrema=np.where((dh[:-1]>0)&(dh[1:]<=0))[0]+1
peaks=[float(fine[i]) for i in extrema if 6.4<fine[i]<8.75 and profiles[1,i]>.05]
report=dict(frame='ROS metres, original scene geometry',route_red=dict(entry=[5.8,6.4,height(5.8,6.4)[0]],exit=[9.2,6.4,height(9.2,6.4)[0]],yaw_rad=0),
    route_blue=dict(entry=[-5.8,-6.4,height(-5.8,-6.4)[0]],exit=[-9.2,-6.4,height(-9.2,-6.4)[0]],yaw_rad=math.pi),
    wheel_radius_m=radius,wheelbase_m=halfbase*2,track_m=.2746,peaks_x=peaks,
    peak_spacing_m=np.diff(peaks).tolist(),wave_height_span_m=[float(profiles[1,wave].min()),float(profiles[1,wave].max())],
    maximum_sampled_longitudinal_slope_deg=float(np.degrees(np.arctan(np.abs(dh[wave[:-1]]/.001).max()))),
    maximum_surface_normal_slope_deg=float(np.degrees(np.arccos(np.clip(normals[1,wave,2],-1,1))).max()),
    wheel_envelope_vs_vertical_ray_extra_height_max_m=float((rolling[1,wave]-profiles[1,wave]-radius).max()),
    axle_pitch_range_deg=[float(np.degrees(normal_pitch[1,wave]).min()),float(np.degrees(normal_pitch[1,wave]).max())],
    rigid_underbody_clearance_candidates=[dict(local_bottom_m=float(h),minimum_gap_m=float(np.array(c)[wave].min())) for h,c in zip((.00998,.03962,.065),floor_clearance)],
    limitation='circle support in longitudinal profile is geometric only; no tire compliance or friction conclusion; underside candidates are bounds, not contact meshes')
(out/'wave-report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report),flush=True)
# Inspect actual original-model polygons at physical half scale, retaining source part separation.
package=Path('assets/vehicles/aim_sentry/asset_import.json')
manifest=json.loads(package.read_text(encoding='utf-8'))
body_part=next(part for part in manifest['parts'] if part['name']=='body')
names=body_part['source_objects']
bpy.ops.wm.open_mainfile(filepath='E:/WarThunderModding/war_thunder_sentry/aim_sentry.blend',load_ui=False,use_scripts=False)
parts={}
for name in names:
    obj=bpy.data.objects.get(name)
    if obj is None or obj.type!='MESH':continue
    v=np.array([tuple(obj.matrix_world@p.co) for p in obj.data.vertices])*0.5
    parts[name]=v[v[:,2]<.16]
np.savez_compressed(out/'halfscale-lower-body-vertices.npz',**parts)
body_bounds={k:dict(count=len(v),lo=v.min(axis=0).tolist(),hi=v.max(axis=0).tolist()) for k,v in parts.items() if len(v)}
(out/'body-parts-bounds.json').write_text(json.dumps(body_bounds,indent=2)+'\n',encoding='utf-8')
print('LOWER_PARTS',len(parts),sum(len(v) for v in parts.values()),flush=True)
