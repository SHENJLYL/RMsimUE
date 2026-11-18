"""Check RMUC arena perimeter and instanced aesthetics in the actual UE scene."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

ROOT=Path(__file__).resolve().parents[2]
def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--editor',type=Path,required=True)
    p.add_argument('--project',type=Path,default=ROOT/'unreal/RMSim/RMSim.uproject')
    p.add_argument('--map-file',type=Path,default=ROOT/'assets/maps/rmuc2027/map.json')
    p.add_argument('--mesh-rig',type=Path,default=ROOT/'assets/vehicles/aim_sentry/ue_mesh_rig.json')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--timeout',type=float,default=120.)
    args=p.parse_args()
    out=args.output.resolve()
    out.mkdir(parents=True,exist_ok=False)
    for name,path in {'UE editor':args.editor,'UE project':args.project,'RMUC map':args.map_file,'robot rig':args.mesh_rig}.items():
        if not path.is_file(): raise FileNotFoundError(name+': '+str(path))
    if args.timeout<=5 or args.timeout>600:raise ValueError('Invalid test timeout')
    manifest=ROOT/'assets/maps/rmuc2027/arena-shell.json'
    art=ROOT/'assets/maps/rmuc2027/arena/kenney-mini-arena/SOURCE.json'
    source=json.loads(manifest.read_text(encoding='utf-8-sig'))
    models=json.loads(art.read_text(encoding='utf-8-sig'))
    asset_folder=args.project.parent/'Content/RMSim/Maps/rmuc2027/Arena/Kenney'
    for name in ('banner','border_straight','column','stairs','wall','wall_gate'):
        if not (asset_folder/(name+'.uasset')).is_file():
            raise FileNotFoundError('Pre-imported CC0 model missing: '+name)
    command=[str(args.editor),str(args.project),'/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode',
             '-game','-RenderOffScreen','-unattended','-NoSplash','-NoSound','-MapOverview',
             '-MapFile='+str(args.map_file),'-MeshRigFile='+str(args.mesh_rig),
             '-ArenaProbeOutput='+str(out/'guard-verification.json'),
             '-CaptureDir='+str(out/'captures'),'-CaptureDelay=5','-ExitAfterCapture','-NoShaderWorker',
             '-abslog='+str(out/'runtime.log')]
    (out/'command.json').write_text(json.dumps(command,indent=2)+'\n',encoding='utf-8')
    start=time.monotonic()
    with (out/'stdout.log').open('wb') as log:
        try:proc=subprocess.run(command,cwd=args.project.parent,stdout=log,stderr=subprocess.STDOUT,timeout=args.timeout)
        except subprocess.TimeoutExpired:proc=None
    p_out=out/'guard-verification.json'
    gate=json.loads(p_out.read_text(encoding='utf-8-sig')) if p_out.is_file() else {}
    logtext=(out/'runtime.log').read_text(errors='replace') if (out/'runtime.log').is_file() else ''
    pictures=sorted(x.name for x in (out/'captures').glob('*.png')) if (out/'captures').is_dir() else []
    checks={'native_ue_process_completed':proc is not None and proc.returncode==0,
            'all_28_continuous_guard_sweeps':gate.get('sweep_passed')==28 and gate.get('sweep_samples')==28,
            'full_four_side_wall_count':gate.get('collision_walls')==4,
            'correct_cc0_instances':gate.get('visual_instances')==216 and gate.get('visual_instanced_groups')==15,
            'cosmetic_no_collision':gate.get('cosmetic_collision_disabled') is True,
            'arena_loaded_from_map_package':'RMSIM_ARENA_SHELL_READY' in logtext,
            'correct_static_parent_mobility':'AttachTo:' not in logtext,
            'real_scene_png_captured':pictures==['aim_camera.png','overview.png']}
    report={'status':'passed' if all(checks.values()) else 'failed','check_count':len(checks),
            'checks':[{'name':k,'passed':v} for k,v in checks.items()],
            'scope':'native UE actor/component/scene queries; original map collision excluded from guard-only sweeps; unchanged in production',
            'elapsed_wall_s':time.monotonic()-start,'process_exit':proc.returncode if proc else None,
            'map_sha256':sha(args.map_file),'arena_manifest_sha256':sha(manifest),
            'cc0_manifest_sha256':sha(art),'module_sha256':sha(args.project.parent/'Binaries/Win64/UnrealEditor-RMSim.dll'),
            'guard_report_sha256':sha(p_out) if p_out.is_file() else None,
            'screenshots':{n:sha(out/'captures'/n) for n in pictures},
            'original_collision_actors_ignored_in_test_only':gate.get('ignored_source_collision_meshes_for_barrier_probe')}
    (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('RMUC_ARENA_NATIVE',json.dumps(report,ensure_ascii=False),flush=True)
    return 0 if report['status']=='passed' else 2

if __name__=='__main__':
    raise SystemExit(main())
