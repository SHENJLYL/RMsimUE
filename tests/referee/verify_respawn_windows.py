"""Run the paired native RMUC respawn scenario with both operator sides."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / 'unreal/RMSim'
MAP = ROOT / 'assets/maps/rmuc2027/map.json'
VEHICLE = ROOT / 'assets/vehicles/aim_sentry'
UE = Path(os.environ.get('UE_ROOT', 'E:/simulator-tools/UE_5.8')) / 'Engine/Binaries/Win64/UnrealEditor.exe'
SCENARIOS = ('red', 'blue')
MARKERS = {
    'physical_supply_fixture', 'death_resets_and_requirement', 'supply_natural_spawn',
    'supply_card_keeps_minimum_shield', 'field_natural_spawn_and_shooter_lock', 'supply_shield_expired',
    'field_shield_expired_weak_remains', 'late_base_card_clears_weak', 'base_damage_fixture',
    'exact_4000_base_hp', 'below_4000_base_hp',
    'paid_spawn', 'paid_protection_expired', 'paid_power_expired_and_next_requirement',
}


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def run(artifact_dir, operator):
    directory = artifact_dir / operator
    directory.mkdir()
    level = json.loads(MAP.read_text(encoding='utf-8-sig'))
    spawns = {'red': 'red', 'blue': 'default'}
    roster = {'map_id': 'rmuc2027', 'primary_team': operator, 'primary_index': 0}
    supply = 'blue' if operator == 'red' else 'red'
    for side in SCENARIOS:
        roster[side] = [dict(armor_number=7, vehicle_id='aim_sentry', control='manual', spawn_id=spawns[side])]
    entry = next(item for item in level['spawns'] if item['id'] == spawns[supply])
    sign = 1 if supply == 'red' else -1
    entry['position_m'] = [sign * 12.0, sign * 4.72, -0.1576]
    entry['yaw_rad'] = math.pi if supply == 'red' else 0.0
    save(directory / 'map.json', level)
    save(directory / 'roster.json', roster)

    log = directory / 'ue.log'
    arguments = [str(UE), str(PROJECT / 'RMSim.uproject'), '/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode',
                 '-game', '-nullrhi', '-nosound', '-nosplash', '-unattended', '-ContactPhysics', '-PhysicsBackend=mujoco',
                 '-SimStepSeconds=0.005', '-SimRealTimePace', f'-MeshRigFile={VEHICLE / "ue_mesh_rig.json"}',
                 f'-MujocoModel={VEHICLE / "mujoco/rmuc2027/manifest.json"}', f'-AbsLog={log}',
                 f'-MapFile={directory / "map.json"}', f'-MatchRoster={directory / "roster.json"}',
                 f'-SpawnId={spawns[operator]}', '-RefereeRespawnParityProbe']
    result = {'arguments': arguments, 'status': 'running', 'operator': operator,
              'module_sha256': hashlib.sha256((PROJECT / 'Binaries/Win64/UnrealEditor-RMSim.dll').read_bytes()).hexdigest()}
    try:
        completed = subprocess.run(arguments, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW, timeout=200)
        result['exit_code'] = completed.returncode
        text = log.read_text(encoding='utf-8', errors='replace')
        lines = [line[line.index('RMUC_RESPAWN_'):] for line in text.splitlines() if 'RMUC_RESPAWN_' in line]
        result['measurements'] = lines
        observed = {line.split()[1].split('=')[1] for line in lines
                    if line.startswith('RMUC_RESPAWN_CHECK ') and 'passed=1' in line}
        assert observed == MARKERS, (MARKERS - observed, lines)
        own_id = 7 if operator == 'red' else 107
        assert any(line.startswith(f'RMUC_RESPAWN_FINISHED passed=1 id={own_id} ') for line in lines), lines
        assert completed.returncode == 0
        result['status'] = 'passed'
        return result
    except BaseException as error:
        result.update(status='failed', error=repr(error))
        raise
    finally:
        save(directory / 'verification.json', result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--team', choices=(*SCENARIOS, 'both'), default='both')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/verification/rmuc-rule-parity-20261127/respawn')
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    artifact_dir = (args.output / stamp).resolve()
    artifact_dir.mkdir(parents=True)
    print(artifact_dir, flush=True)
    cases = SCENARIOS if args.team == 'both' else (args.team,)
    summary = {}
    for team in cases:
        try:
            summary[team] = run(artifact_dir, team)
        except (AssertionError, OSError, subprocess.SubprocessError) as error:
            summary[team] = {'status': 'failed', 'error': repr(error)}
        print(f'{team} {summary[team]["status"]}', flush=True)
        save(artifact_dir / 'verification.json', summary)
    return 0 if all(result['status'] == 'passed' for result in summary.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
