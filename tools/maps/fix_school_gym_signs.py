"""Correct mirrored DJI/RoboMaster brand images in UE's OBJ import UV convention.
Original official artwork stays unchanged. Modifies only generated per-sign copies.
"""
from pathlib import Path
from PIL import Image,ImageOps
import hashlib,json,shutil
root=Path('D:/simulator')
venue=root/'assets/maps/rmuc2027/arena/sketchupbox-school-gym'
asset=venue/'ue-import-03'
source=root/'assets/maps/rmuc2027/arena/downloaded-gym/branding/sign_horizontal_4x1_white.png'
out=root/'artifacts/fixes/downloaded-gym-20261119/school-sign-orientation-01'
if out.exists():raise FileExistsError('Existing audit folder')
out.mkdir(parents=True)
mfile=asset/'prepared.json';before=mfile.read_bytes()
(out/'prepared.before.json').write_bytes(before)
d=json.loads(before)
records={}
for name in ('RMBrandLongNorth','RMBrandLongSouth','RMBrandShortEast','RMBrandShortWest'):
 dest=asset/'textures'/(name+'.png')
 assert dest.is_file()
 shutil.copy2(dest,out/(name+'.before.png'))
 with Image.open(source) as image: corrected=ImageOps.mirror(image.convert('RGBA'))
 corrected.save(dest)
 d['files'][dest.relative_to(asset).as_posix()]={'bytes':dest.stat().st_size,'sha256':hashlib.sha256(dest.read_bytes()).hexdigest()}
 records[name]={'before_sha256':hashlib.sha256((out/(name+'.before.png')).read_bytes()).hexdigest(),
                'after_sha256':hashlib.sha256(dest.read_bytes()).hexdigest()}
mfile.write_text(json.dumps(d,indent=2)+'\n',encoding='utf-8')
report={'status':'prepared_for_UE_reimport','reason':'Source WindowView and DriverView show horizontally mirrored RoboMaster; remedy OBJ UV handedness with generated texture copies only.','textures':records}
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print('SCHOOL_SIGN_ARTWORK_ORIENTATION_PATCH_PREPARED',json.dumps(records),flush=True)
