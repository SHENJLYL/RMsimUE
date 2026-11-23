"""Offline launcher contracts: real file parsing, simulated owned processes, no GUI/ROS."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import time
import unittest
from unittest.mock import patch


def module(name, **items):
    result = types.ModuleType(name)
    result.__dict__.update(items)
    return result


def load_console(source):
    fake = {
        'navigation_console_config': module('navigation_console_config', ROOT=Path('.'),
            prepare_scene=None, rviz_configuration=None,
            digest=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest(),
            atomic_json=None, copy_runtime_arena=None),
        'runtime_identity': module('runtime_identity', capture_pointlio_inputs=None),
        'platform_cpu_policy': module('platform_cpu_policy', apply_owned_process_policy=None,
            prepare_ue_worker_budget=None, worker_budget_digest=None),
    }
    spec = importlib.util.spec_from_file_location('console_under_test', source)
    console = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, fake):
        spec.loader.exec_module(console)
    return console


CONSOLE = load_console(Path(__file__).with_name('navigation_console.py'))


def renderer_identity():
    return dict(receipt_sha256='5'*64, build_manifest_sha256='6'*64,
                gl_verification_sha256='7'*64, viewer_dll_sha256='8'*64, source_sha256='9'*64)


def renderer_cache(*, displayed=True):
    return dict(schema='rmsim_static_flex_vbo_v1', version='rmsim.viewer-static-flex-vbo.1',
                requested=True, available=True, enabled=True, failures=0, rejected_contexts=0,
                live_flex=14 if displayed else 0, live_bytes=231624000 if displayed else 0,
                fast_batches=14 if displayed else 0)


class HelperContracts(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.path = self.root/'status.json'
        self.expected = dict(port=42001, session_token_sha256='1'*64,
                             manifest_sha256='2'*64, model_sha256='3'*64,
                             physics_identity_sha256='4'*64,
                             renderer_runtime_identity=renderer_identity())
        self.status = dict(schema='rmsim_mujoco_viewer_status_v1', pid=123,
                           phase='ready', bound=True, frames=1, body_pose_matches=True,
                           status_written_monotonic_s=100., generation=1, sequence=8,
                           sim_time_s=.05, renderer_cache=renderer_cache(),
                           compiled_model_counts={'nflex':14}, **self.expected)

    def read(self, value=None, now=100.25):
        self.path.write_text(json.dumps(self.status if value is None else value), encoding='utf-8')
        return CONSOLE.read_mujoco_viewer_status(self.path, self.expected, 123, now)

    def test_default_visible_native_mode_skips_viewer(self):
        self.assertFalse(CONSOLE.mujoco_viewer_enabled(types.SimpleNamespace(
            physics_backend='mujoco', no_rviz=False, offscreen=False, mujoco_viewer=False)))

    def test_visible_native_mode_can_opt_into_viewer(self):
        self.assertTrue(CONSOLE.mujoco_viewer_enabled(types.SimpleNamespace(
            physics_backend='mujoco', no_rviz=False, offscreen=False, mujoco_viewer=True)))

    def test_headless_and_explicit_chaos_modes_do_not_enable_viewer(self):
        for backend, no_rviz, offscreen in [('mujoco', True, False), ('mujoco', True, True),
                                           ('mujoco', False, True), ('chaos', False, False)]:
            with self.subTest(backend=backend, no_rviz=no_rviz, offscreen=offscreen):
                self.assertFalse(CONSOLE.mujoco_viewer_enabled(types.SimpleNamespace(
                    physics_backend=backend, no_rviz=no_rviz, offscreen=offscreen, mujoco_viewer=True)))

    def test_matching_verified_frame_is_ready_and_fresh(self):
        self.assertTrue(self.read()['status_fresh'])

    def test_old_heartbeat_is_observable_as_not_fresh(self):
        self.assertFalse(self.read(now=102.01)['status_fresh'])

    def test_loading_can_precede_manifest_initialization(self):
        status = dict(self.status, phase='loading', bound=False, frames=0, body_pose_matches=False)
        for key in ('manifest_sha256', 'model_sha256', 'physics_identity_sha256'):
            status[key] = None
        self.assertEqual(self.read(status)['phase'], 'loading')

    def test_waiting_bound_model_needs_no_native_frame_yet(self):
        self.assertEqual(self.read(dict(self.status, phase='waiting', frames=0, body_pose_matches=False,
                                       renderer_cache=renderer_cache(displayed=False)))['phase'], 'waiting')

    def test_ready_requires_complete_enabled_cache_and_validated_runtime(self):
        for updates in ({'enabled':False}, {'available':False}, {'requested':False},
                        {'schema':'other'}, {'version':'other'}, {'failures':1},
                        {'rejected_contexts':1}, {'live_flex':13}, {'live_bytes':0}, {'fast_batches':0}):
            with self.subTest(updates=updates), self.assertRaisesRegex(ValueError, 'renderer cache'):
                self.read(dict(self.status, renderer_cache={**renderer_cache(), **updates}))
        identity=renderer_identity(); identity['viewer_dll_sha256']='f'*64
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            self.read(dict(self.status, renderer_runtime_identity=identity))
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.read(dict(self.status, renderer_runtime_identity=None))

    def test_generation_changes_do_not_reject_same_session(self):
        self.assertEqual(self.read(dict(self.status, generation=2, sequence=9, sim_time_s=0.))['generation'], 2)

    def test_missing_or_temporarily_locked_status_defers(self):
        self.assertIsNone(CONSOLE.read_mujoco_viewer_status(self.path, self.expected, 123, 100.))
        self.path.write_text('{}')
        with patch.object(Path, 'read_text', side_effect=PermissionError('sharing violation')):
            self.assertIsNone(CONSOLE.read_mujoco_viewer_status(self.path, self.expected, 123, 100.))

    def test_status_size_is_bounded(self):
        self.path.write_text(' '*65537)
        with self.assertRaisesRegex(ValueError, '64 KiB'):
            CONSOLE.read_mujoco_viewer_status(self.path, self.expected, 123, 100.)

    def test_viewer_environment_excludes_ros_and_conda_paths(self):
        python = self.root/'venv/Scripts/python.exe'
        python.parent.mkdir(parents=True)
        python.write_bytes(b'fixture')
        home = self.root/'base-python'; home.mkdir()
        (python.parent.parent/'pyvenv.cfg').write_text(
            f'home = {home}\ninclude-system-site-packages = false\n')
        inherited = {'Path':'bad-ros-path','PYTHONPATH':'bad-python','PYTHONHOME':'bad-home',
                     'ROS_DOMAIN_ID':'71','RMW_IMPLEMENTATION':'bad-rmw','AMENT_PREFIX_PATH':'bad-ament',
                     'CONDA_PREFIX':'bad-conda','COLCON_PREFIX_PATH':'bad-colcon','QT_PLUGIN_PATH':'bad-qt',
                     'SystemRoot':str(self.root/'Windows'),'TEMP':'kept-temp','USERPROFILE':'kept-user'}
        env, policy = CONSOLE.mujoco_viewer_environment(python, inherited)
        self.assertEqual(env['TEMP'], 'kept-temp')
        self.assertEqual(env['USERPROFILE'], 'kept-user')
        self.assertEqual(env['PYTHONNOUSERSITE'], '1')
        self.assertFalse(any('bad-' in value for value in env.values()))
        self.assertFalse(policy['inherits_ros_python_or_dll_paths'])
        self.assertIn(str(home), policy['path'])

    def test_display_defaults_preserve_visible_and_headless_arguments(self):
        for offscreen, expected in ((False, ['-windowed','-ResX=1120','-ResY=860','-WinX=20','-WinY=40']),
                                    (True, ['-RenderOffScreen','-unattended','-ResX=640','-ResY=360'])):
            request, flags = CONSOLE.ue_display_configuration(types.SimpleNamespace(
                fullscreen=False, offscreen=offscreen, width=None, height=None))
            self.assertEqual(flags, expected)
            self.assertFalse(request['actual_client_size_verified'])
            self.assertFalse(request['width_explicit'])

    def test_display_fullscreen_uses_explicit_pixels_without_window_position(self):
        request, flags = CONSOLE.ue_display_configuration(types.SimpleNamespace(
            fullscreen=True, offscreen=False, width=2560, height=1440))
        self.assertEqual(flags, ['-fullscreen','-ResX=2560','-ResY=1440'])
        self.assertEqual((request['mode'],request['width_pixels'],request['height_pixels']),
                         ('fullscreen',2560,1440))
        self.assertTrue(request['width_explicit'] and request['height_explicit'])

    def test_display_invalid_dimensions_and_headless_fullscreen_are_rejected(self):
        for full, off, width, height in ((True,True,None,None),(False,False,0,860),
                                         (False,False,1120,16385),(False,False,True,860)):
            with self.subTest(full=full,off=off,width=width,height=height), self.assertRaises(ValueError):
                CONSOLE.ue_display_configuration(types.SimpleNamespace(
                    fullscreen=full,offscreen=off,width=width,height=height))

    def optimized_fixture(self):
        python = self.root/'artifacts/environments/mujoco-4.0.1-viewer-staticflex-vbo.1/Scripts/python.exe'
        python.parent.mkdir(parents=True); python.write_bytes(b'fixture interpreter')
        home = self.root/'base-python'; home.mkdir()
        (python.parent.parent/'pyvenv.cfg').write_text(
            f'home = {home}\ninclude-system-site-packages = false\n')
        for name in ('live_viewer.py','viewer_protocol.py'):
            path=self.root/'tools/mujoco'/name; path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text('# source fixture\n')
        dll=python.parent.parent/'Lib/site-packages/mujoco/mujoco.dll'
        dll.parent.mkdir(parents=True); dll.write_bytes(b'fixture renderer overlay')
        identity=renderer_identity(); identity['viewer_dll_sha256']=CONSOLE.digest(dll)
        source=self.root/'tools/mujoco/live_viewer.py'
        identity['source_sha256']=CONSOLE.digest(source)
        runtime=dict(python=str(python),dll_path=str(dll),environment=str(python.parent.parent),
                     runtime_identity=identity,source_sha256={
                         'tools/mujoco/'+name:CONSOLE.digest(source.with_name(name))
                         for name in ('live_viewer.py','viewer_protocol.py')},
                     build_manifest={'path':'fixture-build.json','sha256':identity['build_manifest_sha256']},
                     gl_verification={'path':'fixture-gl.json','sha256':identity['gl_verification_sha256']},
                     receipt={'path':'fixture-receipt.json','sha256':identity['receipt_sha256']})
        source.with_name('viewer_runtime.py').write_text(
            'import json\ndef validate_optimized_viewer_runtime(repo_root, viewer_script):\n'
            '    return json.loads('+repr(json.dumps(runtime))+')\n',encoding='utf-8')
        dependencies=dict(runtime={'dll_path':str(dll),'dll_sha256':CONSOLE.digest(dll)})
        return source,runtime,dependencies

    def test_viewer_plan_uses_validated_overlay_and_forwards_explicit_diagnostics(self):
        source,runtime,dependencies=self.optimized_fixture()
        manifest=self.root/'manifest.json'; manifest.write_text('{}')
        model=dict(model_sha256='3'*64,physics_identity_sha256='4'*64)
        for timing,continuous in ((False,False),(True,False),(False,True),(True,True)):
            out=self.root/f'output-{timing}-{continuous}'; out.mkdir()
            with patch.object(CONSOLE,'ROOT',self.root), patch.object(CONSOLE.subprocess,'run',
                    return_value=types.SimpleNamespace(returncode=0,stdout=json.dumps(dependencies),stderr='')):
                plan=CONSOLE.prepare_mujoco_viewer(manifest,model,out,{},
                      timing_diagnostic=timing,continuous_render_diagnostic=continuous,
                      pause_when_covered=not continuous)
            self.assertEqual('--timing-diagnostic' in plan['command'],timing)
            self.assertEqual('--continuous-render-diagnostic' in plan['command'],continuous)
            self.assertEqual(plan['provenance']['timing_diagnostic'],timing)
            self.assertEqual(plan['provenance']['continuous_render_diagnostic'],continuous)
            self.assertEqual(plan['expected']['model_sha256'],model['model_sha256'])
            self.assertEqual(plan['command'][0],runtime['python'])
            self.assertIn('--static-flex-gpu-cache',plan['command'])
            self.assertEqual('--occluder-pid-file' in plan['command'],os.name=='nt' and not continuous)
            self.assertEqual(plan['expected']['renderer_runtime_identity'],runtime['runtime_identity'])
            self.assertEqual(plan['provenance']['renderer_runtime'],runtime)
            self.assertTrue(plan['provenance']['static_flex_gpu_cache'])
            self.assertEqual(plan['provenance']['source_sha256']['tools/mujoco/viewer_runtime.py'],
                             CONSOLE.digest(source.with_name('viewer_runtime.py')))

    def test_pending_or_missing_validation_stops_before_dependency_process_or_session_creation(self):
        source,runtime,_=self.optimized_fixture()
        manifest=self.root/'manifest.json'; manifest.write_text('{}')
        output=self.root/'output'; output.mkdir()
        source.with_name('viewer_runtime.py').write_text(
            'def validate_optimized_viewer_runtime(repo_root, viewer_script):\n'
            '    raise ValueError("renderer GL validation is pending")\n')
        with patch.object(CONSOLE,'ROOT',self.root), patch.object(CONSOLE.subprocess,'run') as run:
            with self.assertRaisesRegex(ValueError,'GL validation is pending'):
                CONSOLE.prepare_mujoco_viewer(manifest,{},output,{})
            run.assert_not_called()
        self.assertFalse((output/'mujoco-viewer').exists())
        source.with_name('viewer_runtime.py').unlink()
        with patch.object(CONSOLE,'ROOT',self.root), self.assertRaisesRegex(RuntimeError,'runtime validator'):
            CONSOLE.optimized_mujoco_viewer_runtime(source)

    def test_dependency_probe_rejects_wrong_or_missing_loaded_dll(self):
        source,runtime,dependencies=self.optimized_fixture()
        for loaded in (None, {}, {**dependencies['runtime'],'dll_sha256':'f'*64},
                       {**dependencies['runtime'],'dll_path':str(self.root/'foreign.dll')}):
            with self.subTest(loaded=loaded), patch.object(CONSOLE.subprocess,'run',
                    return_value=types.SimpleNamespace(returncode=0,stdout=json.dumps({'runtime':loaded}),stderr='')):
                with self.assertRaisesRegex(RuntimeError,'validated renderer overlay'):
                    CONSOLE.check_mujoco_viewer_dependencies(Path(runtime['python']),source,{},runtime)

    @unittest.skipUnless(sys.platform == 'win32' and Path(r'D:\simulator\artifacts\environments\mujoco-4.0.1-viewer-staticflex-vbo.1\Scripts\python.exe').is_file(),
                         'Requires the built Windows overlay; imports only, no GUI or ROS nodes')
    def test_real_windows_overlay_loaded_dll_identity_without_gui(self):
        root=Path(r'D:\simulator')
        manifest=root/'artifacts/dependencies/mujoco/install-4.0.1-viewer-staticflex-vbo.1/rmsim-viewer-build-manifest.json'
        build=json.loads(manifest.read_text(encoding='utf-8-sig'))
        python=Path(build['viewer_python'])
        runtime=dict(dll_path=str(python.parent.parent/'Lib/site-packages/mujoco/mujoco.dll'),
                     runtime_identity={'viewer_dll_sha256':build['viewer_dll_sha256']})
        env,_=CONSOLE.mujoco_viewer_environment(python,dict(os.environ))
        report=CONSOLE.check_mujoco_viewer_dependencies(python,root/'tools/mujoco/live_viewer.py',env,runtime)
        self.assertEqual(report['runtime']['dll_sha256'],build['viewer_dll_sha256'])
        self.assertEqual(report['versions']['mujoco'],'4.0.1')
        if os.environ.get('RMSIM_VIEWER_OVERLAY_IMPORT_EVIDENCE'):
            Path(os.environ['RMSIM_VIEWER_OVERLAY_IMPORT_EVIDENCE']).write_text(json.dumps({
                'schema':'rmsim_viewer_overlay_import_contract_v1','passed':True,
                'scope':'actual isolated imports and loaded DLL identity; no GLFW initialization or GL validation claim',
                'build_manifest_sha256':CONSOLE.digest(manifest),'dependencies':report,
                'gui_or_ros_nodes_launched':False},indent=2)+'\n',encoding='utf-8')

    @unittest.skipUnless(sys.platform == 'win32' and Path(r'D:\simulator\artifacts\environments\mujoco-4.0.1-viewer-staticflex-vbo.1\rmsim-viewer-runtime.json').is_file(),
                         'Requires the final validated Windows renderer receipt; creates no viewer window')
    def test_real_windows_validated_overlay_plan_without_gui(self):
        root=Path(r'D:\simulator')
        manifest=root/'assets/vehicles/aim_sentry/mujoco/rmuc2027/manifest.json'
        model=json.loads(manifest.read_text(encoding='utf-8-sig'))
        output=self.root/'real-validated-plan'; output.mkdir()
        with patch.object(CONSOLE,'ROOT',root):
            plan=CONSOLE.prepare_mujoco_viewer(manifest,model,output,dict(os.environ))
        self.assertIn('--static-flex-gpu-cache',plan['command'])
        identity=plan['expected']['renderer_runtime_identity']
        self.assertEqual(plan['provenance']['dependencies']['runtime']['dll_sha256'],identity['viewer_dll_sha256'])
        self.assertEqual(plan['provenance']['renderer_runtime']['runtime_identity'],identity)
        self.assertFalse(plan['status_file'].exists(),'no viewer process was started')
        if os.environ.get('RMSIM_VIEWER_OVERLAY_PLAN_EVIDENCE'):
            Path(os.environ['RMSIM_VIEWER_OVERLAY_PLAN_EVIDENCE']).write_text(json.dumps({
                'schema':'rmsim_viewer_overlay_plan_contract_v1','passed':True,
                'scope':'complete file/receipt/import preflight; no viewer/UE/ROS process or GL initialization',
                'provenance':plan['provenance'],'expected':plan['expected'],
                'gui_or_ros_nodes_launched':False},indent=2)+'\n',encoding='utf-8')

    @unittest.skipUnless(sys.platform == 'win32' and Path(r'D:\simulator\artifacts\environments\mujoco-4.0.1-viewer-staticflex-vbo.1\Scripts\python.exe').is_file(),
                         'Requires the prepared Windows venv redirector; creates no GUI or ROS nodes')
    def test_real_windows_venv_process_binding(self):
        python=Path(r'D:\simulator\artifacts\environments\mujoco-4.0.1-viewer-staticflex-vbo.1\Scripts\python.exe')
        fixture=self.root/'viewer process fixture.py'
        fixture.write_text('''import argparse,json,os,time
from pathlib import Path
p=argparse.ArgumentParser()
for key in ('manifest','port','session-token','status-file','parent-pid'):p.add_argument('--'+key,required=True)
p.add_argument('--static-flex-gpu-cache',action='store_true')
a=p.parse_args();status=Path(a.status_file)
status.write_text(json.dumps({'pid':os.getpid(),'parent_pid':os.getppid()}))
deadline=time.monotonic()+30
while not status.with_suffix('.stop').exists() and time.monotonic()<deadline:time.sleep(.02)
''',encoding='utf-8')
        env,_=CONSOLE.mujoco_viewer_environment(python,dict(os.environ))
        manifest=self.root/'test manifest.json'; manifest.write_text('{}')
        owned=[]; bindings=[]; proof={'schema':'rmsim_real_venv_process_contract_v1','gui_or_ros_nodes_launched':False,'checks':[]}
        def start(name):
            status=self.root/(name+'.json')
            command=[str(python),str(fixture),'--manifest',str(manifest),'--port','42001',
                     '--session-token','0'*32,'--status-file',str(status),'--parent-pid',str(os.getpid()),
                     '--static-flex-gpu-cache']
            process=subprocess.Popen(command,env=env,creationflags=subprocess.CREATE_NO_WINDOW,
                                     stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            owned.append((process,status))
            deadline=time.monotonic()+5
            while not status.exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue(status.exists(),'fixture did not publish its real process identity')
            return process,status,command,json.loads(status.read_text())
        try:
            process,status,command,actual=start('owned')
            self.assertNotEqual(process.pid,actual['pid'],'this contract must exercise an actual Windows venv redirector')
            binding=CONSOLE.bind_mujoco_viewer_process(process,actual['pid'],command);bindings.append(binding)
            self.assertEqual(binding['pid'],actual['pid']);self.assertEqual(binding['launcher_pid'],process.pid)
            self.assertEqual(binding['ancestry'][-1]['pid'],process.pid)
            self.assertIsNone(CONSOLE.mujoco_viewer_exit_code(binding,process))
            proof.update(binding={k:v for k,v in binding.items() if not k.startswith('_')},
                         fixture_parent_pid=actual['parent_pid'])
            proof['checks'].append('real_redirector_descendant_and_exact_command_bound')
            foreign_python=self.root/'foreign-venv/Scripts/python.exe'
            foreign_python.parent.mkdir(parents=True); foreign_python.write_bytes(b'foreign fixture redirector')
            foreign_home=self.root/'unrelated-python'; foreign_home.mkdir()
            (foreign_home/'python.exe').write_bytes(b'foreign fixture image')
            (foreign_python.parent.parent/'pyvenv.cfg').write_text(
                f'home = {foreign_home}\nexecutable = {foreign_python}\ninclude-system-site-packages = false\n')
            wrong_image=list(command); wrong_image[0]=str(foreign_python)
            with self.assertRaisesRegex(ValueError,'unexpected Python executable'):
                CONSOLE.bind_mujoco_viewer_process(process,actual['pid'],wrong_image)
            proof['checks'].append('unrelated_python_image_with_same_basename_rejected')
            wrong_token=list(command);wrong_token[wrong_token.index('--session-token')+1]='f'*32
            with self.assertRaisesRegex(ValueError,'argument'):
                CONSOLE.bind_mujoco_viewer_process(process,actual['pid'],wrong_token)
            proof['checks'].append('wrong_command_token_rejected')
            wrong_source=list(command);wrong_source[1]=str(self.root/'unowned.py')
            with self.assertRaisesRegex(ValueError,'argument'):
                CONSOLE.bind_mujoco_viewer_process(process,actual['pid'],wrong_source)
            proof['checks'].append('wrong_source_script_rejected')
            with self.assertRaisesRegex(ValueError,'process command does not match'):
                CONSOLE.bind_mujoco_viewer_process(process,actual['pid'],command[:-1])
            proof['checks'].append('missing_cache_flag_rejected_by_exact_command_binding')
            sibling,_,_,foreign=start('unrelated')
            with self.assertRaisesRegex(ValueError,'ancestry'):
                CONSOLE.bind_mujoco_viewer_process(process,foreign['pid'],command)
            proof['checks'].append('unrelated_redirector_child_rejected')
            status.with_suffix('.stop').write_text('stop fixture')
            self.assertEqual(process.wait(timeout=5),0)
            self.assertEqual(CONSOLE.mujoco_viewer_exit_code(binding,process),0)
            with self.assertRaisesRegex(ValueError,'exited launcher'):
                CONSOLE.bind_mujoco_viewer_process(process,actual['pid'],command)
            proof['checks'].append('held_child_handle_reports_exit_and_exited_owner_cannot_rebind')
            proof['passed']=True
        finally:
            for process,status in owned:
                status.with_suffix('.stop').write_text('stop fixture')
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True)
                    process.wait(timeout=5)
            for binding in bindings:CONSOLE.close_mujoco_viewer_binding(binding)
            proof['all_fixture_processes_exited']=all(p.poll() is not None for p,_ in owned)
            if os.environ.get('RMSIM_VIEWER_REAL_PID_EVIDENCE'):
                Path(os.environ['RMSIM_VIEWER_REAL_PID_EVIDENCE']).write_text(json.dumps(proof,indent=2)+'\n',encoding='utf-8')


def rejection_test(key, value, pattern):
    def test(self):
        status = dict(self.status); status[key] = value
        with self.assertRaisesRegex(ValueError, pattern):
            self.read(status)
    return test


for name, key, value, pattern in [
    ('wrong_schema', 'schema', 'wrong', 'schema'),
    ('unknown_phase', 'phase', 'running', 'phase'),
    ('foreign_pid', 'pid', 456, 'PID'),
    ('foreign_token', 'session_token_sha256', 'f'*64, 'identity mismatch'),
    ('foreign_model', 'model_sha256', 'f'*64, 'identity mismatch'),
    ('foreign_manifest', 'manifest_sha256', 'f'*64, 'identity mismatch'),
    ('foreign_physics', 'physics_identity_sha256', 'f'*64, 'identity mismatch'),
    ('foreign_port', 'port', 42002, 'identity mismatch'),
    ('missing_identity', 'model_sha256', None, 'incomplete'),
    ('not_bound', 'bound', False, 'bound'),
    ('no_frame', 'frames', 0, 'native frame'),
    ('boolean_frame', 'frames', True, 'native frame'),
    ('body_mismatch', 'body_pose_matches', False, 'native frame'),
    ('future_stamp', 'status_written_monotonic_s', 110., 'timestamp'),
    ('nan_stamp', 'status_written_monotonic_s', float('nan'), 'timestamp'),
    ('negative_stamp', 'status_written_monotonic_s', -1., 'timestamp'),
]:
    setattr(HelperContracts, 'test_reject_'+name, rejection_test(key, value, pattern))


class ProcessFixture:
    def __init__(self, name, pid):
        self.name, self.pid, self.returncode = name, pid, None
    def poll(self): return self.returncode
    def terminate(self): self.returncode = 0
    def kill(self): self.returncode = -1
    def wait(self, timeout=None):
        if self.returncode is None: self.returncode = 0
        return self.returncode


class LifecycleContracts(unittest.TestCase):
    def run_case(self, scenario, headless=False, extra_arguments=(), with_viewer=True):
        viewer_enabled = with_viewer and not headless
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); out = root/'session'
            engine = root/'engine'
            def put(relative, value=b'fixture'):
                path = root/relative; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(value); return path
            model = {'model_sha256':'3'*64,'physics_identity_sha256':'4'*64,'timestep_s':.001}
            manifest = put('assets/vehicles/aim_sentry/mujoco/rmuc2027/manifest.json', json.dumps(model).encode())
            sdk = 'third_party/mujoco-sdk/4.0.1-rmsim-static-world-flex.3/windows-x86_64'
            put(sdk+'/rmsim-build-manifest.json', json.dumps({'runtime_version_string':'4.0.1+rmsim.static-world-flex.3'}).encode())
            for name in [sdk+'/bin/mujoco.dll','unreal/RMSim/Binaries/Win64/mujoco.dll',
                         'unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll',
                         'unreal/RMSim/Config/DefaultEngine.ini','assets/vehicles/aim_sentry/ue_mesh_rig.json',
                         'assets/vehicles/aim_sentry/navigation-follow.json',
                         'assets/vehicles/aim_sentry/sensors/mid360.json',
                         'assets/vehicles/aim_sentry/sensors/mid360-imu.json',
                         'ros2_ws/src/pb2025_nav_bringup/config/simulation/nav2_params.yaml',
                         'artifacts/runtime/rviz/windows/rviz2.exe','tools/environment/pixi.lock',
                         'artifacts/runtime/navigation/windows/loam_interface.dll',
                         'artifacts/runtime/navigation/windows/sensor_scan_generation.dll',
                         'artifacts/runtime/navigation/windows/fake_vel_transform.dll',
                         'tools/integration/platform_cpu_policy.py','tools/integration/runtime_identity.py',
                         'tools/integration/navigation_console_config.py',
                         'engine/Engine/Binaries/Win64/UnrealEditor.exe']:
                put(name)
            scene_file=put('scene.json', b'{}'); map_file=put('map.yaml', b'{}')
            put('installed/rmsim_bridge/__init__.py', b'')
            events=[]; processes={}; commands={}; snapshots=[]; clock=[100.]; spins=[0]; plan_calls=[]
            expected=dict(port=42001,session_token_sha256='1'*64,manifest_sha256=CONSOLE.digest(manifest),**model)
            expected.pop('timestep_s')
            expected['renderer_runtime_identity']=renderer_identity()
            plan=dict(command=['viewer-fixture'], environment={}, expected=expected, token='0'*32,
                      status_file=out/'mujoco-viewer/status.json', provenance={'enabled':True})
            def prepare_viewer(*args, **kwargs):
                plan_calls.append(kwargs); plan['status_file'].parent.mkdir()
                plan['provenance'].update(kwargs)
                return plan
            def save(path, value):
                if path.name=='status.json': snapshots.append(json.loads(json.dumps(value)))
                path.write_text(json.dumps(value)); return True
            def popen(command, **kwargs):
                if command[0]=='viewer-fixture': name='mujoco_viewer'
                elif command[0].endswith('UnrealEditor.exe'): name='ue'
                elif command[0].endswith('rviz2.exe'): name='rviz'
                else: name=command[3].replace('point_lio','pointlio')
                if name=='ue' and viewer_enabled:
                    self.assertEqual(json.loads(plan['status_file'].read_text())['phase'], 'waiting')
                    self.assertIn('-MujocoViewerPort=42001', command)
                    self.assertIn('-MujocoViewerToken='+'0'*32, command)
                if name=='rviz' and viewer_enabled:
                    self.assertEqual(json.loads(plan['status_file'].read_text())['phase'], 'ready')
                events.append(('launch',name))
                commands[name]=list(command)
                p=ProcessFixture(name,1000+len(processes)); processes[name]=p; return p
            def run(command, **kwargs):
                if command[0]=='taskkill':
                    for p in processes.values():
                        if p.pid==int(command[2]): p.terminate()
                return types.SimpleNamespace(returncode=0, stdout='', stderr='')
            subscriptions={}
            class Node:
                def __init__(self,*args): pass
                def create_subscription(self,kind,topic,callback,qos): subscriptions[topic]=callback
                def destroy_node(self): events.append(('node','destroyed'))
            def spin(node, timeout_sec):
                spins[0]+=1; clock[0]+=.1
                if spins[0]>100: raise AssertionError('Unbounded fixture loop')
                if 'mujoco_viewer' in processes:
                    phase='ready' if 'ue' in processes else 'waiting'
                    if scenario=='early_close': phase='closed'
                    if scenario in ('viewer_error','viewer_exit_reason'): phase='error'
                    if scenario=='blocked_startup': phase='loading'; clock[0]+=60
                    status=dict(schema='rmsim_mujoco_viewer_status_v1', pid=processes['mujoco_viewer'].pid,
                        phase=phase,bound=phase!='loading',frames=int(phase=='ready'),body_pose_matches=phase=='ready',
                        status_written_monotonic_s=clock[0],renderer_cache=renderer_cache(displayed=phase=='ready'),
                        compiled_model_counts={'nflex':14},**expected)
                    if phase=='error':
                        if scenario=='viewer_exit_reason': status['exit_reason']='fixture camera signature failure'
                        else: status['error']='fixture viewer failure'
                    if scenario=='identity_failure': status['model_sha256']='f'*64
                    if scenario=='renderer_identity_failure':
                        status['renderer_runtime_identity']={**renderer_identity(),'receipt_sha256':'f'*64}
                    if scenario=='cache_failure': status['renderer_cache']['failures']=1
                    if scenario=='cache_incomplete' and phase=='ready': status['renderer_cache']['live_flex']=13
                    if 'rviz' in processes and scenario=='viewer_crash': processes['mujoco_viewer'].returncode=17
                    if 'rviz' in processes and scenario=='viewer_close': status['phase']='closed'
                    if 'rviz' in processes and scenario=='stale_then_stop':
                        status['phase']='stale'
                        if any(s.get('phase')=='recovering' for s in snapshots):
                            (out/'stop.request').write_text('fixture stop')
                    plan['status_file'].write_text(json.dumps(status))
                if 'ue' in processes:
                    subscriptions['/rmsim/aim_sentry/navigation'](types.SimpleNamespace(data='{"phase":"running"}'))
                    subscriptions['/map'](types.SimpleNamespace(header=types.SimpleNamespace(frame_id='aim_sentry/map'),info=types.SimpleNamespace(width=10,height=10)))
                    v=types.SimpleNamespace(x=0.,y=0.,z=0.,w=1.)
                    subscriptions['/amcl_pose'](types.SimpleNamespace(header=types.SimpleNamespace(frame_id='aim_sentry/map',stamp=types.SimpleNamespace(sec=1,nanosec=0)),pose=types.SimpleNamespace(pose=types.SimpleNamespace(position=v,orientation=v))))
                if 'rviz' in processes and scenario in ('normal','rviz_crash'):
                    processes['rviz'].returncode=0 if scenario=='normal' else 3221225477
            fake={
                'rclpy':module('rclpy',init=lambda:None,spin_once=spin,shutdown=lambda:None),
                'rclpy.node':module('rclpy.node',Node=Node),
                'rclpy.qos':module('rclpy.qos',QoSProfile=lambda **kw:kw,DurabilityPolicy=types.SimpleNamespace(TRANSIENT_LOCAL=1),ReliabilityPolicy=types.SimpleNamespace(RELIABLE=1)),
                'rmsim_bridge':module('rmsim_bridge',__file__=str(root/'installed/rmsim_bridge/__init__.py')),
                'rmsim_bridge.navigation_drive_profile':module('drive',load_drive_profile=lambda p:dict(source_sha256='5'*64)),
                'rmsim_bridge.sentry_navigation_source':module('sentry',source_profile=lambda *args:None),
                'rmsim_bridge.native_transport':module('transport',loopback_environment=lambda original,p:(dict(original),{})),
                'ament_index_python.packages':module('packages',get_package_share_directory=lambda n:str(root),get_package_prefix=lambda n:str(root)),
                'std_msgs.msg':module('stdmsg',String=object),
                'geometry_msgs.msg':module('geometry',PoseWithCovarianceStamped=object),
                'nav_msgs.msg':module('navmsg',OccupancyGrid=object,Path=object),
                'action_msgs.msg':module('actionmsg',GoalStatusArray=object),
                'rosgraph_msgs.msg':module('clockmsg',Clock=object),
            }
            scene=dict(scene_file=str(scene_file),map_yaml=str(map_file),localization_map_yaml=None,view={},initial_pose=[0,0,0])
            def fake_digest(path):
                path=Path(path)
                return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else 'a'*64
            overrides=dict(ROOT=root,prepare_scene=lambda *a:scene,copy_runtime_arena=lambda *a:dict(render_profile='RMSimGymLumen'),
                rviz_configuration=lambda v:{},atomic_json=save,prepare_mujoco_viewer=prepare_viewer,
                capture_pointlio_inputs=lambda *a:{},worker_budget_digest=lambda a:'b'*64,digest=fake_digest,
                prepare_ue_worker_budget=lambda command,**kw:dict(command=command,launch_allowed=True,corelimit=8),
                apply_owned_process_policy=lambda *a,**kw:dict(status='fixture'))
            argv=['navigation_console.py','--engine-root',str(engine),'--output',str(out),'--port','0']
            # Use a real ephemeral TCP availability check while keeping the public argument valid.
            import socket
            with socket.socket() as available:
                available.bind(('127.0.0.1',0)); argv[-1]=str(available.getsockname()[1])
            if headless: argv+=['--no-rviz','--offscreen','--run-seconds','.2']
            if viewer_enabled: argv+=['--mujoco-viewer']
            argv+=list(extra_arguments)
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.dict(sys.modules,fake))
                stack.enter_context(patch.dict('os.environ',{'CONDA_PREFIX':str(root)}))
                stack.enter_context(patch.object(sys,'argv',argv))
                for key,value in overrides.items():stack.enter_context(patch.object(CONSOLE,key,value))
                stack.enter_context(patch.object(CONSOLE.shutil,'which',return_value='ros2.exe'))
                stack.enter_context(patch.object(CONSOLE.subprocess,'Popen',side_effect=popen))
                stack.enter_context(patch.object(CONSOLE.subprocess,'run',side_effect=run))
                stack.enter_context(patch.object(CONSOLE.subprocess,'check_output',side_effect=lambda cmd,**kw:('a'*40+'\n') if cmd[1]=='rev-parse' else ''))
                stack.enter_context(patch.object(CONSOLE.time,'monotonic',side_effect=lambda:clock[0]))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                result=CONSOLE.main()
            self.assertTrue(all(p.poll() is not None for p in processes.values()), 'owned process leaked')
            self.last_commands=commands
            self.last_provenance=json.loads((out/'runtime-provenance.json').read_text())
            return result,events,snapshots,plan_calls

    def test_normal_rviz_close_cleans_all_three_windows(self):
        result,events,states,_=self.run_case('normal')
        self.assertEqual(result,0)
        self.assertEqual([name for kind,name in events if kind=='launch'],['mujoco_viewer','ue','pointlio','navigation','bridge','rviz'])
        self.assertEqual(states[-1]['phase'],'stopped')

    def test_user_closes_viewer_before_ue_is_started(self):
        result,events,_,_=self.run_case('early_close')
        self.assertEqual(result,0)
        self.assertEqual([name for kind,name in events if kind=='launch'],['mujoco_viewer'])

    def test_viewer_close_after_ready_stops_owned_session(self):
        self.assertEqual(self.run_case('viewer_close')[0],0)

    def test_viewer_crash_is_failure_without_restart(self):
        result,events,states,_=self.run_case('viewer_crash')
        self.assertEqual(result,3)
        self.assertIn('17',states[-1]['error'])
        self.assertEqual(events.count(('launch','mujoco_viewer')),1)

    def test_rviz_crash_is_failure_without_restart(self):
        result,events,states,_=self.run_case('rviz_crash')
        self.assertEqual(result,3)
        self.assertIn('3221225477',states[-1]['error'])
        self.assertEqual(events.count(('launch','rviz')),1)

    def test_viewer_reports_error_before_ue(self):
        result,events,states,_=self.run_case('viewer_error')
        self.assertEqual(result,3)
        self.assertIn('fixture viewer failure',states[-1]['error'])
        self.assertNotIn(('launch','ue'),events)

    def test_viewer_exit_reason_reaches_top_level_failure(self):
        result,events,states,_=self.run_case('viewer_exit_reason')
        self.assertEqual(result,3)
        self.assertIn('fixture camera signature failure',states[-1]['error'])
        self.assertNotIn(('launch','ue'),events)

    def test_foreign_model_status_cannot_launch_ue(self):
        result,events,states,_=self.run_case('identity_failure')
        self.assertEqual(result,3)
        self.assertIn('identity mismatch',states[-1]['error'])
        self.assertNotIn(('launch','ue'),events)

    def test_foreign_renderer_receipt_cannot_launch_ue(self):
        result,events,states,_=self.run_case('renderer_identity_failure')
        self.assertEqual(result,3)
        self.assertIn('identity mismatch',states[-1]['error'])
        self.assertNotIn(('launch','ue'),events)

    def test_renderer_cache_failure_cleans_up_without_launching_ue(self):
        result,events,states,_=self.run_case('cache_failure')
        self.assertEqual(result,3)
        self.assertIn('renderer cache reported a failure',states[-1]['error'])
        self.assertNotIn(('launch','ue'),events)

    def test_incomplete_cache_coverage_cleans_up_and_never_opens_rviz(self):
        result,events,states,_=self.run_case('cache_incomplete')
        self.assertEqual(result,3)
        self.assertIn('complete displayed flex coverage',states[-1]['error'])
        self.assertNotIn(('launch','rviz'),events)

    def test_startup_budget_is_bounded(self):
        result,events,states,_=self.run_case('blocked_startup')
        self.assertEqual(result,3)
        self.assertIn('180s',states[-1]['error'])
        self.assertNotIn(('launch','ue'),events)

    def test_stale_viewer_marks_recovering_without_restart(self):
        result,events,states,_=self.run_case('stale_then_stop')
        self.assertEqual(result,0)
        self.assertTrue(any(s.get('phase')=='recovering' for s in states))
        self.assertEqual(events.count(('launch','mujoco_viewer')),1)

    def test_headless_fixture_never_prepares_or_starts_viewer(self):
        result,events,_,calls=self.run_case('headless',headless=True)
        self.assertEqual(result,0)
        self.assertEqual(calls,[])
        self.assertNotIn(('launch','mujoco_viewer'),events)
        self.assertNotIn(('launch','rviz'),events)

    def test_default_navigation_starts_ue_rviz_and_no_viewer_transport(self):
        result,events,states,calls=self.run_case('normal',with_viewer=False)
        self.assertEqual(result,0)
        self.assertEqual(calls,[])
        self.assertEqual([name for kind,name in events if kind=='launch'],['ue','pointlio','navigation','bridge','rviz'])
        command=self.last_commands['ue']
        self.assertIn('-PhysicsBackend=mujoco',command)
        self.assertIn('-RosLidar',command)
        self.assertFalse(any(arg.startswith('-MujocoViewer') for arg in command))
        self.assertEqual(self.last_provenance['mujoco_viewer'],dict(enabled=False,reason='not_requested'))
        self.assertFalse(states[-1]['mujoco_viewer']['enabled'])

    def test_rmuc_chaos_navigation_uses_the_same_venue_render_profile(self):
        result,_,_,_=self.run_case('normal',extra_arguments=('--physics-backend','chaos'),with_viewer=False)
        self.assertEqual(result,0)
        command=self.last_commands['ue']
        self.assertIn('-CustomConfig=RMSimGymLumen',command)
        self.assertIn('-sm6',command)
        self.assertFalse(any(arg.startswith('-MujocoModel=') for arg in command))
        self.assertEqual(self.last_provenance['runtime_arena']['render_profile'],'RMSimGymLumen')

    def test_render_scale_reaches_ue_without_changing_viewport_or_physics(self):
        result,_,states,_=self.run_case('normal',extra_arguments=(
            '--render-scale','75','--fullscreen','--width','2560','--height','1440'),with_viewer=False)
        self.assertEqual(result,0)
        command=self.last_commands['ue']
        self.assertIn('-ExecCmds=r.ScreenPercentage 75',command)
        self.assertIn('-ResX=2560',command)
        self.assertIn('-ResY=1440',command)
        self.assertIn('-SimStepSeconds=0.005',command)
        self.assertIn('-PhysicsBackend=mujoco',command)
        self.assertEqual(self.last_provenance['render_scale_percent'],75)
        self.assertEqual(states[-1]['render_scale_percent'],75)

    def test_fullscreen_diagnostic_options_reach_owned_children_and_provenance(self):
        result,_,states,calls=self.run_case('normal',extra_arguments=(
            '--fullscreen','--width','2560','--height','1440',
            '--viewer-timing-diagnostic','--viewer-continuous-render-diagnostic'))
        self.assertEqual(result,0)
        command=self.last_commands['ue']
        self.assertTrue(all(x in command for x in ('-fullscreen','-ResX=2560','-ResY=1440')))
        self.assertNotIn('-windowed',command)
        self.assertTrue(all(not x.startswith(('-WinX=','-WinY=')) for x in command))
        self.assertEqual(calls,[dict(timing_diagnostic=True,continuous_render_diagnostic=True,pause_when_covered=False)])
        self.assertEqual(states[-1]['display_request'],self.last_provenance['display_request'])
        self.assertEqual(states[-1]['display_request']['mode'],'fullscreen')
        self.assertTrue(self.last_provenance['mujoco_viewer']['continuous_render_diagnostic'])
        self.assertIn('-SimStepSeconds=0.005',command)
        self.assertIn('-RMSimViewportHz=60.0',command)

    def test_viewer_diagnostics_cannot_be_silently_ignored_in_headless_mode(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            self.run_case('headless',headless=True,extra_arguments=('--viewer-timing-diagnostic',))
        self.assertEqual(error.exception.code,2)


class PowerShellDisplayContracts(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'win32', 'Uses the real Windows PowerShell parser and parameter binder only')
    def test_wrapper_parameter_binding_and_forwarding_without_launch(self):
        test_directory=Path(__file__).resolve().parent
        for name,relative,prefix in (
            ('Start-NavigationConsole.ps1','tools/environment/Start-NavigationConsole.ps1','-'),
            ('Run-NavigationConsole.ps1','tools/integration/Run-NavigationConsole.ps1','--')):
            source=test_directory/name
            if not source.is_file(): source=test_directory.parents[1]/relative
            self.assertTrue(source.is_file())
            script=r'''
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile('__SOURCE__',[ref]$tokens,[ref]$errors)
if($errors.Count){throw ('PowerShell parse errors: '+($errors | Out-String))}
$conditions=@('$Fullscreen','$PSBoundParameters.ContainsKey(''Width'')','$PSBoundParameters.ContainsKey(''Height'')','$MujocoViewer','$ViewerTimingDiagnostic','$ViewerContinuousRenderDiagnostic','$RenderScale -ne 100')
$parts=@($ast.FindAll({param($node)
  $node -is [Management.Automation.Language.IfStatementAst] -and
  $node.Clauses.Count -eq 1 -and $conditions -ccontains $node.Clauses[0].Item1.Extent.Text
},$true) | Sort-Object {$_.Extent.StartOffset})
if($parts.Count -ne 7){throw 'Expected exactly seven isolated display-forwarding conditions'}
$fragment=$ast.ParamBlock.Extent.Text+"`n"+'$arguments=@()'+"`n"+(($parts | ForEach-Object {$_.Extent.Text}) -join "`n")+"`n"+'return ,$arguments'
$block=[scriptblock]::Create($fragment)
$default=@(& $block)
$selected=@(& $block -Fullscreen -Width 2560 -Height 1440 -MujocoViewer -ViewerTimingDiagnostic -ViewerContinuousRenderDiagnostic -RenderScale 75)
$invalidRejected=$false
try{[void](& $block -Width 0)}catch{$invalidRejected=$true}
[ordered]@{default=$default;selected=$selected;invalid_width_rejected=$invalidRejected;launched_processes=0} | ConvertTo-Json -Depth 5 -Compress
'''.replace('__SOURCE__',str(source).replace("'","''"))
            completed=subprocess.run([shutil.which('powershell.exe') or 'powershell.exe',
                '-NoProfile','-Command',script],capture_output=True,text=True,timeout=15)
            self.assertEqual(completed.returncode,0,completed.stderr)
            report=json.loads(completed.stdout)
            # PowerShell preserves the returned argument array as one pipeline item.
            default=report['default'][0] if report['default'] else []
            selected=report['selected'][0]
            self.assertEqual(default,[])
            expected=([prefix+'Fullscreen',prefix+'Width',2560,prefix+'Height',1440,
                       prefix+'MujocoViewer',prefix+'ViewerTimingDiagnostic',prefix+'ViewerContinuousRenderDiagnostic',prefix+'RenderScale',75]
                      if prefix=='-' else ['--fullscreen','--width',2560,'--height',1440,
                                          '--mujoco-viewer','--viewer-timing-diagnostic','--viewer-continuous-render-diagnostic','--render-scale',75])
            self.assertEqual(selected,expected)
            self.assertTrue(report['invalid_width_rejected'])
            self.assertEqual(report['launched_processes'],0)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,default=Path(__file__).with_name('navigation_console.py'))
    args,remaining=parser.parse_known_args()
    CONSOLE=load_console(args.source)
    unittest.main(argv=[sys.argv[0],*remaining],verbosity=2)
