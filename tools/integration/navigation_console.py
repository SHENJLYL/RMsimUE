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

from navigation_console_config import ROOT, prepare_scene, rviz_configuration, digest, atomic_json, copy_runtime_arena
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
    parser.add_argument('--navigation-source', type=Path, help='Explicit vehicle-supplied sentry_ws Nav2 YAML; omit to use workspace preset')
    parser.add_argument('--localization-map-yaml', type=Path, help='Optional validated AMCL map; navigation and costmap keep the scene map')
    parser.add_argument('--initial-pose-offset', nargs=3, type=float, default=None, metavar=('DX_M','DY_M','DYAW_RAD'), help='Explicit localization-recovery test offset from the scene default spawn; XY offsets use map axes')
    parser.add_argument('--navigation-follow-profile',type=Path,help='Vehicle-supplied high-level navigation gimbal-follow profile')
    parser.add_argument('--lidar-profile',type=Path,help='Explicit vehicle LiDAR profile; diagnostic sensor budget can use a separate file')
    parser.add_argument('--sim-step', type=float, choices=[0.001,0.002,0.005,0.01], default=0.005, help='Fixed physics-step seconds; acceptance required for non-default values')
    parser.add_argument('--no-rviz', action='store_true', help='Integration-test mode; normal users use RViz')
    parser.add_argument('--offscreen', action='store_true', help='Owned integration fixture only; requires --no-rviz')
    parser.add_argument('--no-rhi', action='store_true', help='Diagnostic renderer isolation only: no GUI, no hardware camera validation')
    parser.add_argument('--profile-sensor-timing',action='store_true', help='Native engine isolated raycast/camera timing logs')
    parser.add_argument('--profile-mujoco-timing',action='store_true',help='Native dynamics timing attribution for offscreen fixtures')
    parser.add_argument('--profile-frames',type=int,default=0,help='Bounded native CPU/physics CSV attribution; integration fixtures only')
    parser.add_argument('--verify-lidar-queries',action='store_true',help='Compare the first 4096 physical rays against serial queries')
    parser.add_argument('--lidar-serial-query',action='store_true',help='Diagnostic serial scene-query reference')
    parser.add_argument('--run-seconds', type=float, default=0., help='Optional bounded integration run')
    parser.add_argument('--viewport-hz',type=float,default=60.,help='Scene rendering cadence independent of 200Hz physical sampling')
    parser.add_argument('--camera-hz',type=float,default=0.,help='ROS RGB stream: 0 disables capture; 1..60 enables images independently of the UE viewport')
    parser.add_argument('--pace-real-time',action=argparse.BooleanOptionalAction,default=True,help='Pace actual fixed physics ticks against wall time; overload remains observable')
    parser.add_argument('--cpu-policy', choices=['performance','system'], default='performance', help='Owned paced UE process: dynamic performance CPU Sets on Windows, or preserve system policy')
    args = parser.parse_args()
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
    if args.physics_backend == 'mujoco':
        if args.scene != 'rmuc2027':
            raise ValueError('The prepared MuJoCo scene is RMUC2027; choose its matching scene')
        # UE resolves the layout relative to this shell; snapshot both unchanged.
        runtime_arena = copy_runtime_arena(ROOT/'assets/maps/rmuc2027/arena-shell.json',
                                           Path(scene['scene_file']).parent)
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
    navigation_source = (args.navigation_source or ROOT/'ros2_ws/src/pb2025_nav_bringup/config'/args.control_profile/'nav2_params.yaml').resolve()
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
    config.write_text(json.dumps(rviz_configuration(scene['view']), indent=2)+'\n', encoding='utf-8')
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
             'cpu_policy_requested':args.cpu_policy,'process_policy':None,
             'navigation_source':str(navigation_source),'gimbal_follow_profile':str(follow_source),'lidar_profile':str(lidar_source),
             'navigation_drive_profile':navigation_drive_profile,
             'navigation':None, 'bridge':None, 'estimated_pose':None, 'clock_ns':0,
             'map_received':False, 'path_points':0, 'actions':{}, 'pids':{},
             'scope':'Point-LIO local mapping + AMCL map localization + original Nav2; monitor consumes no evaluation truth'}
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
        nonlocal status_sequence
        status_sequence += 1
        state['snapshot_sequence'] = status_sequence
        state['snapshot_written_monotonic_s'] = time.monotonic()
        state['status_period_seconds'] = .5
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
    try:
        editor = (args.engine_root/'Engine/Binaries/Win64/UnrealEditor.exe').resolve()
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
                          sensor_timing_diagnostic=args.profile_sensor_timing,mujoco_timing_diagnostic=args.profile_mujoco_timing,status_period_seconds=.5,
                          verify_lidar_queries=args.verify_lidar_queries,lidar_serial_query=args.lidar_serial_query,
                          profile_frames=args.profile_frames)
        provenance['physics_backend'] = args.physics_backend
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
        display_flags=['-RenderOffScreen','-unattended','-ResX=640','-ResY=360'] if args.offscreen else ['-windowed','-ResX=1120','-ResY=860','-WinX=20','-WinY=40']
        if runtime_arena and runtime_arena['render_profile'] == 'RMSimGymLumen':
            display_flags.extend(['-CustomConfig=RMSimGymLumen', '-d3d12', '-sm6'])
        if args.no_rhi: display_flags.append('-nullrhi')
        if args.profile_sensor_timing: display_flags.append('-ProfileSensorTiming')
        if args.profile_mujoco_timing: display_flags.append('-ProfileMujocoTiming')
        if args.profile_frames:
            display_flags.extend(['-csvCaptureFrames='+str(args.profile_frames),'-csvCategories=PhysicsVerbose,PhysicsCounters','-csvCompression=0','-csvStatCounts'])
        if args.verify_lidar_queries: display_flags.append('-VerifyLidarQueries')
        if args.lidar_serial_query: display_flags.append('-LidarSerialQuery')
        if args.viewport_hz: display_flags.append('-RMSimViewportHz='+str(args.viewport_hz))
        if args.pace_real_time: display_flags.append('-SimRealTimePace')
        ue_command = [str(value) for value in [editor, project, '/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode',
            '-game',*display_flags,'-NoSplash','-NoSound',
            # Apply before world/Chaos creation; manual entry points retain their 1/240 s substeps.
            provenance['physics_config_override'],
            '-RosControl','-RosAimControl','-RosImu','-RosLidar',
            '-LidarProfile='+str(lidar_source),
            '-ImuProfile='+str(ROOT/'assets/vehicles/aim_sentry/sensors/mid360-imu.json'),'-SimStepSeconds='+str(args.sim_step),'-RosCameraWidth=320','-RosCameraHeight=240','-RosCameraHz='+str(args.camera_hz),
            '-RosPort='+str(args.port), '-MeshRigFile='+str(ROOT/'assets/vehicles/aim_sentry/ue_mesh_rig.json'),
            '-MapFile='+scene['scene_file'], '-abslog='+str(output/'runtime.log')]]
        if args.physics_backend == 'mujoco':
            ue_command.extend(['-PhysicsBackend=mujoco','-MujocoModel='+str(args.mujoco_model)])
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
        launch('ue', ue_command, original)
        launch('pointlio', [ros2,'run','rmsim_bridge','point_lio','--ros-args','-p','imu_id:=mid360_imu','-p','sentry_source:='+str(navigation_source),'-p','output_directory:='+str(output/'pointlio')], local_environment)
        launch('navigation', [ros2,'run','rmsim_bridge','navigation','--ros-args','-p',
            'reference_config:='+str(ROOT/'ros2_ws/src/pb2025_nav_bringup/config/reality/nav2_params.yaml'),
            '-p','map_yaml:='+scene['map_yaml'],
            *(['-p','localization_map_yaml:='+effective_localization_map_yaml] if effective_localization_map_yaml else []),
            '-p','initial_pose:='+json.dumps(scene['initial_pose']),
            '-p','control_profile:='+args.control_profile,'-p','control_source:='+str(navigation_source),
            '-p','drive_profile:='+str(drive_source),
            '-p','native_runtime:='+str(ROOT/'artifacts/runtime/navigation/windows'),
            '-p','output_directory:='+str(output/'navigation')], local_environment)
        launch('bridge', [ros2,'run','rmsim_bridge','bridge','--ros-args','-p','port:='+str(args.port),'-p','aim_control:=true',
            '-p','navigation_follow:=true','-p','navigation_follow_profile:='+str(follow_source)], local_environment)
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
