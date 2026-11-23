"""Own one UE + MuJoCo viewer + ROS + RViz session and its process lifetime."""
from __future__ import annotations
import argparse
import datetime
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import uuid

from navigation_console_config import (
    ROOT, atomic_json, copy_runtime_arena, digest, prepare_scene, rviz_configuration, select_navigation_source,
)
from runtime_identity import capture_pointlio_inputs
from platform_cpu_policy import apply_owned_process_policy, prepare_ue_worker_budget, worker_budget_digest


def terminate_owned(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == 'nt':
        result = subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
        if result.returncode and process.poll() is None:
            raise RuntimeError('Could not stop owned process ' + str(process.pid))
    else:
        process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


class StopRequested(Exception):
    pass


def mujoco_viewer_enabled(args) -> bool:
    return args.mujoco_viewer and args.physics_backend == 'mujoco' and not args.no_rviz and not args.offscreen


def ue_display_configuration(args) -> tuple[dict, list[str]]:
    """Describe requested startup pixels; actual client pixels are observed separately."""
    if args.fullscreen and args.offscreen:
        raise ValueError('--fullscreen cannot be combined with --offscreen')
    width = args.width if args.width is not None else (640 if args.offscreen else 1120)
    height = args.height if args.height is not None else (360 if args.offscreen else 860)
    if any(type(value) is not int or not 64 <= value <= 16384 for value in (width, height)):
        raise ValueError('Display width and height must be integers in 64..16384 pixels')
    mode = 'offscreen' if args.offscreen else ('fullscreen' if args.fullscreen else 'windowed')
    flags = (['-RenderOffScreen', '-unattended'] if args.offscreen else
             ['-fullscreen' if args.fullscreen else '-windowed'])
    flags.extend(['-ResX='+str(width), '-ResY='+str(height)])
    if not args.offscreen and not args.fullscreen:
        flags.extend(['-WinX=20', '-WinY=40'])
    requested = dict(mode=mode, width_pixels=width, height_pixels=height,
        width_explicit=args.width is not None, height_explicit=args.height is not None,
        actual_client_size_verified=False,
        scope='requested UE startup flags; actual client pixels and foreground require separate observation')
    return requested, flags


def mujoco_viewer_environment(python: Path, inherited: dict) -> tuple[dict, dict]:
    """Use the prepared viewer venv without inheriting the ROS DLL/Python paths."""
    venv = python.parent.parent
    configuration = {}
    for line in (venv/'pyvenv.cfg').read_text(encoding='utf-8').splitlines():
        key, separator, value = line.partition('=')
        if separator:
            configuration[key.strip().lower()] = value.strip()
    home = Path(configuration.get('home', ''))
    if not configuration.get('home') or not home.is_dir():
        raise RuntimeError('MuJoCo viewer venv has no valid base Python home')
    if configuration.get('include-system-site-packages', '').lower() != 'false':
        raise RuntimeError('MuJoCo viewer requires its isolated prepared venv')
    blocked = {'PATH', 'VIRTUAL_ENV', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH',
               'LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH', 'MUJOCO_GL', 'MUJOCO_EGL_DEVICE_ID'}
    prefixes = ('PYTHON', 'ROS_', 'RMW_', 'AMENT_', 'COLCON_', 'CONDA_', 'PIXI_', '_CE_')
    environment = {key:value for key,value in inherited.items()
                   if key.upper() not in blocked and not key.upper().startswith(prefixes)}
    system_root = Path(inherited.get('SystemRoot', inherited.get('SYSTEMROOT', 'C:/Windows')))
    search_path = [python.parent, home, home/'DLLs', system_root/'System32', system_root,
                   system_root/'System32/Wbem', system_root/'System32/WindowsPowerShell/v1.0']
    environment.update(PATH=os.pathsep.join(str(item) for item in search_path),
                       VIRTUAL_ENV=str(venv), PYTHONNOUSERSITE='1')
    return environment, {'policy':'isolated_mujoco_venv_and_windows_system_paths',
                         'base_python_home':str(home), 'path':list(map(str, search_path)),
                         'inherits_ros_python_or_dll_paths':False}


def optimized_mujoco_viewer_runtime(source: Path) -> dict:
    """Load the shared receipt validator without changing the ROS import path."""
    helper = ROOT/'tools/mujoco/viewer_runtime.py'
    if not helper.is_file():
        raise RuntimeError('Missing optimized MuJoCo viewer runtime validator: '+str(helper))
    spec = importlib.util.spec_from_file_location('_rmsim_viewer_runtime_preflight', helper)
    if spec is None or spec.loader is None:
        raise RuntimeError('Cannot load optimized MuJoCo viewer runtime validator')
    module = importlib.util.module_from_spec(spec)
    # Keep annotations/dataclasses resolvable during module execution. This
    # private name cannot replace the ROS modules or alter their search paths.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.validate_optimized_viewer_runtime(ROOT, source)


def check_mujoco_viewer_dependencies(python: Path, source: Path, environment: dict,
                                    runtime: dict) -> dict:
    """Import the exact overlay and verify its loaded DLL; no GL context is created."""
    check = ('import importlib.metadata as m,json,sys; import mujoco,numpy,glfw; '
             'assert mujoco.__version__ == "4.0.1", "MuJoCo viewer needs 4.0.1"; '
             f'sys.path.insert(0,{str(source.parent)!r}); from live_viewer import runtime_identity; '
             'print(json.dumps({"python":sys.executable,"base_prefix":sys.base_prefix,'
             '"versions":{n:m.version(n) for n in ("mujoco","mujoco-core","numpy","glfw")},'
             '"module_files":{n:x.__file__ for n,x in (("mujoco",mujoco),("numpy",numpy),("glfw",glfw))},'
             '"runtime":runtime_identity(mujoco)}))')
    options = {'creationflags':subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    result = subprocess.run([str(python), '-I', '-c', check], env=environment,
                            capture_output=True, text=True, timeout=30, **options)
    if result.returncode:
        raise RuntimeError('MuJoCo viewer dependency check failed: '+result.stderr[-3000:])
    dependencies = json.loads(result.stdout)
    loaded = dependencies.get('runtime')
    if (not isinstance(loaded, dict) or loaded.get('dll_sha256') != runtime['runtime_identity']['viewer_dll_sha256']
            or not isinstance(loaded.get('dll_path'), str)
            or Path(loaded['dll_path']).resolve() != Path(runtime['dll_path']).resolve()):
        raise RuntimeError('MuJoCo viewer loaded DLL does not match its validated renderer overlay')
    return dependencies


def prepare_mujoco_viewer(manifest: Path, model_spec: dict, output: Path, inherited: dict, *,
                          timing_diagnostic: bool = False, continuous_render_diagnostic: bool = False,
                          pause_when_covered: bool = False) -> dict:
    source = ROOT/'tools/mujoco/live_viewer.py'
    protocol = source.with_name('viewer_protocol.py')
    for path in (source, protocol):
        if not path.is_file():
            raise RuntimeError('Missing MuJoCo viewer prerequisite: '+str(path))
    runtime = optimized_mujoco_viewer_runtime(source)
    python = Path(runtime['python'])
    if not python.is_file():
        raise RuntimeError('Missing validated MuJoCo viewer interpreter: '+str(python))
    environment, environment_policy = mujoco_viewer_environment(python, inherited)
    dependencies = check_mujoco_viewer_dependencies(python, source, environment, runtime)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reserve:
        reserve.bind(('127.0.0.1', 0))
        port = reserve.getsockname()[1]
    # The viewer confirms its bind before UE is launched. A port race fails the
    # startup explicitly; no control listener or retrying background process exists.
    token = uuid.uuid4().hex
    directory = output/'mujoco-viewer'
    directory.mkdir(exist_ok=False)
    expected = {'manifest_sha256':digest(manifest), 'model_sha256':model_spec['model_sha256'],
                'physics_identity_sha256':model_spec['physics_identity_sha256'], 'port':port,
                'session_token_sha256':hashlib.sha256(token.encode('ascii')).hexdigest(),
                'renderer_runtime_identity':runtime['runtime_identity']}
    status_file = directory/'status.json'
    command = [str(python), str(source), '--manifest', str(manifest), '--port', str(port),
               '--session-token', token, '--status-file', str(status_file), '--parent-pid', str(os.getpid()),
               '--static-flex-gpu-cache']
    if timing_diagnostic:
        command.append('--timing-diagnostic')
    if continuous_render_diagnostic:
        command.append('--continuous-render-diagnostic')
    occluder_pid = directory/'owned-ue.pid' if pause_when_covered and os.name == 'nt' else None
    if occluder_pid is not None:
        command.extend(['--occluder-pid-file', str(occluder_pid)])
    provenance = dict(enabled=True, transport='read_only_loopback_udp', host='127.0.0.1',
        status_file=str(status_file), python=str(python), python_sha256=digest(python),
        source_sha256={**runtime['source_sha256'],
            str(source.with_name('viewer_runtime.py').relative_to(ROOT)).replace('\\','/'):digest(source.with_name('viewer_runtime.py'))},
        environment=environment_policy, dependencies=dependencies, stream_hz=30, max_datagram_bytes=60*1024,
        renderer_runtime=runtime, static_flex_gpu_cache=True,
        timing_diagnostic=timing_diagnostic, continuous_render_diagnostic=continuous_render_diagnostic,
        pause_when_covered=occluder_pid is not None,
        startup_policy='bind_before_ue_then_first_native_frame_and_navigation_ready',
        close_policy='normal_viewer_or_rviz_close_stops_owned_session',
        scope='read-only native state visualization; UE owns all dynamics and control', **expected)
    return dict(command=command, environment=environment, expected=expected, token=token, occluder_pid=occluder_pid,
                status_file=status_file, provenance=provenance)


def bind_mujoco_viewer_process(process, actual_pid: int, command: list[str]) -> dict:
    """Pin the actual Windows venv child to its owned redirector and exact argv."""
    if type(actual_pid) is not int or actual_pid <= 0 or process.poll() is not None:
        raise ValueError('MuJoCo viewer cannot bind to an invalid PID or exited launcher')
    if actual_pid == process.pid:
        return dict(pid=actual_pid, launcher_pid=process.pid, method='owned_popen_process', _handles=[])
    if os.name != 'nt':
        raise ValueError('MuJoCo viewer PID does not match its owned process')
    import base64
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)]*4)]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    handles = []
    def creation(handle):
        fields = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *[ctypes.byref(value) for value in fields]):
            raise ctypes.WinError(ctypes.get_last_error())
        return (fields[0].dwHighDateTime << 32) | fields[0].dwLowDateTime
    def opened(pid):
        handle = kernel.OpenProcess(0x00100000 | 0x1000, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        handles.append(handle)
        if kernel.WaitForSingleObject(handle, 0) != 258:
            raise ValueError('MuJoCo viewer process chain contains an exited process')
        return handle
    try:
        # Keep the child's process object alive while resolving its ancestry.
        # Popen also retains the original redirector handle throughout this check.
        actual_handle = opened(actual_pid)
        original_creation = creation(int(process._handle))
        script = ("[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
                  f"$candidateId={actual_pid};$ownerId={int(process.pid)};$rows=@();$found=$false;"
                  "for($depth=0;$depth -lt 16;$depth++){"
                  "$item=Get-CimInstance Win32_Process -Filter ('ProcessId='+$candidateId);"
                  "if($null -eq $item){throw 'Process disappeared during viewer identity check'};"
                  "$rows+=@([pscustomobject]@{pid=[int]$item.ProcessId;parent_pid=[int]$item.ParentProcessId;command_line=$item.CommandLine});"
                  "if($candidateId -eq $ownerId){$found=$true;break};$candidateId=[int]$item.ParentProcessId};"
                  "if(-not $found){throw 'Viewer is not a descendant of its owned launcher'};"
                  "ConvertTo-Json -InputObject @($rows) -Depth 4 -Compress")
        powershell = Path(os.environ.get('SystemRoot', 'C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
        result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-EncodedCommand',
                                 base64.b64encode(script.encode('utf-16le')).decode('ascii')],
                                capture_output=True, text=True, encoding='utf-8', timeout=10,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise ValueError('MuJoCo viewer ancestry check failed: '+result.stderr[-1500:])
        chain = json.loads(result.stdout.lstrip('\ufeff'))
        if not isinstance(chain, list) or not 2 <= len(chain) <= 16 or chain[0]['pid'] != actual_pid or chain[-1]['pid'] != process.pid:
            raise ValueError('Invalid MuJoCo viewer process ancestry')
        records = []
        for index, item in enumerate(chain):
            handle = actual_handle if index == 0 else opened(item['pid'])
            born = creation(handle)
            if index and (records[-1]['parent_pid'] != item['pid'] or born > records[-1]['creation_filetime']):
                raise ValueError('MuJoCo viewer process ancestry changed or a PID was reused')
            records.append(dict(pid=item['pid'], parent_pid=item['parent_pid'], creation_filetime=born))
        if records[-1]['creation_filetime'] != original_creation or process.poll() is not None:
            raise ValueError('MuJoCo viewer launcher identity changed during binding')
        capacity = wintypes.DWORD(32768); image_buffer = ctypes.create_unicode_buffer(capacity.value)
        if not kernel.QueryFullProcessImageNameW(actual_handle, 0, image_buffer, ctypes.byref(capacity)):
            raise ctypes.WinError(ctypes.get_last_error())
        cfg = Path(command[0]).parent.parent/'pyvenv.cfg'
        values = dict((key.strip().lower(), value.strip()) for line in cfg.read_text().splitlines()
                      for key, separator, value in [line.partition('=')] if separator)
        normalize = lambda value:os.path.normcase(str(Path(value).resolve()))
        # A venv created by another venv records that redirector as executable;
        # Windows ultimately runs the interpreter from the explicit base home.
        base_home = Path(values['home'])
        if not base_home.is_absolute():
            raise ValueError('MuJoCo viewer venv base Python home is not absolute')
        base_image = (base_home/'python.exe').resolve(strict=True)
        if not base_image.is_file():
            raise ValueError('MuJoCo viewer venv base Python image is unavailable')
        allowed_images = {normalize(command[0]), normalize(base_image)}
        if values.get('executable'):
            allowed_images.add(normalize(values['executable']))
        if normalize(image_buffer.value) not in allowed_images:
            raise ValueError('MuJoCo viewer child uses an unexpected Python executable')
        shell = ctypes.WinDLL('shell32', use_last_error=True)
        shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
        shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
        kernel.LocalFree.argtypes = [wintypes.HLOCAL]; kernel.LocalFree.restype = wintypes.HLOCAL
        count = ctypes.c_int()
        argv_pointer = shell.CommandLineToArgvW(chain[0]['command_line'], ctypes.byref(count))
        if not argv_pointer:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            actual_argv = [argv_pointer[index] for index in range(count.value)]
        finally:
            kernel.LocalFree(ctypes.cast(argv_pointer, wintypes.HLOCAL))
        if len(actual_argv) != len(command) or normalize(actual_argv[0]) not in allowed_images:
            raise ValueError('MuJoCo viewer process command does not match the owned launch')
        for index in range(1, len(command)):
            path_argument = index == 1 or command[index-1] in ('--manifest', '--status-file')
            if (normalize(actual_argv[index]) != normalize(command[index]) if path_argument else actual_argv[index] != command[index]):
                raise ValueError('MuJoCo viewer process argument does not match the owned launch at index '+str(index))
        if process.poll() is not None or any(kernel.WaitForSingleObject(handle, 0) != 258 for handle in handles):
            raise ValueError('MuJoCo viewer process exited during identity verification')
        return dict(pid=actual_pid, launcher_pid=process.pid, method='windows_owned_venv_descendant',
                    executable=image_buffer.value, command_sha256=hashlib.sha256(chain[0]['command_line'].encode('utf-8')).hexdigest(),
                    ancestry=records, _handles=handles, _kernel=kernel, _actual_handle=actual_handle)
    except Exception:
        for handle in handles:
            kernel.CloseHandle(handle)
        raise


def close_mujoco_viewer_binding(binding):
    if binding and binding.get('_handles'):
        for handle in binding['_handles']:
            binding['_kernel'].CloseHandle(handle)
        binding['_handles'].clear()


def mujoco_viewer_exit_code(binding, process):
    if not binding or binding['pid'] == process.pid:
        return process.poll()
    import ctypes
    from ctypes import wintypes
    kernel, handle = binding['_kernel'], binding['_actual_handle']
    waited = kernel.WaitForSingleObject(handle, 0)
    if waited == 258:
        return None
    if waited != 0:
        raise ctypes.WinError(ctypes.get_last_error())
    code = wintypes.DWORD()
    if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
        raise ctypes.WinError(ctypes.get_last_error())
    return code.value


def read_mujoco_viewer_status(path: Path, expected: dict, pid, now: float) -> dict | None:
    try:
        if path.stat().st_size > 65536:
            raise ValueError('MuJoCo viewer status exceeds 64 KiB')
        status = json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, PermissionError):
        return None
    if not isinstance(status, dict) or status.get('schema') != 'rmsim_mujoco_viewer_status_v1':
        raise ValueError('Invalid MuJoCo viewer status schema')
    phase = status.get('phase')
    if phase not in ('loading', 'waiting', 'ready', 'stale', 'closed', 'error'):
        raise ValueError('Invalid MuJoCo viewer phase')
    for key, value in expected.items():
        if status.get(key) is not None and status[key] != value:
            raise ValueError('MuJoCo viewer identity mismatch: '+key)
        if (phase in ('waiting', 'ready', 'stale') or key in ('port', 'session_token_sha256')) and status.get(key) != value:
            raise ValueError('MuJoCo viewer identity is incomplete: '+key)
    expected_pid = pid(status.get('pid')) if callable(pid) else pid
    if status.get('pid') != expected_pid:
        raise ValueError('MuJoCo viewer PID does not match its owned process')
    stamp = status.get('status_written_monotonic_s')
    if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not math.isfinite(stamp) or stamp < 0 or stamp > now+1:
        raise ValueError('Invalid MuJoCo viewer status timestamp')
    status['status_age_seconds'] = max(0., now-stamp)
    status['status_fresh'] = status['status_age_seconds'] <= 2.
    if phase in ('waiting', 'ready', 'stale') and status.get('bound') is not True:
        raise ValueError('MuJoCo viewer has not bound its read-only input')
    if 'renderer_runtime_identity' in expected and phase in ('waiting', 'ready', 'stale'):
        cache = status.get('renderer_cache')
        if (not isinstance(cache, dict) or cache.get('schema') != 'rmsim_static_flex_vbo_v1'
                or cache.get('version') != 'rmsim.viewer-static-flex-vbo.1'
                or any(cache.get(key) is not True for key in ('requested', 'available', 'enabled'))):
            raise ValueError('MuJoCo viewer optimized renderer cache is not enabled')
        if any(type(cache.get(key)) is not int or cache[key] != 0 for key in ('failures', 'rejected_contexts')):
            raise ValueError('MuJoCo viewer renderer cache reported a failure')
        if phase in ('ready', 'stale'):
            counts = status.get('compiled_model_counts')
            flex_count = counts.get('nflex') if isinstance(counts, dict) else None
            if (type(flex_count) is not int or flex_count <= 0
                    or type(cache.get('live_flex')) is not int or cache['live_flex'] != flex_count
                    or any(type(cache.get(key)) is not int or cache[key] <= 0
                           for key in ('live_bytes', 'fast_batches'))):
                raise ValueError('MuJoCo viewer renderer cache lacks complete displayed flex coverage')
    if phase == 'ready':
        frames = status.get('frames')
        if isinstance(frames, bool) or not isinstance(frames, int) or frames < 1 or status.get('body_pose_matches') is not True:
            raise ValueError('MuJoCo viewer ready without a verified displayed native frame')
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene', choices=['rmuc2027', 'import_lab'], default='rmuc2027')
    parser.add_argument('--engine-root', type=Path, default=Path('E:/simulator-tools/UE_5.8'))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--port', type=int, default=9451)
    parser.add_argument('--domain', type=int, default=71)
    parser.add_argument('--control-profile', choices=['simulation','reality'], default='simulation', help='Use original sentry_ws Nav2 controller, smoother, planner and costmap configuration')
    parser.add_argument('--physics-backend', choices=['chaos','mujoco'], default='mujoco', help='Standard vehicle dynamics backend; chaos is available only for explicit engine diagnostics')
    parser.add_argument('--mujoco-model', type=Path, help='Prepared MuJoCo scene manifest; required for the MuJoCo backend')
    parser.add_argument('--navigation-source', type=Path, help='Explicit vehicle-supplied sentry_ws Nav2 YAML; omit to use the default for the selected scene and profile')
    parser.add_argument('--localization-map-yaml', type=Path, help='Optional validated AMCL map; navigation and costmap keep the scene map')
    parser.add_argument('--initial-pose-offset', nargs=3, type=float, default=None, metavar=('DX_M','DY_M','DYAW_RAD'), help='Explicit localization-recovery test offset from the scene default spawn; XY offsets use map axes')
    parser.add_argument('--navigation-follow-profile',type=Path,help='Vehicle-supplied high-level navigation gimbal-follow profile')
    parser.add_argument('--lidar-profile',type=Path,help='Explicit vehicle LiDAR profile; diagnostic sensor budget can use a separate file')
    parser.add_argument('--sim-step', type=float, choices=[0.001,0.002,0.005,0.01], default=0.005, help='Fixed physics-step seconds; acceptance required for non-default values')
    parser.add_argument('--no-rviz', action='store_true', help='Integration-test mode; normal users use RViz')
    parser.add_argument('--offscreen', action='store_true', help='Owned integration fixture only; requires --no-rviz')
    parser.add_argument('--fullscreen', action='store_true', help='Request a fullscreen UE window; actual client pixels must be observed separately')
    parser.add_argument('--width', type=int, help='Requested UE client width; visible default 1120, offscreen default 640')
    parser.add_argument('--height', type=int, help='Requested UE client height; visible default 860, offscreen default 360')
    parser.add_argument('--mujoco-viewer', action='store_true', help='Opt into the independent MuJoCo graphics window')
    parser.add_argument('--viewer-timing-diagnostic', action='store_true', help='Explicit MuJoCo display timing diagnostics; requires the visible native viewer')
    parser.add_argument('--viewer-continuous-render-diagnostic', action='store_true', help='Explicit continuous-render viewer baseline; native physics and snapshots remain unchanged')
    parser.add_argument('--no-rhi', action='store_true', help='Diagnostic renderer isolation only: no GUI, no hardware camera validation')
    parser.add_argument('--profile-sensor-timing',action='store_true', help='Native engine isolated raycast/camera timing logs')
    parser.add_argument('--profile-mujoco-timing',action='store_true',help='Native dynamics timing attribution for offscreen fixtures')
    parser.add_argument('--profile-frames',type=int,default=0,help='Bounded native CPU/physics CSV attribution; integration fixtures only')
    parser.add_argument('--verify-lidar-queries',action='store_true',help='Compare the first 4096 physical rays against serial queries')
    parser.add_argument('--lidar-serial-query',action='store_true',help='Diagnostic serial scene-query reference')
    parser.add_argument('--run-seconds', type=float, default=0., help='Optional bounded integration run')
    parser.add_argument('--viewport-hz',type=float,default=60.,help='Scene rendering cadence independent of 200Hz physical sampling')
    parser.add_argument('--render-scale',type=int,default=100,help='UE primary render resolution percentage (50–100)')
    parser.add_argument('--operator-gyro', action=argparse.BooleanOptionalAction, default=True,
                        help='Start this navigation session with operator spin armed')
    parser.add_argument('--operator-gyro-rate', type=float,
                        help='Initial armed spin rate in rad/s when --operator-gyro is selected')
    parser.add_argument('--camera-hz',type=float,default=0.,help='ROS RGB stream: 0 disables capture; 1..60 enables images independently of the UE viewport')
    parser.add_argument('--pace-real-time',action=argparse.BooleanOptionalAction,default=True,help='Pace actual fixed physics ticks against wall time; overload remains observable')
    parser.add_argument('--cpu-policy', choices=['performance','system'], default='performance', help='Owned paced UE process: dynamic performance CPU Sets on Windows, or preserve system policy')
    args = parser.parse_args()
    if args.operator_gyro_rate is not None and (
        not args.operator_gyro or not math.isfinite(args.operator_gyro_rate)
        or not 0.1 <= args.operator_gyro_rate <= 5.0
    ):
        parser.error('--operator-gyro-rate requires --operator-gyro and a rate from 0.1 to 5 rad/s')
    args.mujoco_viewer |= args.viewer_timing_diagnostic or args.viewer_continuous_render_diagnostic
    try:
        display_request, base_display_flags = ue_display_configuration(args)
    except ValueError as error:
        parser.error(str(error))
    if args.mujoco_viewer and not mujoco_viewer_enabled(args):
        parser.error('The optional MuJoCo viewer requires a visible MuJoCo navigation session with RViz')
    if args.physics_backend == 'mujoco':
        if args.mujoco_model is None:
            args.mujoco_model = ROOT/'assets/vehicles/aim_sentry/mujoco'/args.scene/'manifest.json'
        if not args.mujoco_model.is_file():
            parser.error('No prepared MuJoCo package for scene '+args.scene+': '+str(args.mujoco_model))
        args.mujoco_model = args.mujoco_model.resolve()
        model_spec = json.loads(args.mujoco_model.read_text(encoding='utf-8-sig'))
        if model_spec.get('diagnostic_plane_only') or model_spec.get('scene',{}).get('diagnostic_plane_only'):
            parser.error('Navigation requires a complete scene model, not a diagnostic plane')
        if args.sim_step > .005:
            parser.error('MuJoCo sensor integration requires --sim-step at most 0.005')
    if args.offscreen and not args.no_rviz: parser.error('--offscreen requires --no-rviz')
    if args.no_rhi and (not args.offscreen or not args.no_rviz): parser.error('--no-rhi requires headless diagnostic fixture')
    if args.profile_mujoco_timing and not args.offscreen: parser.error('--profile-mujoco-timing needs an offscreen fixture')
    if args.profile_sensor_timing and not args.offscreen: parser.error('--profile-sensor-timing needs an offscreen fixture')
    if args.profile_frames and (not args.offscreen or not 200<=args.profile_frames<=100000):parser.error('CPU profiling requires an offscreen fixture and 200..100000 frames')
    if args.verify_lidar_queries and args.lidar_serial_query:parser.error('Choose one LiDAR query diagnostic')
    if (args.verify_lidar_queries or args.lidar_serial_query) and not args.offscreen:parser.error('LiDAR query diagnostics require an offscreen fixture')
    if args.viewport_hz!=0 and not 10<=args.viewport_hz<=144:parser.error('Viewport Hz must be 0 or 10..144')
    if not 50<=args.render_scale<=100 or (args.no_rhi and args.render_scale!=100):
        parser.error('Render scale must be 50..100 and requires rendering')
    if args.camera_hz!=0 and not 1<=args.camera_hz<=60:parser.error('Camera Hz must be 0 or 1..60')
    if not 1024 <= args.port <= 65535 or not 0 <= args.domain <= 232 or args.run_seconds < 0:
        parser.error('Invalid port, ROS domain or duration')
    base = ROOT/'artifacts/navigation/console-live'
    base.mkdir(parents=True, exist_ok=True)
    output = args.output or base/(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    scene = prepare_scene(args.scene, output/'scene', args.localization_map_yaml, args.initial_pose_offset)
    runtime_arena = None
    if args.scene == 'rmuc2027':
        # Both physics backends stage the canonical venue for the same RMUC map.
        runtime_arena = copy_runtime_arena(ROOT/'assets/maps/rmuc2027/arena-shell.json',
                                           Path(scene['scene_file']).parent)
    if args.physics_backend == 'mujoco':
        if args.scene != 'rmuc2027':
            raise ValueError('The prepared MuJoCo scene is RMUC2027; choose its matching scene')
        verification = subprocess.run([sys.executable, str(ROOT/'tools/mujoco/verify_model.py'),
            '--manifest', str(args.mujoco_model), '--map-file', str(scene['scene_file']),
            '--preflight-only', '--output', str(output/'mujoco-preflight')], capture_output=True, text=True)
        if verification.returncode:
            raise RuntimeError('MuJoCo source/model preflight failed; see '+str(output/'mujoco-preflight/report.json'))
        mujoco_sdk_root = ROOT/'third_party/mujoco-sdk/4.0.1-rmsim-static-world-flex.3/windows-x86_64'
        mujoco_sdk = json.loads((mujoco_sdk_root/'rmsim-build-manifest.json').read_text(encoding='utf-8-sig'))
        mujoco_runtime = ROOT/'unreal/RMSim/Binaries/Win64/mujoco.dll'
        mujoco_runtime_sha256 = digest(mujoco_runtime)
        if (mujoco_sdk.get('runtime_version_string') != '4.0.1+rmsim.static-world-flex.3'
                or mujoco_runtime_sha256 != digest(mujoco_sdk_root/'bin/mujoco.dll')):
            raise ValueError('Staged MuJoCo runtime does not match the selected static-flex SDK')
    localization_map = None
    effective_localization_map_yaml = scene['localization_map_yaml']
    if effective_localization_map_yaml:
        from rmsim_bridge.navigation_map_identity import validate_localization_map
        localization_map = validate_localization_map(scene['map_yaml'], effective_localization_map_yaml)
    navigation_source = select_navigation_source(scene, args.control_profile, output, args.navigation_source)
    drive_source = (ROOT/'assets/vehicles/aim_sentry/drive-profile.json').resolve()
    from rmsim_bridge.navigation_drive_profile import load_drive_profile
    navigation_drive_profile = load_drive_profile(drive_source)
    follow_source = (args.navigation_follow_profile or ROOT/'assets/vehicles/aim_sentry/navigation-follow.json').resolve()
    lidar_source = (args.lidar_profile or ROOT/'assets/vehicles/aim_sentry/sensors/mid360.json').resolve()
    if not lidar_source.is_file() or lidar_source.stat().st_size>16384: raise ValueError('Invalid vehicle lidar profile')
    if not follow_source.is_file() or follow_source.stat().st_size>16384:
        raise ValueError('Invalid vehicle gimbal-follow profile')
    from rmsim_bridge.sentry_navigation_source import source_profile
    source_profile(ROOT/'ros2_ws/src/pb2025_nav_bringup/config/reality/nav2_params.yaml',args.control_profile,str(navigation_source))
    config = output/'navigation.rviz'
    rviz_config = rviz_configuration(scene['view'])
    coverage_prefix = ROOT/'ros2_ws/install_rviz_coverage'
    coverage_library = coverage_prefix/'bin/rmsim_rviz_coverage.dll'
    rviz_coverage = (os.name == 'nt' and not args.no_rviz and coverage_library.is_file() and
        (coverage_prefix/'share/ament_index/resource_index/rviz_common__pluginlib__plugin/rmsim_rviz_coverage').is_file())
    if rviz_coverage:
        rviz_config['Visualization Manager']['Displays'].append({
            'Class':'rmsim_rviz_coverage/FullscreenCoverage','Name':'Fullscreen coverage','Enabled':True,'Value':True})
    config.write_text(json.dumps(rviz_config, indent=2)+'\n', encoding='utf-8')
    from ament_index_python.packages import get_package_share_directory, get_package_prefix
    from rmsim_bridge.native_transport import loopback_environment
    profile = Path(get_package_share_directory('rmsim_bridge'))/'config/fastdds-loopback-udp.xml'
    original = dict(os.environ)
    original['ROS_DOMAIN_ID'] = str(args.domain)
    local_environment, transport = loopback_environment(original, profile)
    os.environ.update(local_environment)
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
    from std_msgs.msg import String
    from geometry_msgs.msg import PoseWithCovarianceStamped
    from nav_msgs.msg import OccupancyGrid, Path as NavPath
    from action_msgs.msg import GoalStatusArray
    from rosgraph_msgs.msg import Clock
    rclpy.init()
    node = Node('rmsim_navigation_console_monitor')
    state = {'phase':'starting', 'scene':args.scene, 'pid':os.getpid(), 'output_directory':str(output),
             'ros_domain_id':args.domain, 'ros_port':args.port, 'transport':transport,
             'control_profile':args.control_profile,'sim_step_seconds':args.sim_step,'no_rhi_diagnostic':args.no_rhi,
             'sensor_timing_diagnostic':args.profile_sensor_timing,'mujoco_timing_diagnostic':args.profile_mujoco_timing,'viewport_hz':args.viewport_hz,'pace_real_time':args.pace_real_time,
             'camera_hz':args.camera_hz,'camera_capture_enabled':args.camera_hz>0 and not args.no_rhi,
             'display_request':display_request,'render_scale_percent':args.render_scale,
             'viewer_timing_diagnostic':args.viewer_timing_diagnostic,
             'viewer_continuous_render_diagnostic':args.viewer_continuous_render_diagnostic,
             'cpu_policy_requested':args.cpu_policy,'process_policy':None,
             'navigation_source':str(navigation_source),'gimbal_follow_profile':str(follow_source),'lidar_profile':str(lidar_source),
             'navigation_drive_profile':navigation_drive_profile,
             'mujoco_viewer':{'enabled':mujoco_viewer_enabled(args)},
             'navigation':None, 'bridge':None, 'estimated_pose':None, 'clock_ns':0,
             'map_received':False, 'path_points':0, 'actions':{}, 'pids':{},
             'scope':'Point-LIO local mapping + AMCL map localization + original Nav2 consume no evaluation truth; optional MuJoCo viewer uses native state only for display and identity validation'}
    history = []
    status_sequence = 0
    def nav_status(message):
        state['navigation'] = json.loads(message.data)
        state['navigation_received_wall'] = time.monotonic()
    def bridge_status(message):
        value = json.loads(message.data)
        state['bridge_received_monotonic_s'] = time.monotonic()
        state['bridge'] = {k:value.get(k) for k in (
            'connected','state_fresh','session','epoch','sim_time_ns','command',
            'simulation_rate','clock_source','physics_backend','dynamics_step_s','fixed_step_s','aim','aim_adapter',
            'imu','lidar','rendering','camera_config','published_images')}
    def pose(message):
        p, q = message.pose.pose.position, message.pose.pose.orientation
        state['estimated_pose'] = {'frame':message.header.frame_id, 'x':p.x, 'y':p.y,
                                   'q':[q.x,q.y,q.z,q.w], 'stamp_ns':message.header.stamp.sec*10**9+message.header.stamp.nanosec}
    def action_status(name, message):
        value = [{'id':bytes(item.goal_info.goal_id.uuid).hex(), 'status':item.status} for item in message.status_list]
        if state['actions'].get(name) != value:
            state['actions'][name] = value
            event = {'wall':time.time(), 'monotonic_s':time.monotonic(), 'clock_ns':state['clock_ns'], 'action':name, 'goals':value}
            if state.get('rviz_ready_frame'):
                frame = state['rviz_ready_frame']
                event['wall_since_rviz_ready_s'] = event['monotonic_s'] - frame['monotonic_s']
                event['sim_since_rviz_ready_s'] = (state['clock_ns'] - frame['clock_ns'])*1e-9
            history.append(event)
    def on_map(message):
        state['map_received'] = message.header.frame_id == 'aim_sentry/map' and message.info.width > 0
        state['map_size'] = [message.info.width, message.info.height]
    node.create_subscription(String, '/rmsim/aim_sentry/navigation', nav_status, 1)
    node.create_subscription(String, '/rmsim/aim_sentry/state', bridge_status, 1)
    node.create_subscription(PoseWithCovarianceStamped, '/amcl_pose', pose, 1)
    node.create_subscription(Clock, '/clock', lambda m:state.update(clock_ns=m.clock.sec*10**9+m.clock.nanosec), 1)
    node.create_subscription(NavPath, '/plan', lambda m:state.update(path_points=len(m.poses)), 1)
    latched = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL)
    node.create_subscription(OccupancyGrid, '/map', on_map, latched)
    for name in ('navigate_to_pose', 'follow_waypoints', 'navigate_through_poses'):
        node.create_subscription(GoalStatusArray, '/'+name+'/_action/status', lambda m,n=name:action_status(n,m), latched)
    children: dict[str, subprocess.Popen] = {}
    logs = []
    viewer_plan = None
    viewer_status = None
    viewer_binding = None
    next_viewer_poll = 0.
    def save():
        nonlocal status_sequence
        status_sequence += 1
        state['snapshot_sequence'] = status_sequence
        state['snapshot_written_monotonic_s'] = time.monotonic()
        state['status_period_seconds'] = .5
        state['launcher_pids'] = {name:p.pid for name,p in children.items() if p.poll() is None}
        state['pids'] = {name:(viewer_binding['pid'] if name == 'mujoco_viewer' else p.pid)
                         for name,p in children.items() if p.poll() is None and (name != 'mujoco_viewer' or viewer_binding)}
        state['updated_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if not atomic_json(output/'status.json', state):
            state['snapshot_write_deferrals'] = state.get('snapshot_write_deferrals', 0)+1
        atomic_json(output/'action-events.json', history[-500:])
    def launch(name, command, environment):
        log = (output/(name+'.log')).open('w', encoding='utf-8'); logs.append(log)
        options = {'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
        process = subprocess.Popen([str(x) for x in command], stdout=log, stderr=subprocess.STDOUT,
                                   env=environment, cwd=ROOT, **options)
        children[name] = process
        if name == 'ue':
            policy = apply_owned_process_policy(process, editor, paced=args.pace_real_time, mode=args.cpu_policy,
                                                worker_budget=provenance['worker_budget'])
            policy.update(session_directory=str(output), runtime_provenance_path=str(output/'runtime-provenance.json'),
                          runtime_provenance_sha256=digest(output/'runtime-provenance.json'),
                          module_sha256=provenance['module_sha256'], capture_phase='immediately_after_owned_ue_popen')
            policy_path = output/'process-policy/verification.json'
            policy_path.parent.mkdir(exist_ok=False)
            with policy_path.open('x', encoding='utf-8') as policy_file:
                json.dump(policy, policy_file, indent=2); policy_file.write('\n')
            state['process_policy'] = {key:policy.get(key) for key in (
                'status','outcome','reason','pid','requested_cpu_set_ids','observed_default_cpu_set_ids')}
            state['process_policy'].update(path=str(policy_path), sha256=digest(policy_path))
            print('Owned UE CPU policy: '+json.dumps(state['process_policy']), flush=True)
        save(); return process

    def poll_viewer():
        nonlocal viewer_status, next_viewer_poll, viewer_binding
        if viewer_plan is None:
            return None
        process = children['mujoco_viewer']
        now = time.monotonic()
        def resolve_pid(actual_pid):
            nonlocal viewer_binding
            if viewer_binding is None:
                viewer_binding = bind_mujoco_viewer_process(process, actual_pid, viewer_plan['command'])
                state['mujoco_viewer_process'] = {key:value for key,value in viewer_binding.items() if not key.startswith('_')}
            elif viewer_binding['pid'] != actual_pid:
                raise ValueError('MuJoCo viewer changed its bound process identity')
            return viewer_binding['pid']
        if now >= next_viewer_poll:
            next_viewer_poll = now+.1
            received = read_mujoco_viewer_status(viewer_plan['status_file'], viewer_plan['expected'], resolve_pid, now)
            if received is not None:
                viewer_status = received
        if viewer_status is not None:
            viewer_status['status_age_seconds'] = max(0., now-viewer_status['status_written_monotonic_s'])
            viewer_status['status_fresh'] = viewer_status['status_age_seconds'] <= 2.
            state['mujoco_viewer'] = dict(viewer_status, enabled=True)
            if viewer_status['phase'] == 'error':
                raise RuntimeError('MuJoCo viewer failed: '+str(viewer_status.get('error') or viewer_status.get('exit_reason') or 'see mujoco_viewer.log'))
        exit_code = mujoco_viewer_exit_code(viewer_binding, process)
        launcher_exit = process.poll()
        if exit_code is None and launcher_exit is not None:
            raise RuntimeError('MuJoCo viewer launcher exited while its bound window process was still running')
        if exit_code is not None:
            state['mujoco_viewer_exit_code'] = exit_code
            if exit_code != 0:
                raise RuntimeError('MuJoCo viewer exited with code '+str(exit_code)+'; see mujoco_viewer.log')
            if viewer_status is None or viewer_status['phase'] not in ('closed', 'ready', 'stale'):
                raise RuntimeError('MuJoCo viewer exited before displaying a native frame; see mujoco_viewer.log')
            raise StopRequested()
        if viewer_status is not None and viewer_status['phase'] == 'closed':
            raise StopRequested()
        return viewer_status

    try:
        editor = (args.engine_root/'Engine/Binaries/Win64/UnrealEditor.exe').resolve()
        project = ROOT/'unreal/RMSim/RMSim.uproject'
        rviz = ROOT/'artifacts/runtime/rviz/windows/rviz2.exe'
        ros2 = shutil.which('ros2')
        if not editor.is_file() or not ros2 or (not args.no_rviz and not rviz.is_file()):
            raise RuntimeError('Missing UE/ROS/RViz prerequisites; run tools/environment/Prepare-NavigationConsole.cmd')
        if mujoco_viewer_enabled(args):
            viewer_plan = prepare_mujoco_viewer(args.mujoco_model, model_spec, output, original,
                timing_diagnostic=args.viewer_timing_diagnostic,
                continuous_render_diagnostic=args.viewer_continuous_render_diagnostic,
                pause_when_covered=not args.viewer_continuous_render_diagnostic and
                    os.environ.get('RMSIM_VIEWER_RENDER_COVERED_DIAGNOSTIC') != '1')
        # A dedicated port is checked before taking any UI ownership.
        with socket.socket() as check:
            check.bind(('127.0.0.1', args.port))
        provenance = dict(scene=scene, transport=transport, git_head=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
                          module_sha256=digest(ROOT/'unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll'),
                          engine_config_sha256=digest(ROOT/'unreal/RMSim/Config/DefaultEngine.ini'),
                          drive_profile_sha256=navigation_drive_profile['source_sha256'],
                          navigation_drive_profile=navigation_drive_profile,
                          rig_sha256=digest(ROOT/'assets/vehicles/aim_sentry/ue_mesh_rig.json'),
                          lidar_sha256=digest(lidar_source),lidar_profile=str(lidar_source),
                          imu_sha256=digest(ROOT/'assets/vehicles/aim_sentry/sensors/mid360-imu.json'),
                          navigation_follow_sha256=digest(follow_source),navigation_follow_profile=str(follow_source),
                          source_control_config_sha256=digest(navigation_source),navigation_source=str(navigation_source),
                          control_profile=args.control_profile,sim_step_seconds=args.sim_step,no_rhi_diagnostic=args.no_rhi,
                          viewport_hz=args.viewport_hz,pace_real_time=args.pace_real_time,
                          camera_hz=args.camera_hz,camera_capture_enabled=args.camera_hz>0 and not args.no_rhi,
                          camera_width=320,camera_height=240,
                          physics_max_substep_seconds=.005,
                          physics_config_override='-ini:Engine:[/Script/Engine.PhysicsSettings]:MaxSubstepDeltaTime=0.005',
                          offscreen=args.offscreen,no_rviz=args.no_rviz,
                          display_request=display_request,render_scale_percent=args.render_scale,
                          viewer_timing_diagnostic=args.viewer_timing_diagnostic,
                          viewer_continuous_render_diagnostic=args.viewer_continuous_render_diagnostic,
                          sensor_timing_diagnostic=args.profile_sensor_timing,mujoco_timing_diagnostic=args.profile_mujoco_timing,status_period_seconds=.5,
                          verify_lidar_queries=args.verify_lidar_queries,lidar_serial_query=args.lidar_serial_query,
                          profile_frames=args.profile_frames)
        provenance['physics_backend'] = args.physics_backend
        provenance['mujoco_viewer'] = viewer_plan['provenance'] if viewer_plan else dict(enabled=False, reason='not_requested')
        if args.physics_backend == 'mujoco':
            provenance['mujoco_model'] = str(args.mujoco_model)
            provenance['mujoco_model_sha256'] = digest(args.mujoco_model)
            provenance['mujoco_sdk'] = mujoco_sdk
            provenance['mujoco_sdk_root'] = str(mujoco_sdk_root)
            provenance['mujoco_runtime_file'] = str(mujoco_runtime)
            provenance['mujoco_runtime_sha256'] = mujoco_runtime_sha256
            provenance['ue_query_scene_max_substep_seconds'] = provenance['physics_max_substep_seconds']
            provenance['physics_max_substep_seconds'] = json.loads(args.mujoco_model.read_text(encoding='utf-8-sig'))['timestep_s']
        provenance['localization_map'] = localization_map
        provenance['cpu_policy_requested'] = args.cpu_policy
        provenance['cpu_policy_platform'] = os.name
        provenance['cpu_policy_source_sha256'] = digest(Path(__file__).with_name('platform_cpu_policy.py'))
        provenance['process_policy_path'] = str(output/'process-policy/verification.json')
        provenance['ros_process_transport'] = {name:dict(transport) for name in ('monitor','bridge','pointlio','navigation','rviz')}
        provenance['rviz_fullscreen_coverage'] = dict(installed=rviz_coverage,
            plugin_sha256=digest(coverage_library) if rviz_coverage else None,
            diagnostic_unpaused=os.environ.get('RMSIM_RVIZ_RENDER_COVERED_DIAGNOSTIC')=='1')
        provenance['git_worktree_status'] = subprocess.check_output(
            ['git','status','--porcelain=v1','--untracked-files=normal'],cwd=ROOT,text=True).splitlines()
        source_root=ROOT/'ros2_ws/src/rmsim_bridge/rmsim_bridge'
        import rmsim_bridge
        installed_root=Path(rmsim_bridge.__file__).resolve().parent
        provenance['bridge_inputs']={}
        for source in sorted(source_root.rglob('*.py')):
            name=source.relative_to(source_root).as_posix();installed=installed_root/name
            provenance['bridge_inputs'][name]={'source_sha256':digest(source),'installed_sha256':digest(installed)}
            if source.read_text(encoding='utf-8')!=installed.read_text(encoding='utf-8'):
                raise RuntimeError('Rebuild the installed bridge package before launching: '+name)
        provenance['native_navigation']={name:digest(ROOT/'artifacts/runtime/navigation/windows'/name) for name in ('loam_interface.dll','sensor_scan_generation.dll','fake_vel_transform.dll')}
        provenance['pointlio_inputs']=capture_pointlio_inputs(ROOT, Path(get_package_prefix('point_lio')))
        provenance['runtime_identity_source_sha256']=digest(Path(__file__).with_name('runtime_identity.py'))
        provenance['console_source_sha256']=digest(Path(__file__))
        provenance['console_config_source_sha256']=digest(Path(__file__).with_name('navigation_console_config.py'))
        if runtime_arena is not None:
            provenance['runtime_arena'] = runtime_arena
        provenance['dependency_lock_sha256']=digest(ROOT/'tools/environment/pixi.lock')
        provenance['capture_scope']='inputs captured before process launch; source/installed Python text compared'
        display_flags=list(base_display_flags)
        if runtime_arena and runtime_arena['render_profile'] == 'RMSimGymLumen':
            display_flags.extend(['-CustomConfig=RMSimGymLumen', '-d3d12', '-sm6'])
        if args.render_scale!=100:
            display_flags.append(f'-ExecCmds=r.ScreenPercentage {args.render_scale}')
        if args.no_rhi: display_flags.append('-nullrhi')
        if args.profile_sensor_timing: display_flags.append('-ProfileSensorTiming')
        if args.profile_mujoco_timing: display_flags.append('-ProfileMujocoTiming')
        if args.profile_frames:
            display_flags.extend(['-csvCaptureFrames='+str(args.profile_frames),'-csvCategories=PhysicsVerbose,PhysicsCounters','-csvCompression=0','-csvStatCounts'])
        gpu_diagnostic = os.environ.get('RMSIM_GPU_SCHEDULING_DIAGNOSTIC')
        if gpu_diagnostic in ('1', 'passes'):
            if not args.fullscreen or args.run_seconds <= 0 or args.no_rhi or args.profile_frames:
                raise ValueError('GPU scheduling diagnostic requires a bounded fullscreen run with its own capture')
            # These variables change GPU queue placement and RHI buffering only.
            # Restore their established defaults between the two independent trials.
            events = [
                '1000:r.ProfileGPU.ShowUI 0', '2000:r.Lumen.Reflections.AsyncCompute',
                '2000:r.GTSyncType', '4000:r.Lumen.Reflections.AsyncCompute 1',
                '8000:r.Lumen.Reflections.AsyncCompute 0', '12000:r.GTSyncType -2',
                '16000:r.GTSyncType 0',
            ]
            frames = 16800
            if gpu_diagnostic == 'passes':
                # A solver-only tick can have no scene pass. Sample adjacent
                # engine frames here to capture one that actually renders.
                events = ['1000:r.ProfileGPU.ShowUI 0']
                events.extend(f'{frame}:ProfileGPU' for frame in range(5200, 5254))
                frames = 5700
            display_flags.extend(['-csvCaptureFrames='+str(frames), '-csvCompression=0',
                                  '-csvExecCmds='+','.join(events)])
            provenance['gpu_scheduling_diagnostic'] = events
        if args.verify_lidar_queries: display_flags.append('-VerifyLidarQueries')
        if args.lidar_serial_query: display_flags.append('-LidarSerialQuery')
        if args.viewport_hz: display_flags.append('-RMSimViewportHz='+str(args.viewport_hz))
        if args.pace_real_time: display_flags.append('-SimRealTimePace')
        ue_command = [str(value) for value in [editor, project, '/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode',
            '-game',*display_flags,'-NoSplash','-NoSound',
            # Apply before world/Chaos creation; manual entry points retain their 1/240 s substeps.
            provenance['physics_config_override'],
            '-RosControl','-RosAimControl','-RosImu','-RosLidar','-RosOperatorGyroControl',
            '-LidarProfile='+str(lidar_source),
            '-ImuProfile='+str(ROOT/'assets/vehicles/aim_sentry/sensors/mid360-imu.json'),'-SimStepSeconds='+str(args.sim_step),'-RosCameraWidth=320','-RosCameraHeight=240','-RosCameraHz='+str(args.camera_hz),
            '-RosPort='+str(args.port), '-MeshRigFile='+str(ROOT/'assets/vehicles/aim_sentry/ue_mesh_rig.json'),
            '-MapFile='+scene['scene_file'], '-abslog='+str(output/'runtime.log')]]
        if args.operator_gyro:
            ue_command.append('-RosOperatorGyroStart')
            startup_rate = args.operator_gyro_rate if args.operator_gyro_rate is not None else 5.0
            ue_command.append('-RosOperatorGyroStartRate='+str(startup_rate))
        if args.physics_backend == 'mujoco':
            ue_command.extend(['-PhysicsBackend=mujoco','-MujocoModel='+str(args.mujoco_model)])
        if viewer_plan:
            ue_command.extend(['-MujocoViewerPort='+str(viewer_plan['expected']['port']),
                               '-MujocoViewerToken='+viewer_plan['token']])
        worker_budget = prepare_ue_worker_budget(ue_command, paced=args.pace_real_time, mode=args.cpu_policy)
        ue_command = worker_budget['command']
        provenance['worker_budget'] = worker_budget
        provenance['worker_budget_sha256'] = worker_budget_digest(worker_budget)
        provenance['ue_command'] = ue_command
        provenance['ue_command_sha256'] = worker_budget_digest(ue_command)
        provenance['ue_corelimit'] = worker_budget['corelimit']
        state['worker_budget'] = {key:worker_budget.get(key) for key in (
            'status','action','reason','corelimit','corelimit_source','physical_core_count','logical_processor_count')}
        (output/'runtime-provenance.json').write_text(json.dumps(provenance, indent=2)+'\n', encoding='utf-8')
        (base/'latest.json').write_text(json.dumps({'output_directory':str(output),'pid':os.getpid()})+'\n', encoding='utf-8')
        if not worker_budget['launch_allowed']:
            raise ValueError('UE worker budget conflicts with the explicit launch arguments: '+worker_budget.get('error',''))
        print('Owned UE worker budget: '+json.dumps(state['worker_budget']), flush=True)
        deadline = time.monotonic()+180
        last_save = 0.
        if viewer_plan:
            launch('mujoco_viewer', viewer_plan['command'], viewer_plan['environment'])
            print('Opening read-only MuJoCo viewer. Waiting for its model and loopback input...', flush=True)
            while True:
                rclpy.spin_once(node, timeout_sec=.05)
                if (output/'stop.request').exists():
                    raise StopRequested()
                visible = poll_viewer()
                if visible and visible['phase'] in ('waiting', 'ready') and visible['status_fresh']:
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError('MuJoCo viewer did not bind within the 180s startup budget')
                if time.monotonic()-last_save > .5:
                    state['phase'] = 'opening_mujoco_viewer'
                    save(); last_save = time.monotonic()
        launch('recorder', [sys.executable, ROOT/'tools/integration/navigation_bag.py', output], local_environment)
        launch('ue', ue_command, original)
        if viewer_plan and viewer_plan.get('occluder_pid') is not None:
            destination = viewer_plan['occluder_pid']
            temporary = destination.with_suffix('.tmp')
            temporary.write_text(str(children['ue'].pid), encoding='ascii')
            temporary.replace(destination)
        launch('pointlio', [ros2,'run','rmsim_bridge','point_lio','--ros-args','-p','imu_id:=mid360_imu','-p','sentry_source:='+str(navigation_source),'-p','output_directory:='+str(output/'pointlio')], local_environment)
        launch('navigation', [ros2,'run','rmsim_bridge','navigation','--ros-args','-p',
            'reference_config:='+str(ROOT/'ros2_ws/src/pb2025_nav_bringup/config/reality/nav2_params.yaml'),
            '-p','map_yaml:='+scene['map_yaml'],
            *(['-p','localization_map_yaml:='+effective_localization_map_yaml] if effective_localization_map_yaml else []),
            '-p','initial_pose:='+json.dumps(scene['initial_pose']),
            '-p','control_profile:='+args.control_profile,'-p','control_source:='+str(navigation_source),
            '-p','drive_profile:='+str(drive_source),
            '-p','motion_ready_file:='+str(output/'drive-ready.json'),
            '-p','native_runtime:='+str(ROOT/'artifacts/runtime/navigation/windows'),
            '-p','output_directory:='+str(output/'navigation')], local_environment)
        launch('bridge', [ros2,'run','rmsim_bridge','bridge','--ros-args','-p','port:='+str(args.port),'-p','aim_control:=true',
            '-p','navigation_follow:=true','-p','navigation_follow_profile:='+str(follow_source)], local_environment)
        if not viewer_plan:
            deadline = time.monotonic()+180
        print('Starting native navigation. Waiting for measured localization, Nav2 and enabled viewer readiness...', flush=True)
        while True:
            rclpy.spin_once(node, timeout_sec=.05)
            if (output/'stop.request').exists(): raise StopRequested()
            visible = poll_viewer()
            exited={name:p.poll() for name,p in children.items() if p.poll() is not None}
            if exited:
                state['component_exits']=exited
                raise RuntimeError('Owned component exited during startup: '+str(exited)+'; see component logs')
            nav = state['navigation'] or {}
            viewer_ready = not viewer_plan or (visible and visible['phase']=='ready' and visible['status_fresh'])
            if (nav.get('phase')=='running' and state['map_received'] and state['estimated_pose'] and viewer_ready
                    and (output/'bag-ready.json').is_file()):
                break
            if nav.get('phase')=='map_mismatch': raise RuntimeError('Scene/navigation map identity mismatch')
            if time.monotonic()>deadline: raise TimeoutError('Navigation and enabled viewer did not become ready within the 180s startup budget')
            if time.monotonic()-last_save>.5:
                state['phase']='waiting_mujoco_viewer' if nav.get('phase')=='running' and not viewer_ready else nav.get('phase','starting')
                save(); last_save=time.monotonic()
        state['phase']='opening_rviz' if not args.no_rviz else 'ready'
        if not args.no_rviz:
            gui_environment = dict(local_environment)
            prefix = Path(os.environ['CONDA_PREFIX'])/'Library'
            gui_environment['QT_PLUGIN_PATH'] = str(prefix/'plugins')
            gui_environment['QT_QPA_PLATFORM_PLUGIN_PATH'] = str(prefix/'plugins/platforms')
            if rviz_coverage:
                for variable, directory in (('AMENT_PREFIX_PATH', coverage_prefix), ('PATH', coverage_prefix/'bin')):
                    gui_environment[variable] = str(directory)+os.pathsep+gui_environment.get(variable,'')
                gui_environment['RMSIM_RVIZ_OCCLUDER_PID'] = str(children['ue'].pid)
                gui_environment['RMSIM_RVIZ_READY_PATH'] = str(output/'rviz-first-frame.json')
            if not rviz_coverage:
                raise RuntimeError('Visible navigation requires the RViz frame readiness plugin. Run Prepare-NavigationConsole.cmd.')
            observer = launch('rviz', [rviz,'-d',config,'--ros-args','-p','use_sim_time:=true'], gui_environment)
            frame_file = output/'rviz-first-frame.json'
            frame_deadline = time.monotonic() + 120
            while True:
                rclpy.spin_once(node, timeout_sec=.025)
                if (output/'stop.request').exists(): raise StopRequested()
                if any(p.poll() is not None for p in children.values()):
                    raise RuntimeError('An owned component exited before RViz rendered the navigation scene')
                if frame_file.is_file():
                    frame = json.loads(frame_file.read_text(encoding='utf-8'))
                    if (frame.get('pid') != observer.pid or frame.get('draws',0) < 1 or
                            [frame.get('map_width'),frame.get('map_height')] != state.get('map_size') or
                            not frame.get('local_map') or not frame.get('robot_tf') or frame.get('clock_ns',0) <= 0):
                        raise RuntimeError('RViz frame does not match the active navigation scene')
                    if (state.get('navigation') or {}).get('phase') == 'running' and atomic_json(output/'drive-ready.json', frame):
                        state['rviz_ready_frame'] = frame
                        break
                if time.monotonic() >= frame_deadline: raise TimeoutError('RViz navigation scene first frame')
                if time.monotonic()-last_save > .5:
                    save(); last_save=time.monotonic()
            state['phase']='ready'
        save()
        if args.no_rviz:
            if not atomic_json(output/'drive-ready.json', {'headless':True}): raise RuntimeError('Could not release headless motion')
            print('READY: UE5 and ROS navigation. Press Ctrl+C here to stop this session.', flush=True)
        else:
            windows = 'UE5 + synchronized MuJoCo viewer + RViz' if viewer_plan else 'UE5 + RViz'
            close_windows = 'RViz or the MuJoCo viewer' if viewer_plan else 'RViz'
            print(f'READY: {windows}. RViz Nav2 Goal selects a destination. Waypoint mode queues destinations. Cancel stops navigation.', flush=True)
            print(f'Close {close_windows}, or press Ctrl+C here, to stop this entire owned session.', flush=True)
        ready_at=time.monotonic()
        while not args.run_seconds or time.monotonic()-ready_at<args.run_seconds:
            rclpy.spin_once(node, timeout_sec=.05)
            if (output/'stop.request').exists(): raise StopRequested()
            visible = poll_viewer()
            if 'rviz' in children and children['rviz'].poll() is not None:
                state['rviz_exit_code']=children['rviz'].returncode
                if children['rviz'].returncode != 0: raise RuntimeError('RViz exited with code '+str(children['rviz'].returncode)+'; see rviz.log')
                break
            exited={name:p.returncode for name,p in children.items() if name!='rviz' and p.poll() is not None}
            if exited:
                state['component_exits']=exited
                raise RuntimeError('Owned component exited during run: '+str(exited)+'; see component logs')
            if time.monotonic()-last_save>.5:
                viewer_ready = not viewer_plan or (visible and visible['phase']=='ready' and visible['status_fresh'])
                state['phase']='ready' if (state['navigation'] or {}).get('phase')=='running' and viewer_ready else 'recovering'
                save(); last_save=time.monotonic()
        state['phase']='stopped'
    except (KeyboardInterrupt, StopRequested):
        state['phase']='stopped'
    except Exception as error:
        state.update(phase='failed', error=repr(error)); print('Navigation console failed:', error, flush=True)
        import traceback
        (output/'failure-trace.txt').write_text(traceback.format_exc(),encoding='utf-8')
    finally:
        for name, process in reversed(list(children.items())):
            if name == 'recorder': continue
            try: terminate_owned(process)
            except Exception as error: state.setdefault('cleanup_errors', []).append(name+': '+str(error))
        if 'recorder' in children:
            recorder = children['recorder']
            try:
                (output/'bag-stop.request').touch()
                if recorder.wait(timeout=120) != 0:
                    raise RuntimeError('Recorder failed; see recorder.log and rosbag')
                reviewed = json.loads((output/'bag-review.json').read_text(encoding='utf-8'))
                if not reviewed['verified']: raise RuntimeError('Required bag streams failed readback')
                state['recording'] = {'verified': True, 'directory': str(output/'rosbag'),
                                      'messages': reviewed['message_count']}
            except Exception as error:
                state.setdefault('cleanup_errors',[]).append('recorder: '+str(error))
                if recorder.poll() is None: terminate_owned(recorder)
        close_mujoco_viewer_binding(viewer_binding)
        save()
        for log in logs: log.close()
        node.destroy_node(); rclpy.shutdown()
    return 0 if state['phase']=='stopped' and not state.get('cleanup_errors') else 3

if __name__ == '__main__':
    raise SystemExit(main())
