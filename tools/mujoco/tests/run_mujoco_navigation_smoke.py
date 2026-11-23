"""Exercise the public MuJoCo navigation entry inside the unchanged 60 s clock gate."""
from __future__ import annotations
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import os
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'tools/integration'))
from run_navigation_realtime_acceptance import read_json, ps_quote, terminate_owned
from summarize_navigation_realtime import action_trace_interval, clock_covers_action
from measure_console_clock import read_observation, ready as clock_observation_ready


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--engine-root', type=Path, required=True)
    parser.add_argument('--domain', type=int, default=174)
    parser.add_argument('--port', type=int, default=9474)
    parser.add_argument('--profile-frames', type=int, default=0)
    parser.add_argument('--profile-sensor-timing', action='store_true')
    parser.add_argument('--profile-mujoco-timing', action='store_true')
    args = parser.parse_args()
    if args.profile_frames and not 200 <= args.profile_frames <= 100000:
        parser.error('Profiling frame count must be zero or 200..100000')
    session = args.session.resolve()
    if session.exists():
        parser.error('Use a fresh session directory; existing evidence is preserved')
    out = session.parent/(session.name+'-orchestration')
    out.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    children, streams, events = {}, [], []
    report = dict(schema='rmsim_mujoco_navigation_clock_gate_v1', status='failed', events=events,
                  session_directory=str(session), script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  clock_contract='Unmodified production measure_console_clock.py: 60 s, 0.98..1.02 overall/rolling, 200 k rays/s, 200 Hz IMU, 60 Hz native Draw +/-5%',
                  profiling=dict(frames=args.profile_frames, sensor_timing=args.profile_sensor_timing,mujoco_timing=args.profile_mujoco_timing,
                                 scope='CPU attribution diagnostic' if args.profile_frames or args.profile_sensor_timing or args.profile_mujoco_timing else 'unprofiled production observation'),
                  cleanup=[], cleanup_errors=[])

    def launch(name, command):
        stream = (out/(name+'.log')).open('wb')
        streams.append(stream)
        child = subprocess.Popen(command, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT)
        children[name] = child
        events.append(dict(name=name, command=command, pid=child.pid, started_monotonic_s=time.monotonic()))
        print(json.dumps(events[-1]), flush=True)
        return child

    try:
        console_command = ['powershell.exe', '-NoProfile', '-File',
            str(ROOT/'tools/environment/Start-NavigationConsole.ps1'), '-Scene', 'rmuc2027',
            '-EngineRoot', str(args.engine_root),  # Exercise the standard default backend/model selection.
            '-OutputDirectory', str(session), '-Port', str(args.port), '-Domain', str(args.domain),
            '-SimStep', '0.005', '-PaceRealTime', '-ViewportHz', '60', '-CameraHz', '0',
            '-CpuPolicy', 'performance', '-ControlProfile', 'simulation', '-NoRviz', '-Offscreen']
        if args.profile_frames:
            console_command.extend(['-ProfileFrames', str(args.profile_frames)])
        if args.profile_sensor_timing:
            console_command.append('-ProfileSensorTiming')
        if args.profile_mujoco_timing:
            console_command.append('-ProfileMujocoTiming')
        console = launch('console', console_command)
        deadline = time.monotonic()+90
        while not session.is_dir():
            if console.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError('Public navigation entry did not create its owned fresh session')
            time.sleep(.1)
        clock = launch('clock', [sys.executable, str(ROOT/'tools/integration/measure_console_clock.py'),
            '--session', str(session), '--output', str(session/'clock'), '--seconds', '60',
            '--warmup-seconds', '10', '--ready-timeout-seconds', '240'])
        action_args = ['powershell.exe', '-NoProfile', '-File',
                       ROOT/'tools/mujoco/tests/Run-MujocoNavigationProbe.ps1',
                       '-SessionDirectory', session, '-OutputDirectory', session/'actions', '-Domain', str(args.domain)]
        command = '& '+ps_quote(ROOT/'tools/environment/run-env.ps1')+' -Command @('+','.join(ps_quote(x) for x in action_args)+')'
        action = None
        deadline = time.monotonic()+245
        while True:
            state = read_json(session/'clock/measurement-state.json')
            if (action is None and state.get('phase') == 'waiting_for_warmup'
                    and clock_observation_ready(read_observation(session))):
                report['clock_at_action_observer_launch'] = state
                action = launch('actions', ['powershell.exe', '-NoProfile', '-Command', command])
            if action is not None and action.poll() is not None:
                raise RuntimeError('Action observer exited before the production measurement gate')
            if state.get('phase') == 'measuring':
                if action is None:
                    raise RuntimeError('Action observer missed the existing clock warmup')
                report['clock_at_action_release'] = state
                break
            if console.poll() is not None or clock.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError('Clock observer did not enter its unchanged production measurement')
            time.sleep(.1)
        deadline = time.monotonic()+100
        report['action_deadline_monotonic_s'] = deadline
        while action.poll() is None or clock.poll() is None:
            now = time.monotonic()
            if console.poll() is not None:
                raise RuntimeError('Owned navigation console stopped during acceptance')
            if action.poll() is None and now >= deadline:
                raise TimeoutError('Short goal/cancel observer exceeded its 100 s limit')
            if clock.poll() is None and now >= float(state['window_end_monotonic_s'])+30:
                raise TimeoutError('Clock observer exceeded its bounded measurement tail')
            time.sleep(.25)
        results = {name: read_json(session/name/'verification.json') for name in ('clock', 'actions')}
        report['result_statuses'] = {name: value.get('status', 'missing') for name, value in results.items()}
        interval = action_trace_interval(read_json(session/'actions/trace.json'),
                                        (results['actions'].get('last_navigation') or {}).get('identity'))
        covered = clock_covers_action(results['clock'], interval)
        report.update(action_interval=interval, complete_action_covered_by_clock=covered,
                      measured_realtime_factor=results['clock'].get('measured_realtime_factor'))
        report['status'] = 'passed' if covered and all(c.returncode == 0 for c in (action, clock)) and all(
            value.get('status') == 'passed' for value in results.values()) else 'failed'
    except (Exception, KeyboardInterrupt) as error:
        report['error'] = repr(error)
        print('NAVIGATION_GATE_ERROR '+repr(error), flush=True)
    finally:
        for name in ('actions', 'clock'):
            child = children.get(name)
            if child is None:
                continue
            was_running = child.poll() is None
            try:
                report['cleanup'].append(dict(name=name, **terminate_owned(child)))
                if was_running:
                    report['status'] = 'failed'
            except Exception as error:
                report['cleanup_errors'].append(name+': '+repr(error))
        console = children.get('console')
        if console is not None:
            try:
                if console.poll() is None and session.is_dir():
                    declared = read_json(session/'status.json').get('output_directory')
                    if declared and Path(declared).resolve() != session:
                        raise RuntimeError('Owned session declares a different output directory')
                    request = session/'stop.request'
                    request.write_text(datetime.datetime.now(datetime.timezone.utc).isoformat()+'\n', encoding='utf-8')
                    report['stop_request_path'] = str(request)
                    try:
                        console.wait(timeout=30)
                        report['cleanup'].append(dict(name='console', status='owned_stop_requested', exit_code=console.returncode))
                    except subprocess.TimeoutExpired:
                        report['cleanup_errors'].append('Owned console exceeded graceful shutdown deadline')
                        report['cleanup'].append(dict(name='console', **terminate_owned(console)))
                else:
                    report['cleanup'].append(dict(name='console', **terminate_owned(console)))
            except Exception as error:
                report['cleanup_errors'].append('console: '+repr(error))
        if console is not None and console.poll() not in (None, 0):
            report['cleanup_errors'].append('Owned console exited with code '+str(console.returncode))
        if report['cleanup_errors']:
            report['status'] = 'failed'
        report['exit_codes'] = {name: child.poll() for name, child in children.items()}
        report['finished_monotonic_s'] = time.monotonic()
        (out/'verification.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
        for stream in streams:
            stream.close()
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report['status'] == 'passed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
