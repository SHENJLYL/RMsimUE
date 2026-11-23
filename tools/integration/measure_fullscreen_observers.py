"""Measure owned simulator windows under identical UE fullscreen conditions on Windows."""

import argparse
import ctypes
from ctypes import wintypes
from itertools import groupby
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
COVERAGE = re.compile(r'covered=(\d) paused=(\d) updates=(\d+) draws=(\d+)')


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def observe(run, directory, single_view, *, viewer_enabled, reexpose=True):
    user = ctypes.windll.user32
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]

    def foreground_window():
        window = user.GetForegroundWindow()
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(window, ctypes.byref(pid))
        rect = wintypes.RECT()
        size = [rect.right, rect.bottom] if user.GetClientRect(window, ctypes.byref(rect)) else None
        return pid.value, size

    def focus(pid):
        subprocess.run(['powershell', '-NoProfile', '-Command',
                        f'(New-Object -ComObject WScript.Shell).AppActivate({int(pid)})'],
                       capture_output=True, timeout=8, check=False, creationflags=subprocess.CREATE_NO_WINDOW)

    pids = {}
    deadline = time.monotonic() + 180
    while run.poll() is None and time.monotonic() < deadline:
        if (directory/'status.json').exists():
            state = read_json(directory/'status.json')
            pids = state['pids']
            if state['phase'] == 'ready' and pids.get('ue') and (single_view or pids.get('rviz')):
                break
        time.sleep(.5)
    else:
        raise RuntimeError('Owned console did not reach readiness')
    if bool(pids.get('mujoco_viewer')) != viewer_enabled:
        raise RuntimeError('MuJoCo viewer process does not match the selected mode')
    print(directory.parent.name, 'ready', flush=True)

    rviz_log = directory/'rviz.log'
    if not single_view:
        deadline = time.monotonic() + 15
        while run.poll() is None and time.monotonic() < deadline:
            if rviz_log.is_file() and COVERAGE.search(rviz_log.read_text(errors='replace')):
                break
            time.sleep(.25)

    def measure(seconds, role):
        focus(pids[role])
        samples = []
        for _ in range(seconds):
            if run.poll() is not None:
                raise RuntimeError('Owned console exited while sampling')
            state = read_json(directory/'status.json')
            bridge = state.get('bridge') or {}
            rendering = bridge.get('rendering') or {}
            gpu = (rendering.get('engine_timing') or {}).get('gpu_ms')
            rviz = COVERAGE.findall(rviz_log.read_text(errors='replace')) if rviz_log.is_file() else []
            foreground_pid, size = foreground_window()
            samples.append(dict(wall=time.monotonic(), focus=foreground_pid == pids[role],
                                foreground_pixels=size, required_pixels=[2560,1440] if role=='ue' else None,
                                epoch=bridge.get('epoch'),
                                clock_ns=state.get('clock_ns'), scene_frames=rendering.get('scene_rendered_frames'),
                                gpu_ms=gpu, rviz=dict(zip(('covered','paused','updates','draws'), map(int, rviz[-1]))) if rviz else None))
            time.sleep(1)
        return samples

    covered = measure(24, 'ue')
    return dict(fullscreen=covered, reexposed=measure(7, 'rviz') if reexpose and not single_view else [])


def summarize(samples):
    measurement = samples[3:]
    segments = [list(rows) for focused,rows in groupby(measurement,lambda row:
        (row.get('epoch'),) if row['focus'] and row['scene_frames'] is not None and row['clock_ns'] is not None
        and (not row.get('required_pixels') or row['foreground_pixels']==row['required_pixels']) else None) if focused is not None]
    active = max(segments,key=len,default=[])
    clock_monotonic = all(a['clock_ns']<=b['clock_ns'] for a,b in zip(active,active[1:]))
    seconds = active[-1]['wall']-active[0]['wall'] if len(active)>1 and clock_monotonic else None
    gpu = [row['gpu_ms'] for row in active if row['gpu_ms'] is not None]
    rviz = [row['rviz'] for row in active if row['rviz'] is not None]
    return dict(all_samples_focused=len(active)==len(measurement), focused_samples=len(active), elapsed_s=seconds,
                ue_draw_hz=(active[-1]['scene_frames']-active[0]['scene_frames'])/seconds if seconds else None,
                simulation_rate=(active[-1]['clock_ns']-active[0]['clock_ns'])/1e9/seconds if seconds else None,
                gpu_ms_median=statistics.median(gpu) if gpu else None,
                rviz_covered_samples=sum(row['covered'] for row in rviz), rviz_paused_samples=sum(row['paused'] for row in rviz),
                rviz_draws_delta=rviz[-1]['draws']-rviz[0]['draws'] if len(rviz)>1 else None,
                rviz_updates_delta=rviz[-1]['updates']-rviz[0]['updates'] if len(rviz)>1 else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--viewer-ab', action='store_true', help='Compare the former three-window default with UE + RViz')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    baseline_flags = ['powershell','-NoProfile','-File', ROOT/'tools/environment/Start-NavigationConsole.ps1',
                      '-Fullscreen','-Width','2560','-Height','1440','-ViewportHz','120','-RunSeconds','55']
    summary = {}
    modes = ('three-covered-paused', 'two-no-viewer') if args.viewer_ab else ('three-covered-active', 'three-covered-paused', 'ue-only')
    for mode in modes:
        out = args.output/mode
        out.mkdir()
        session = out/'session'
        overrides = dict(os.environ, RMSIM_RVIZ_COVERAGE_TIMING='1')
        for key in ('GIT_CONFIG_COUNT','RMSIM_RVIZ_RENDER_COVERED_DIAGNOSTIC','RMSIM_VIEWER_RENDER_COVERED_DIAGNOSTIC'):
            overrides.pop(key, None)
        if mode == 'three-covered-active':
            overrides['RMSIM_RVIZ_RENDER_COVERED_DIAGNOSTIC'] = '1'
            overrides['RMSIM_VIEWER_RENDER_COVERED_DIAGNOSTIC'] = '1'
        single_view = mode == 'ue-only'
        viewer_enabled = mode.startswith('three-')
        flags = baseline_flags+['-OutputDirectory',session]+(['-NoRviz'] if single_view else [])
        if viewer_enabled:
            flags.append('-MujocoViewer')
        with (out/'launcher.log').open('wb') as stream:
            run = subprocess.Popen(list(map(str, flags)), cwd=ROOT, env=overrides, stdout=stream,
                                   stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                measurements = observe(run, session, single_view, viewer_enabled=viewer_enabled, reexpose=not args.viewer_ab)
                report = {name:summarize(rows) for name,rows in measurements.items() if rows}
                (out/'samples.json').write_text(json.dumps(measurements,indent=2)+'\n',encoding='utf-8')
            except (OSError, ValueError, RuntimeError) as error:
                report = dict(error=str(error))
            finally:
                if session.is_dir() and run.poll() is None:
                    (session/'stop.request').touch()
                report['exit'] = run.wait(timeout=220)
        summary[mode] = report
        print(mode, json.dumps(report), flush=True)
        (args.output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')


if __name__ == '__main__':
    main()
