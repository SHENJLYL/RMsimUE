"""Own one UE + ROS + RViz session. Closing RViz stops only this session's processes."""
from __future__ import annotations
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

from navigation_console_config import ROOT, prepare_scene, rviz_configuration, digest, atomic_json


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

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene', choices=['rmuc2027', 'import_lab'], default='rmuc2027')
    parser.add_argument('--engine-root', type=Path, default=Path('E:/simulator-tools/UE_5.8'))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--port', type=int, default=9451)
    parser.add_argument('--domain', type=int, default=71)
    parser.add_argument('--control-profile', choices=['simulation','reality'], default='simulation', help='Use original sentry_ws Nav2 controller, smoother, planner and costmap configuration')
    parser.add_argument('--navigation-source', type=Path, help='Explicit vehicle-supplied sentry_ws Nav2 YAML; omit to use workspace preset')
    parser.add_argument('--navigation-follow-profile',type=Path,help='Vehicle-supplied high-level navigation gimbal-follow profile')
    parser.add_argument('--sim-step', type=float, choices=[0.001,0.002,0.005,0.01], default=0.005, help='Fixed physics-step seconds; acceptance required for non-default values')
    parser.add_argument('--no-rviz', action='store_true', help='Integration-test mode; normal users use RViz')
    parser.add_argument('--offscreen', action='store_true', help='Owned integration fixture only; requires --no-rviz')
    parser.add_argument('--run-seconds', type=float, default=0., help='Optional bounded integration run')
    args = parser.parse_args()
    if args.offscreen and not args.no_rviz: parser.error('--offscreen requires --no-rviz')
    if not 1024 <= args.port <= 65535 or not 0 <= args.domain <= 232 or args.run_seconds < 0:
        parser.error('Invalid port, ROS domain or duration')
    base = ROOT/'artifacts/navigation/console-live'
    base.mkdir(parents=True, exist_ok=True)
    output = args.output or base/(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    scene = prepare_scene(args.scene, output/'scene')
    navigation_source = (args.navigation_source or ROOT/'ros2_ws/src/pb2025_nav_bringup/config'/args.control_profile/'nav2_params.yaml').resolve()
    follow_source = (args.navigation_follow_profile or ROOT/'assets/vehicles/aim_sentry/navigation-follow.json').resolve()
    if not follow_source.is_file() or follow_source.stat().st_size>16384:
        raise ValueError('Invalid vehicle gimbal-follow profile')
    from rmsim_bridge.sentry_navigation_source import source_profile
    source_profile(ROOT/'ros2_ws/src/pb2025_nav_bringup/config/reality/nav2_params.yaml',args.control_profile,str(navigation_source))
    config = output/'navigation.rviz'
    config.write_text(json.dumps(rviz_configuration(scene['view']), indent=2)+'\n', encoding='utf-8')
    from ament_index_python.packages import get_package_share_directory
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
             'control_profile':args.control_profile,'sim_step_seconds':args.sim_step,
             'navigation_source':str(navigation_source),'gimbal_follow_profile':str(follow_source),
             'navigation':None, 'bridge':None, 'estimated_pose':None, 'clock_ns':0,
             'map_received':False, 'path_points':0, 'actions':{}, 'pids':{},
             'scope':'Point-LIO local mapping + AMCL map localization + original Nav2; monitor consumes no evaluation truth'}
    history = []
    def nav_status(message):
        state['navigation'] = json.loads(message.data)
        state['navigation_received_wall'] = time.monotonic()
    def bridge_status(message):
        value = json.loads(message.data)
        state['bridge'] = {k:value.get(k) for k in ('connected','state_fresh','session','epoch','sim_time_ns','command','simulation_rate','aim','aim_adapter')}
    def pose(message):
        p, q = message.pose.pose.position, message.pose.pose.orientation
        state['estimated_pose'] = {'frame':message.header.frame_id, 'x':p.x, 'y':p.y,
                                   'q':[q.x,q.y,q.z,q.w], 'stamp_ns':message.header.stamp.sec*10**9+message.header.stamp.nanosec}
    def action_status(name, message):
        value = [{'id':bytes(item.goal_info.goal_id.uuid).hex(), 'status':item.status} for item in message.status_list]
        if state['actions'].get(name) != value:
            state['actions'][name] = value
            history.append({'wall':time.time(), 'clock_ns':state['clock_ns'], 'action':name, 'goals':value})
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
    def save():
        state['pids'] = {name:p.pid for name,p in children.items() if p.poll() is None}
        state['updated_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if not atomic_json(output/'status.json', state):
            state['snapshot_write_deferrals'] = state.get('snapshot_write_deferrals', 0)+1
        atomic_json(output/'action-events.json', history[-500:])
    def launch(name, command, environment):
        log = (output/(name+'.log')).open('w', encoding='utf-8'); logs.append(log)
        options = {'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
        process = subprocess.Popen([str(x) for x in command], stdout=log, stderr=subprocess.STDOUT,
                                   env=environment, cwd=ROOT, **options)
        children[name] = process; save(); return process
    try:
        editor = args.engine_root/'Engine/Binaries/Win64/UnrealEditor.exe'
        project = ROOT/'unreal/RMSim/RMSim.uproject'
        rviz = ROOT/'artifacts/runtime/rviz/windows/rviz2.exe'
        ros2 = shutil.which('ros2')
        if not editor.is_file() or not ros2 or (not args.no_rviz and not rviz.is_file()):
            raise RuntimeError('Missing UE/ROS/RViz prerequisites; run tools/environment/Prepare-NavigationConsole.cmd')
        # A dedicated port is checked before taking any UI ownership.
        import socket
        with socket.socket() as check:
            check.bind(('127.0.0.1', args.port))
        provenance = dict(scene=scene, transport=transport, git_head=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
                          module_sha256=digest(ROOT/'unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll'),
                          drive_profile_sha256=digest(ROOT/'assets/vehicles/aim_sentry/drive-profile.json'),
                          rig_sha256=digest(ROOT/'assets/vehicles/aim_sentry/ue_mesh_rig.json'),
                          lidar_sha256=digest(ROOT/'assets/vehicles/aim_sentry/sensors/mid360.json'),
                          imu_sha256=digest(ROOT/'assets/vehicles/aim_sentry/sensors/mid360-imu.json'),
                          navigation_follow_sha256=digest(follow_source),navigation_follow_profile=str(follow_source),
                          source_control_config_sha256=digest(navigation_source),navigation_source=str(navigation_source),
                          control_profile=args.control_profile,sim_step_seconds=args.sim_step)
        source_root=ROOT/'ros2_ws/src/rmsim_bridge/rmsim_bridge'
        import rmsim_bridge
        installed_root=Path(rmsim_bridge.__file__).resolve().parent
        provenance['bridge_inputs']={}
        for name in ('node.py','protocol.py','imu.py','lidar.py','aim_adapter.py','navigation_follow.py','navigation_follow_adapter.py','navigation_config.py','navigation_runner.py','point_lio_runner.py','pointlio_units.py','sentry_pointlio_source.py'):
            source=source_root/name;installed=installed_root/name
            provenance['bridge_inputs'][name]={'source_sha256':digest(source),'installed_sha256':digest(installed)}
            if source.read_text(encoding='utf-8')!=installed.read_text(encoding='utf-8'):
                raise RuntimeError('Rebuild the installed bridge package before launching: '+name)
        provenance['native_navigation']={name:digest(ROOT/'artifacts/runtime/navigation/windows'/name) for name in ('loam_interface.dll','sensor_scan_generation.dll','fake_vel_transform.dll')}
        provenance['console_source_sha256']=digest(Path(__file__))
        provenance['dependency_lock_sha256']=digest(ROOT/'tools/environment/pixi.lock')
        provenance['capture_scope']='inputs captured before process launch; source/installed Python text compared'
        (output/'runtime-provenance.json').write_text(json.dumps(provenance, indent=2)+'\n', encoding='utf-8')
        (base/'latest.json').write_text(json.dumps({'output_directory':str(output),'pid':os.getpid()})+'\n', encoding='utf-8')
        display_flags=['-RenderOffScreen','-unattended'] if args.offscreen else ['-windowed','-ResX=1120','-ResY=860','-WinX=20','-WinY=40']
        launch('ue', [editor, project, '/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode',
            '-game',*display_flags,'-NoSplash','-NoSound',
            '-RosControl','-RosAimControl','-RosImu','-RosLidar',
            '-LidarProfile='+str(ROOT/'assets/vehicles/aim_sentry/sensors/mid360.json'),
            '-ImuProfile='+str(ROOT/'assets/vehicles/aim_sentry/sensors/mid360-imu.json'),'-SimStepSeconds='+str(args.sim_step),'-RosCameraWidth=320','-RosCameraHeight=240',
            '-RosPort='+str(args.port), '-MeshRigFile='+str(ROOT/'assets/vehicles/aim_sentry/ue_mesh_rig.json'),
            '-MapFile='+scene['scene_file'], '-abslog='+str(output/'runtime.log')], original)
        launch('pointlio', [ros2,'run','rmsim_bridge','point_lio','--ros-args','-p','imu_id:=mid360_imu','-p','sentry_source:='+str(navigation_source),'-p','output_directory:='+str(output/'pointlio')], original)
        launch('navigation', [ros2,'run','rmsim_bridge','navigation','--ros-args','-p',
            'reference_config:='+str(ROOT/'ros2_ws/src/pb2025_nav_bringup/config/reality/nav2_params.yaml'),
            '-p','map_yaml:='+scene['map_yaml'],'-p','initial_pose:='+json.dumps(scene['initial_pose']),
            '-p','control_profile:='+args.control_profile,'-p','control_source:='+str(navigation_source),
            '-p','native_runtime:='+str(ROOT/'artifacts/runtime/navigation/windows'),
            '-p','output_directory:='+str(output/'navigation')], local_environment)
        launch('bridge', [ros2,'run','rmsim_bridge','bridge','--ros-args','-p','port:='+str(args.port),'-p','aim_control:=true',
            '-p','navigation_follow:=true','-p','navigation_follow_profile:='+str(follow_source)], original)
        print('Starting native navigation. Waiting for measured localization and Nav2 readiness...', flush=True)
        deadline = time.monotonic()+180; last_save = 0.
        while True:
            rclpy.spin_once(node, timeout_sec=.05)
            if (output/'stop.request').exists(): raise StopRequested()
            exited={name:p.poll() for name,p in children.items() if p.poll() is not None}
            if exited:
                state['component_exits']=exited
                raise RuntimeError('Owned component exited during startup: '+str(exited)+'; see component logs')
            nav = state['navigation'] or {}
            if nav.get('phase')=='running' and state['map_received'] and state['estimated_pose']:
                break
            if nav.get('phase')=='map_mismatch': raise RuntimeError('Scene/navigation map identity mismatch')
            if time.monotonic()>deadline: raise TimeoutError('Navigation did not become ready within 180s')
            if time.monotonic()-last_save>.5:
                state['phase']=nav.get('phase','starting'); save(); last_save=time.monotonic()
        state['phase']='ready'
        if not args.no_rviz:
            gui_environment = dict(local_environment)
            prefix = Path(os.environ['CONDA_PREFIX'])/'Library'
            gui_environment['QT_PLUGIN_PATH'] = str(prefix/'plugins')
            gui_environment['QT_QPA_PLATFORM_PLUGIN_PATH'] = str(prefix/'plugins/platforms')
            launch('rviz', [rviz,'-d',config,'--ros-args','-p','use_sim_time:=true'], gui_environment)
        save()
        print('READY: RViz Nav2 Goal selects a destination. Waypoint mode queues destinations. Cancel stops navigation.', flush=True)
        print('Close RViz or press Ctrl+C here to stop this entire owned session.', flush=True)
        ready_at=time.monotonic()
        while not args.run_seconds or time.monotonic()-ready_at<args.run_seconds:
            rclpy.spin_once(node, timeout_sec=.05)
            if (output/'stop.request').exists(): raise StopRequested()
            if 'rviz' in children and children['rviz'].poll() is not None:
                state['rviz_exit_code']=children['rviz'].returncode
                if children['rviz'].returncode != 0: raise RuntimeError('RViz exited with code '+str(children['rviz'].returncode)+'; see rviz.log')
                break
            if any(p.poll() is not None for name,p in children.items() if name!='rviz'):
                raise RuntimeError('An owned simulator or algorithm process exited')
            if time.monotonic()-last_save>.5:
                state['phase']='ready' if (state['navigation'] or {}).get('phase')=='running' else 'recovering'
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
            try: terminate_owned(process)
            except Exception as error: state.setdefault('cleanup_errors', []).append(name+': '+str(error))
        save()
        for log in logs: log.close()
        node.destroy_node(); rclpy.shutdown()
    return 0 if state['phase']=='stopped' and not state.get('cleanup_errors') else 3

if __name__ == '__main__':
    raise SystemExit(main())
