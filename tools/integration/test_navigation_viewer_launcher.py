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


class HelperContracts(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.path = self.root/'status.json'
        self.expected = dict(port=42001, session_token_sha256='1'*64,
                             manifest_sha256='2'*64, model_sha256='3'*64,
                             physics_identity_sha256='4'*64)
        self.status = dict(schema='rmsim_mujoco_viewer_status_v1', pid=123,
                           phase='ready', bound=True, frames=1, body_pose_matches=True,
                           status_written_monotonic_s=100., generation=1, sequence=8,
                           sim_time_s=.05, **self.expected)

    def read(self, value=None, now=100.25):
        self.path.write_text(json.dumps(self.status if value is None else value), encoding='utf-8')
        return CONSOLE.read_mujoco_viewer_status(self.path, self.expected, 123, now)

    def test_default_visible_native_mode_enables_viewer(self):
        self.assertTrue(CONSOLE.mujoco_viewer_enabled(types.SimpleNamespace(
            physics_backend='mujoco', no_rviz=False, offscreen=False)))

    def test_headless_and_explicit_chaos_modes_do_not_enable_viewer(self):
        for backend, no_rviz, offscreen in [('mujoco', True, False), ('mujoco', True, True),
                                           ('mujoco', False, True), ('chaos', False, False)]:
            with self.subTest(backend=backend, no_rviz=no_rviz, offscreen=offscreen):
                self.assertFalse(CONSOLE.mujoco_viewer_enabled(types.SimpleNamespace(
                    physics_backend=backend, no_rviz=no_rviz, offscreen=offscreen)))

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
        self.assertEqual(self.read(dict(self.status, phase='waiting', frames=0, body_pose_matches=False))['phase'], 'waiting')

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

    @unittest.skipUnless(sys.platform == 'win32' and Path(r'D:\simulator\artifacts\environments\mujoco-4.0.1\Scripts\python.exe').is_file(),
                         'Requires the prepared Windows venv redirector; creates no GUI or ROS nodes')
    def test_real_windows_venv_process_binding(self):
        python=Path(r'D:\simulator\artifacts\environments\mujoco-4.0.1\Scripts\python.exe')
        fixture=self.root/'viewer process fixture.py'
        fixture.write_text('''import argparse,json,os,time
from pathlib import Path
p=argparse.ArgumentParser()
for key in ('manifest','port','session-token','status-file','parent-pid'):p.add_argument('--'+key,required=True)
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
                     '--session-token','0'*32,'--status-file',str(status),'--parent-pid',str(os.getpid())]
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
            wrong_token=list(command);wrong_token[wrong_token.index('--session-token')+1]='f'*32
            with self.assertRaisesRegex(ValueError,'argument'):
                CONSOLE.bind_mujoco_viewer_process(process,actual['pid'],wrong_token)
            proof['checks'].append('wrong_command_token_rejected')
            wrong_source=list(command);wrong_source[1]=str(self.root/'unowned.py')
            with self.assertRaisesRegex(ValueError,'argument'):
                CONSOLE.bind_mujoco_viewer_process(process,actual['pid'],wrong_source)
            proof['checks'].append('wrong_source_script_rejected')
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
    def run_case(self, scenario, headless=False):
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
            events=[]; processes={}; snapshots=[]; clock=[100.]; spins=[0]; plan_calls=[]
            expected=dict(port=42001,session_token_sha256='1'*64,manifest_sha256=CONSOLE.digest(manifest),**model)
            expected.pop('timestep_s')
            plan=dict(command=['viewer-fixture'], environment={}, expected=expected, token='0'*32,
                      status_file=out/'mujoco-viewer/status.json', provenance={'enabled':True})
            def prepare_viewer(*args):
                plan_calls.append(True); plan['status_file'].parent.mkdir()
                return plan
            def save(path, value):
                if path.name=='status.json': snapshots.append(json.loads(json.dumps(value)))
                path.write_text(json.dumps(value)); return True
            def popen(command, **kwargs):
                if command[0]=='viewer-fixture': name='mujoco_viewer'
                elif command[0].endswith('UnrealEditor.exe'): name='ue'
                elif command[0].endswith('rviz2.exe'): name='rviz'
                else: name=command[3].replace('point_lio','pointlio')
                if name=='ue' and not headless:
                    self.assertEqual(json.loads(plan['status_file'].read_text())['phase'], 'waiting')
                    self.assertIn('-MujocoViewerPort=42001', command)
                    self.assertIn('-MujocoViewerToken='+'0'*32, command)
                if name=='rviz' and not headless:
                    self.assertEqual(json.loads(plan['status_file'].read_text())['phase'], 'ready')
                events.append(('launch',name))
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
                        status_written_monotonic_s=clock[0],**expected)
                    if phase=='error':
                        if scenario=='viewer_exit_reason': status['exit_reason']='fixture camera signature failure'
                        else: status['error']='fixture viewer failure'
                    if scenario=='identity_failure': status['model_sha256']='f'*64
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
            overrides=dict(ROOT=root,prepare_scene=lambda *a:scene,copy_runtime_arena=lambda *a:dict(render_profile='fixture'),
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


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,default=Path(__file__).with_name('navigation_console.py'))
    args,remaining=parser.parse_known_args()
    CONSOLE=load_console(args.source)
    unittest.main(argv=[sys.argv[0],*remaining],verbosity=2)
