"""Reintroduce CC0 6-chair modules on 6 genuine stepped school-gallery risers.
Generated risers and chairs are visual-only. The source gym, original competition
field, physical boundary, ROS navigation, vehicle parameters remain unchanged.
"""
from pathlib import Path
import json,hashlib,os
ROOT=Path('D:/simulator')
layout=ROOT/'assets/maps/rmuc2027/arena/sketchupbox-school-gym/layout.json'
old=layout.read_bytes()
d=json.loads(old)
if 'RMSchoolSeats' in d['native_meshes']:raise RuntimeError('Seat extension already exists')
if len(d['native_meshes'])!=19 or len(d['groups'])!=19:raise RuntimeError('Unexpected native layout baseline')
native='/Game/RMSim/Maps/rmuc2027/Arena/Campus/Meshes/SM_SeatRow.SM_SeatRow'
real=ROOT/'unreal/RMSim/Content/RMSim/Maps/rmuc2027/Arena/Campus/Meshes/SM_SeatRow.uasset'
if not real.is_file():raise RuntimeError('CC0 Innerscene source native mesh missing')
legacy=json.loads((ROOT/'assets/maps/rmuc2027/arena/campus-gym/layout.json').read_text())
seats=next(g['instances'] for g in legacy['groups'] if g['mesh']=='seat_row')
assert len(seats)==144
d['native_meshes']['RMSchoolSeats']=native
d['groups'].append({'id':'RMSchoolRealSeats','mesh':'RMSchoolSeats','material':'source','cast_shadow':True,'instances':seats})
steps=[]
z0=-.335
# Continuous concrete/gallery tiers, with regular aisles between pairs of seat modules.
for sign in (-1,1):
 for index in range(6):
  # Original Innerscene CC0 seat-row data rises +0.40m per 0.80m stepped row.
  s=index*0.4+0.19
  h=s-z0
  y=sign*(12.35+index*.8)
  for x in (-15.,-4.9,4.9,15.):
   steps.append({'center':[x,y,(z0+s)*.5],'size':[6.2,.76,h],'rotation_deg':[0,0,0]})
  x=sign*(20.55+index*.8)
  for yy in (-4.7,4.7):
   steps.append({'center':[x,yy,(z0+s)*.5],'size':[.76,6.2,h],'rotation_deg':[0,0,0]})
assert len(steps)==72
d['groups'].append({'id':'RMSchoolConcreteTiers','mesh':'cube','material':'court_line','cast_shadow':True,'instances':steps})
d['seat_attribution']={'author':'Innerscene','license':'CC0-1.0','source_from_existing_local_asset':native,'seat_count':len(seats)*6,
                      'tier_count_per_side':6,'tier_rise_m':.40,'tier_run_m':.80,'visual_only':True}
d['field_clearance_note']='All spectator modules lie outside 28x16m RMUC CAD field; visual stair tiers do not affect physics or navigation.'
dest=ROOT/'artifacts/fixes/downloaded-gym-20261119/school-seating-01'
dest.mkdir(parents=True,exist_ok=False)
(dest/'layout.before.json').write_bytes(old)
(dest/'layout.candidate.json').write_text(json.dumps(d,indent=2)+'\n',encoding='utf-8')
assert layout.read_bytes()==old
layout.write_text(json.dumps(d,indent=2)+'\n',encoding='utf-8')
report={'status':'applied_pending_runtime_qa','before_sha256':hashlib.sha256(old).hexdigest(),
        'after_sha256':hashlib.sha256(layout.read_bytes()).hexdigest(),
        'seat_module_count':len(seats),'individual_chair_count':len(seats)*6,
        'tier_blocks':len(steps),'native_meshes':len(d['native_meshes']),
        'groups':len(d['groups']),'physics_modified':False,'source_modified':False}
(dest/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print('SCHOOL_STEP_STAND_AND_SEATS_PREPARED',json.dumps(report),flush=True)
