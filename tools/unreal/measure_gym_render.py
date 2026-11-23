"""Profile RMUC objects, or trial an Unreal shadow optimization, without changing its defaults."""

import argparse
from collections import defaultdict
import csv
import hashlib
from itertools import islice
import json
from pathlib import Path
import re
import shutil
import statistics
import subprocess

ROOT = Path(__file__).resolve().parents[2]
PROFILES = {
    'reference': [], 'reference_repeat': [], 'fence_glass': ['-GymRenderIsolation=fence_glass'],
    'sun_shadows': ['-GymRenderIsolation=sun_shadows'], 'rect_all': ['-GymRenderIsolation=rect_all'],
    'csm_scissor': ['-ExecCmds=r.Shadow.CSMScissorOptim 1'],
    'csm_cache': ['-ExecCmds=r.Shadow.CSMCaching 1,r.Shadow.CSMScrollingOverlapAreaThrottle 1'],
    'csm_scroll': ['-ExecCmds=r.Shadow.CSMCaching 1,r.Shadow.CSMScrollingOverlapAreaThrottle 0.75'],
    'clustered_lighting': ['-ExecCmds=r.UseClusteredDeferredShading_ToBeRemoved 1'],
    'tsr_async': ['-ExecCmds=r.TSR.AsyncCompute 3'],
    'tsr_history_100': ['-ExecCmds=r.TSR.History.ScreenPercentage 100'],
    'translucency_80': ['-ExecCmds=r.SeparateTranslucencyScreenPercentage 80'],
    'lumen_graphics_queue': ['-ExecCmds=r.Lumen.AsyncCompute 0'],
    'translucency_volume_off': ['-ExecCmds=r.TranslucencyLightingVolume 0'],
    'tsr_75': ['-ExecCmds=r.ScreenPercentage 75'],
    'tsr_50': ['-ExecCmds=r.ScreenPercentage 50'],
    'tsr_50_original_flicker': ['-ExecCmds=r.ScreenPercentage 50,r.TSR.ShadingRejection.Flickering.Period 3,r.TSR.History.SampleCount 16,r.TSR.ShadingRejection.SampleCount 2'],
    'tsr_50_period4_only': ['-ExecCmds=r.ScreenPercentage 50,r.TSR.History.SampleCount 16,r.TSR.ShadingRejection.SampleCount 2'],
    'tsr_50_stable_history': ['-ExecCmds=r.ScreenPercentage 50,r.TSR.History.SampleCount 24,r.TSR.ShadingRejection.SampleCount 3'],
    'tsr_50_filtered_mips': ['-ExecCmds=r.ScreenPercentage 50,r.ViewTextureMipBias.Offset 0.5,r.TSR.History.SampleCount 16,r.TSR.ShadingRejection.SampleCount 2'],
    **{f'rect_{i}': [f'-GymRenderIsolation=rect_{i}'] for i in range(1,13)},
}
EVENTS = {'SceneRender - ViewFamilies','Translucency','RenderDeferredLighting','ShadowDepths','BasePass','PostProcessing'}
TIME = re.compile(r'\b([\d.]+) ms\s*$')


def pass_summary(log):
    frames = defaultdict(dict)
    frame = None
    for line in log.splitlines():
        if 'LogRHI: Display:' not in line:
            continue
        columns = line.rsplit('┃',3)
        if len(columns)!=4:
            continue
        match = TIME.search(columns[-3])
        if not match:
            continue
        milliseconds, event = match[1], columns[-2].strip()
        if event.startswith('Frame ') and ' (CsvFrame ' in event:
            frame = int(event.split()[1])
        if frame is not None and event in EVENTS:
            frames[frame][event] = max(frames[frame].get(event,0.0),float(milliseconds))
    valid = {frame:passes for frame,passes in frames.items() if passes.get('BasePass',0)>0.1}
    return dict(rendered_gpu_frames=len(valid), median_ms={key:statistics.median(
        [passes.get(key,0.0) for passes in valid.values()]) for key in sorted(EVENTS)} if valid else {}, frames=valid)


