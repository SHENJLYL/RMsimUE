"""Coordinate native navigation actions inside the measured realtime window."""
from pathlib import Path
import argparse
import datetime
import hashlib
import json
import math
import os
import subprocess
import sys
import time

from summarize_navigation_realtime import action_trace_interval, clock_covers_action

ROOT = Path(__file__).resolve().parents[2]


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def terminate_owned(process):
    """Only terminate a still-live Popen child and its Windows descendants."""
    if process.poll() is not None:
        return {'pid': process.pid, 'status': 'already_exited', 'exit_code': process.returncode}
    result = {'pid': process.pid, 'status': 'termination_requested'}
    if os.name == 'nt':
        killed = subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                capture_output=True, timeout=15)
        result.update(taskkill_exit_code=killed.returncode,
                      taskkill_output=(killed.stdout+killed.stderr).decode('utf-8', errors='replace'))
        if killed.returncode and process.poll() is None:
            raise RuntimeError('Could not stop owned child tree: '+str(process.pid))
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    result.update(status='terminated', exit_code=process.returncode)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=90.)
    parser.add_argument('--cpu-policy', choices=('performance', 'system'), default='performance')
    parser.add_argument('--localization-map-yaml', type=Path,
                        help='Optional verified localization map; navigation occupancy map is preserved')
    parser.add_argument('--initial-pose-offset', nargs=3, type=float, default=None,
                        metavar=('DX', 'DY', 'DYAW'),
                        help='Explicit world-frame diagnostic offset from the declared scene spawn')
    parser.add_argument('--capture-localization-clouds', action='store_true',
                        help='Capture bounded raw localization clouds for offline diagnosis; default disabled')
    parser.add_argument('--profile-frames', type=int, default=0,
                        help='Optional CPU attribution capture; default matches the unprofiled user launcher')
    parser.add_argument('--startup-timeout-seconds', type=float, default=90.)
    parser.add_argument('--ready-timeout-seconds', type=float, default=240.)
    parser.add_argument('--action-timeout-seconds', type=float, default=240.)
    parser.add_argument('--shutdown-timeout-seconds', type=float, default=30.)
    parser.add_argument('--keep-session', action='store_true',
                        help='Retain the owned console; action and clock children are still cleaned up')
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or not 60 <= args.seconds <= 600:
        parser.error('Production observation requires 60..600 seconds')
    if args.initial_pose_offset is not None and not all(math.isfinite(value) for value in args.initial_pose_offset):
        parser.error('Initial pose offsets must be finite')
    if args.profile_frames != 0 and not 200 <= args.profile_frames <= 100000:
        parser.error('Profile frames must be zero or 200..100000')
    for name in ('startup_timeout_seconds', 'ready_timeout_seconds',
                 'action_timeout_seconds', 'shutdown_timeout_seconds'):
        value = getattr(args, name)
        if not math.isfinite(value) or not 1 <= value <= 600:
            parser.error(name+' must be finite and within 1..600 seconds')
    if args.localization_map_yaml is not None:
        args.localization_map_yaml = args.localization_map_yaml.resolve()
        if not args.localization_map_yaml.is_file():
            parser.error('Localization map YAML must be an existing file')
    session = args.session.resolve()
    if session.exists():
        parser.error('Use a new session directory; existing evidence is preserved')
    out = session.parent / (session.name + '-orchestration')
    out.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    children, streams, events = {}, [], []

    def launch(name, command):
        stream = (out/(name+'.log')).open('wb')
        streams.append(stream)
        child = subprocess.Popen(command, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT)
        children[name] = child
        events.append(dict(name=name, command=command, pid=child.pid, started_monotonic_s=time.monotonic()))
        print(json.dumps(events[-1]), flush=True)
        return child

    report = {'status': 'failed', 'session_directory': str(session), 'events': events,
              'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'keep_session_requested': args.keep_session,
              'capture_localization_clouds_requested': args.capture_localization_clouds,
              'initial_pose_offset': args.initial_pose_offset,
              'cleanup': [], 'cleanup_errors': []}
    try:
        console_command = ['powershell', '-NoProfile', '-File',
            str(ROOT/'tools/environment/Start-NavigationConsole.ps1'), '-Scene', 'rmuc2027',
            '-NoRviz', '-Offscreen', '-PaceRealTime', '-ControlProfile', 'simulation', '-VerifyLidarQueries',
            '-ProfileSensorTiming', '-ProfileFrames', str(args.profile_frames),
            '-CpuPolicy', args.cpu_policy, '-OutputDirectory', str(session)]
        if args.initial_pose_offset is not None:
            console_command.extend(['-InitialPoseOffsetX', str(args.initial_pose_offset[0]),
                '-InitialPoseOffsetY', str(args.initial_pose_offset[1]),
                '-InitialPoseOffsetYaw', str(args.initial_pose_offset[2])])
        if args.localization_map_yaml is not None:
            console_command.extend(['-LocalizationMapYaml', str(args.localization_map_yaml)])
        console = launch('console', console_command)
        deadline = time.monotonic()+args.startup_timeout_seconds
        while not session.is_dir():
            if console.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError('Console did not create a fresh session')
            time.sleep(.1)
        clock = launch('clock', [sys.executable, str(ROOT/'tools/integration/measure_console_clock.py'),
            '--session', str(session), '--output', str(session/'clock'), '--seconds', str(args.seconds),
            '--ready-timeout-seconds', str(args.ready_timeout_seconds)])
        deadline = time.monotonic()+args.ready_timeout_seconds+5.
        while True:
            state = read_json(session/'clock/measurement-state.json')
            if state.get('phase') == 'measuring':
                report['clock_at_action_launch'] = state
                break
            if console.poll() is not None or clock.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError('Clock did not enter measurement before action')
            time.sleep(.1)
        action_arguments = ['powershell', '-NoProfile', '-File',
            ROOT/'tools/integration/Run-HighSpeedDiagnostics.ps1',
            '-OutputDirectory', session/'actions']
        if args.capture_localization_clouds:
            action_arguments.append('-CaptureLocalizationClouds')
        command = "& " + ps_quote(ROOT/'tools/environment/run-env.ps1') + " -Command @(" + ",".join(
            ps_quote(x) for x in action_arguments) + ")"
        action = launch('actions', ['powershell', '-NoProfile', '-Command', command])
        action_deadline = time.monotonic()+args.action_timeout_seconds
        clock_deadline = float(state['window_end_monotonic_s'])+30.
        while action.poll() is None or clock.poll() is None:
            now = time.monotonic()
            if console.poll() is not None:
                raise RuntimeError('The owned console stopped during acceptance')
            if action.poll() is None and now >= action_deadline:
                raise TimeoutError('Owned action observer exceeded its deadline')
            if clock.poll() is None and now >= clock_deadline:
                raise TimeoutError('Owned clock observer exceeded the measurement tail deadline')
            time.sleep(.25)
        results = {name: read_json(session/name/'verification.json') for name in ('clock', 'actions')}
        report['result_statuses'] = {name: value.get('status', 'missing') for name, value in results.items()}
        interval = action_trace_interval(read_json(session/'actions/trace.json'),
                                        (results['actions'].get('last_navigation') or {}).get('identity'))
        covered = clock_covers_action(results['clock'], interval)
        report.update(action_interval=interval, complete_action_covered_by_clock=covered)
        report['status'] = 'passed' if covered and all(
            child.returncode == 0 for child in (action, clock)) and all(
            value.get('status') == 'passed' for value in results.values()) else 'failed'
    except (Exception, KeyboardInterrupt) as error:
        report['error'] = repr(error)
    finally:
        # These are the exact Popen instances created above. A retained console
        # never retains this coordinator's unfinished observers or action clients.
        for name in ('actions', 'clock'):
            child = children.get(name)
            if child is None:
                continue
            was_running = child.poll() is None
            try:
                report['cleanup'].append({'name': name, **terminate_owned(child)})
                if was_running:
                    report['status'] = 'failed'
            except Exception as error:
                report['cleanup_errors'].append(name+': '+repr(error))
        console = children.get('console')
        if not args.keep_session and console is not None:
            try:
                if console.poll() is None and session.is_dir():
                    state = read_json(session/'status.json')
                    declared = state.get('output_directory')
                    if declared and Path(declared).resolve() != session:
                        raise RuntimeError('Owned session status declares a different output directory')
                    request = session/'stop.request'
                    request.write_text(datetime.datetime.now(datetime.timezone.utc).isoformat()+'\n',
                                       encoding='utf-8')
                    report['stop_request_path'] = str(request)
                    try:
                        console.wait(timeout=args.shutdown_timeout_seconds)
                        report['cleanup'].append({'name': 'console', 'status': 'owned_stop_requested',
                                                  'exit_code': console.returncode})
                    except subprocess.TimeoutExpired:
                        report['cleanup_errors'].append('Owned console exceeded graceful shutdown deadline')
                        report['cleanup'].append({'name': 'console', **terminate_owned(console)})
                else:
                    report['cleanup'].append({'name': 'console', **terminate_owned(console)})
            except Exception as error:
                report['cleanup_errors'].append('console: '+repr(error))
        if not args.keep_session and console is not None and console.poll() not in (None, 0):
            report['cleanup_errors'].append('Owned console exited with code '+str(console.returncode))
        report['kept_session'] = bool(args.keep_session and console is not None and console.poll() is None)
        report['exit_codes'] = {name: child.poll() for name, child in children.items()}
        if report['cleanup_errors']:
            report['status'] = 'failed'
        report['finished_monotonic_s'] = time.monotonic()
        try:
            (out/'verification.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
        finally:
            for stream in streams:
                stream.close()
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report['status'] == 'passed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
