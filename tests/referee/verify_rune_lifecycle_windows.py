from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time


REPO = Path(__file__).resolve().parents[2]
ENGINE = Path(os.environ.get('UE_ROOT', 'E:/simulator-tools/UE_5.8'))
EDITOR = ENGINE / 'Engine/Binaries/Win64/UnrealEditor.exe'
CLIENT = REPO / 'tools/integration/rmuc-rune-client'
STAMP = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
OUT = REPO / 'artifacts/unreal' / f'rmuc-rune-lifecycle-{STAMP}'
OUT.mkdir(parents=True, exist_ok=False)
LOG = OUT / 'ue.log'
ROSTER = OUT / 'match_roster.json'
MAP = REPO / 'assets/maps/rmuc2027/map.json'
VEHICLE = REPO / 'assets/vehicles/aim_sentry'
ENV = dict(os.environ, RMSIM_RUNE_BRIDGE_TOKEN=secrets.token_urlsafe(24),
           RMSIM_RUNE_OPERATOR_103_PASSWORD=secrets.token_urlsafe(24))


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def wait_log(ue, marker, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if LOG.exists():
            lines = [line for line in LOG.read_text(encoding='utf-8', errors='replace').splitlines()
                     if marker in line or 'RMUC_RUNE_PROOF_FINISHED' in line or 'IMPORT_LAB_FAILED' in line]
            if lines:
                line = lines[-1]
                if marker in line:
                    print(line, flush=True)
                    return line
                if 'passed=0' in line or 'IMPORT_LAB_FAILED' in line:
                    raise RuntimeError(line)
        if ue.poll() is not None:
            raise RuntimeError(f'UE exited {ue.returncode} waiting for {marker}')
        time.sleep(.20)
    raise TimeoutError(marker)


def client(mqtt, status, activate=False):
    args = ['node', 'client.mjs', '--id', '103', '--port', str(mqtt), '--expect', str(status)]
    if activate:
        args.append('--activate')
    result = subprocess.run(args, cwd=CLIENT, env=ENV, capture_output=True, text=True, timeout=12,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    with (OUT / 'mqtt-client.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(result.stdout)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    assert rows[-1]['rune_status'] == status, rows
    return rows[-1]


def run():
    pids = subprocess.check_output(['tasklist.exe', '/FI', 'IMAGENAME eq UnrealEditor.exe', '/FO', 'CSV', '/NH'])
    if b'UnrealEditor.exe' in pids:
        raise RuntimeError('An existing UE editor is active')
    bridge, mqtt = port(), port()
    save_json(ROSTER, {'map_id': 'rmuc2027', 'map_path': str(MAP), 'primary_team': 'blue',
                       'primary_index': 0, 'primary_control': 'manual', 'ros_team': '',
                       'red': [{'role': 'S7', 'vehicle_id': 'aim_sentry', 'control': 'manual'}],
                       'blue': [{'role': 'S7', 'vehicle_id': 'aim_sentry', 'control': 'manual'}]})
    args = [str(EDITOR), str(REPO / 'unreal/RMSim/RMSim.uproject'), '/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode',
            '-game', '-windowed', '-ResX=1600', '-ResY=900', '-nosplash', '-NoSound', '-d3d12', '-sm6',
            f'-MapFile={MAP}', f'-MatchRoster={ROSTER}', '-SpawnId=default', f'-MeshRigFile={VEHICLE / "ue_mesh_rig.json"}',
            '-ContactPhysics', '-PhysicsBackend=mujoco', f'-MujocoModel={VEHICLE / "mujoco/rmuc2027/manifest.json"}',
            '-SimStepSeconds=0.005', '-SimRealTimePace', '-RMSimViewportHz=120', '-CustomConfig=RMSimGymLumen',
            '-ExecCmds=r.ScreenPercentage 75', f'-RuneClientBridgePort={bridge}',
            f'-RefereeRuneLifecycleProbe={OUT}', '-RefereeFieldDeviceSelfTest',
            f'-InputEvidence={OUT / "input.json"}', f'-abslog={LOG}']
    dll = REPO / 'unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll'
    receipt = {'status': 'running', 'module_sha256': hashlib.sha256(dll.read_bytes()).hexdigest(), 'rounds': {}}
    ue = mqtt_server = None
    setup = subprocess.STARTUPINFO()
    setup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    setup.wShowWindow = 1  # SW_SHOWNORMAL
    try:
        with (OUT / 'mqtt-gateway.log').open('w', encoding='utf-8') as server_log:
            mqtt_server = subprocess.Popen(['node', 'sidecar.mjs', '--ue-port', str(bridge), '--mqtt-port', str(mqtt),
                                            '--operator', '103'], cwd=CLIENT, env=ENV, creationflags=subprocess.CREATE_NO_WINDOW,
                                           stdout=server_log, stderr=subprocess.STDOUT)
            ue = subprocess.Popen(args, cwd=REPO, env=ENV, startupinfo=setup, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            receipt['ue_pid'] = ue.pid
            save_json(OUT / 'receipt.json', receipt)
            print(OUT, flush=True)
            for mode, hits in [('small', 5), ('large', 10)]:
                wait_log(ue, f'RMUC_RUNE_PROOF_WAITING mode={mode}', 130 if mode == 'small' else 25)
                current = {'inactive': client(mqtt, 1), 'activating': client(mqtt, 2, activate=True)}
                current['settled'] = wait_log(ue, f'RMUC_RUNE_PROOF_SETTLED mode={mode}', 18)
                current['activated'] = client(mqtt, 3)
                assert current['activated']['activated_arms'] == hits, current['activated']
                current['expired'] = wait_log(ue, f'RMUC_RUNE_PROOF_EXPIRED mode={mode}', 80)
                current['returned_to_inactive'] = client(mqtt, 1)
                assert current['returned_to_inactive']['activated_arms'] == 0
                receipt['rounds'][mode] = current
                save_json(OUT / 'receipt.json', receipt)
            wait_log(ue, 'RMUC_RUNE_PROOF_FINISHED mode=large passed=1', 10)
            assert ue.wait(timeout=12) == 0
            log = LOG.read_text(encoding='utf-8', errors='replace')
            assert 'RMUC_RUNE_LIGHT_TRACKS sections=120' in log
            assert all(x not in log for x in ('Ensure condition failed', 'Fatal error:', 'Assertion failed:'))
            for mode in ('small', 'large'):
                for phase in ('targets', 'progress', 'activated', 'expired'):
                    assert (OUT / f'{mode}-{phase}.png').stat().st_size > 10_000
            receipt['status'] = 'passed'
    except BaseException as error:
        receipt.update(status='failed', error=repr(error))
        raise
    finally:
        for process in (ue, mqtt_server):
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=12)
        save_json(OUT / 'receipt.json', receipt)


if __name__ == '__main__':
    run()