def pixel_difference(reference, candidate):
    from PIL import Image, ImageChops

    with Image.open(reference) as first, Image.open(candidate) as second:
        first, second = first.convert('RGB'), second.convert('RGB')
        if first.size != second.size:
            raise ValueError('Compared screenshots have different render sizes')
        channels = ImageChops.difference(first, second).histogram()
        return dict(size=first.size, mean_absolute_rgb=sum((i%256)*count for i,count in enumerate(channels))/sum(channels),
                    fraction_channels_above_8=sum(count for i,count in enumerate(channels) if i%256>8)/sum(channels))


def frame_timing(log, editor, output, orbit):
    match = re.search(r'Capture Ended\. Writing CSV to file : (.+)',log)
    if not match:
        return {}
    reported = Path(match[1].strip())
    source = next((path for path in (reported,editor.parent/reported,ROOT/reported) if path.is_file()),None)
    if not source:
        return {}
    shutil.copyfile(source,output)
    csv.field_size_limit(16*1024*1024)  # UE's boot frame can contain hundreds of kilobytes of startup events.
    with output.open(encoding='utf-8-sig',newline='') as handle:
        # Stop before the metadata footer.
        frames = list(islice(csv.DictReader(handle),213))
    windows = {'settled':frames[92:126]}
    if orbit:
        windows['moving'] = frames[175:213]
    result = {}
    for phase,rows in windows.items():
        timings = {}
        for column in ('FrameTime','GPUTime','GameThreadTime','RenderThreadTime'):
            samples = [float(row[column]) for row in rows if row.get(column) and float(row[column])>0]
            if samples:
                timings[column] = statistics.median(samples)
        result[phase] = dict(frame_count=len(rows),median_ms=timings)
    return result


