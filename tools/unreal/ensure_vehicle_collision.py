"""UE commandlet: validate/rebuild missing or stale contact geometry from local meshes.

Uses the same source-only workflow on Windows and Linux. No downloads.
Arguments: -CollisionRig=<rig.json> -CollisionEvidence=<fresh directory>
"""
import hashlib
import json
from pathlib import Path
import sys
import unreal

sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'assets'))
import prepare_vehicle_collision as prepare
import bind_vehicle_collision as bind
from wheel_contact_geometry import geometry_hash

def main():
    command=unreal.SystemLibrary.get_command_line()
    rig_path=Path(prepare.argument(command,'CollisionRig')).resolve(strict=True)
    evidence=Path(prepare.argument(command,'CollisionEvidence')).resolve()
    project=Path(unreal.Paths.project_dir()).resolve();content=project/'Content'
    rig=json.loads(rig_path.read_text(encoding='utf-8-sig'));missing=[]
    wheel_geometry={w['part']:geometry_hash(w.get('collision_vertices_m')) for w in rig.get('wheels',[])}
    for part in rig['parts']:
        asset=part.get('collision_asset','')
        candidate=(content/(asset.split('.')[0].removeprefix('/Game/')+'.uasset')).resolve()
        valid=asset.startswith('/Game/RMSim/Collision/') and candidate.is_relative_to(content) and candidate.is_file()
        valid=valid and part.get('collision_source_fbx_sha256')==part['source_fbx_sha256']
        valid=valid and part.get('collision_geometry_sha256','')==wheel_geometry.get(part['name'],'')
        if valid:
            with candidate.open('rb') as f:valid=hashlib.file_digest(f,'sha256').hexdigest()==part.get('collision_asset_sha256')
        if not valid:missing.append(part['name'])
    if not missing:
        unreal.log('CONTACT_COLLISION_CACHE_VALID all parts');return
    evidence.mkdir(parents=True,exist_ok=False)
    for name in missing:
        report=evidence/(name+'.json');count,precision=(128,1000000) if name=='body' else (16,100000)
        prepare.main(f'-CollisionRig="{rig_path}" -CollisionOutput="{report}" -CollisionPart={name} '
                     f'-CollisionHullCount={count} -CollisionHullPrecision={precision}')
        bind.main(['--rig',str(rig_path),'--project',str(project),'--report',str(report),
                   '--evidence',str(evidence/('bind-'+name))])
    unreal.log('CONTACT_COLLISION_READY rebuilt='+','.join(missing))

if __name__=='__main__':main()