def main():
    from PIL import Image

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--engine-root', type=Path, default=Path('E:/simulator-tools/UE_5.8'))
    parser.add_argument('--map-file', type=Path, default=ROOT/'assets/maps/rmuc2027/map.json')
    parser.add_argument('--cloud-time', type=float, help='Freeze the school cloud phase for same-view comparisons')
    parser.add_argument('--orbit', action='store_true', help='Move the existing chase camera along a fixed frame-indexed orbit')
    parser.add_argument('--settled-sample', action='store_true', help='Also capture fixed-view GPU passes and pixels before the orbit')
    parser.add_argument('--temporal-capture', action='store_true', help='Capture consecutive frames at the driver view with lighting frozen')
    parser.add_argument('--mode', nargs='+', choices=PROFILES,
                        default=['reference','reference_repeat','fence_glass','sun_shadows','rect_all','csm_scissor','csm_cache'])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    editor = args.engine_root/'Engine/Binaries/Win64/UnrealEditor.exe'
    project = ROOT/'unreal/RMSim/RMSim.uproject'
    module_hash = hashlib.sha256((project.parent/'Binaries/Win64/UnrealEditor-RMSim.dll').read_bytes()).hexdigest()
    map_file = args.map_file.resolve(strict=True)
    summary = dict(module_sha256=module_hash, map_file=str(map_file), scene_capture='main viewport rendered offscreen',
                   requested_resolution=[2560,1440], orbit=args.orbit, cases={})
    for name in args.mode:
        case = (args.output/name).resolve()
        case.mkdir()
        screenshot = case/'view.png'
        runtime = case/'runtime.log'
        events = ['80:r.SetRes 2560x1440w','100:r.ProfileGPU.ShowUI 0']+[f'{frame}:ProfileGPU' for frame in range(220,230)]
        if args.temporal_capture:
            events.extend(f'{frame}:Shot -nosuffix filename={(case/f"temporal-{frame}.png").as_posix()}'
                          for frame in range(174,188))
        if args.settled_sample:
            events.extend(f'{frame}:ProfileGPU' for frame in range(130,140))
            events.append(f'145:HighResShot 1 filename={(case/"settled.png").as_posix()}')
        events.append(f'260:HighResShot 1 filename={screenshot.as_posix()}')
        if args.orbit:
            events.append(f'340:HighResShot 1 filename={(case/"later.png").as_posix()}')
        command = [str(editor),str(project),'/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode',
                   '-game','-RenderOffScreen','-unattended','-NoSplash','-NoSound','-NoVSync',
                   '-ResX=2560','-ResY=1440','-ForceRes','-CustomConfig=RMSimGymLumen','-d3d12','-sm6',
                   '-PhysicsBackend=mujoco','-SimStepSeconds=0.005','-SimRealTimePace','-RMSimViewportHz=120',
                   '-MeshRigFile='+str(ROOT/'assets/vehicles/aim_sentry/ue_mesh_rig.json'),
                   '-MapFile='+str(map_file),
                   '-MujocoModel='+str(ROOT/'assets/vehicles/aim_sentry/mujoco/rmuc2027/manifest.json'),
                   '-ProfileGymRenderRig','-LogCmds=LogRMSim Verbose','-csvCaptureFrames=375','-ExitAfterCsvProfiling',
                   '-csvCompression=0','-csvExecCmds='+','.join(events),
                   '-abslog='+str(runtime),*PROFILES[name]]
        if args.orbit:
            command.append('-GymRenderOrbit')
        if args.cloud_time is not None:
            command.append(f'-GymCloudTime={args.cloud_time}')
        if args.temporal_capture:
            command.extend(['-GymReviewCamera','-GymCameraPosition=-8,6,1.15',
                            '-GymCameraTarget=8,-3,2','-GymCameraFov=78','-GymCloudTime=0'])
        with (case/'process.log').open('wb') as stream:
            run = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, timeout=120,
                                 creationflags=subprocess.CREATE_NO_WINDOW, check=False)
        log = runtime.read_text(encoding='utf-8-sig',errors='replace')
        report = dict(exit=run.returncode, screenshot=screenshot.is_file(), **pass_summary(log))
        if args.settled_sample:
            for phase,frames in (('settled',[v for i,v in report['frames'].items() if i<160]),
                                 ('moving',[v for i,v in report['frames'].items() if i>=160])):
                if frames:
                    report[phase+'_profile_median_ms'] = {key:statistics.median(v.get(key,0.) for v in frames) for key in sorted(EVENTS)}
        report['frame_timing'] = frame_timing(log,editor,case/'frames.csv',args.orbit)
        if args.temporal_capture:
            report['temporal_screenshots'] = sorted(p.name for p in case.glob('temporal-*.png'))
        if screenshot.is_file():
            with Image.open(screenshot) as image:
                report['screenshot_resolution']=image.size
        reference = args.output/'reference/view.png'
        if name!='reference' and screenshot.is_file() and reference.is_file():
            report['pixel_difference']=pixel_difference(reference,screenshot)
            later = case/'later.png'
            if args.orbit and later.is_file() and reference.with_name('later.png').is_file():
                report['later_pixel_difference']=pixel_difference(reference.with_name('later.png'),later)
            if args.settled_sample and (case/'settled.png').is_file() and reference.with_name('settled.png').is_file():
                report['settled_pixel_difference']=pixel_difference(reference.with_name('settled.png'),case/'settled.png')
        if name=='reference':
            summary['rig']=[line.split('RMSIM_GYM_GPU_OBJECT ',1)[1] for line in log.splitlines()
                            if 'RMSIM_GYM_GPU_OBJECT ' in line]
        summary['cases'][name]=report
        (args.output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
        print(name, run.returncode, report['median_ms'], 'pixels',report.get('pixel_difference'), flush=True)
    if any(case['exit'] or not case['screenshot'] or not case['rendered_gpu_frames'] or not case['frame_timing']
           or (args.temporal_capture and len(case['temporal_screenshots'])!=14)
           for case in summary['cases'].values()):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
