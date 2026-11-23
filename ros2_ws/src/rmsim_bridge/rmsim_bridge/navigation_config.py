"""Adapt pinned sentry parameters at the simulator's frame/unit/map boundary."""
import copy,json
from pathlib import Path
import yaml
from .protocol import identifier
from .sentry_navigation_source import source_profile
from .navigation_map_identity import validate_localization_map
from .navigation_drive_profile import apply_drive_profile

LOCALIZATION_NODES=['map_server','amcl']
NAVIGATION_NODES=['controller_server','planner_server','smoother_server','behavior_server','bt_navigator','waypoint_follower','velocity_smoother']
INTERFACE_NODES=['loam_interface','sensor_scan_generation','terrain_analysis','terrain_analysis_ext','pointcloud_to_laserscan','fake_vel_transform']

def prepare(reference,map_yaml,robot,initial_pose,control_profile='legacy',control_source='',localization_map_yaml='',drive_profile=''):
    identifier(robot);reference=Path(reference);map_yaml=Path(map_yaml).resolve()
    source=yaml.safe_load(reference.read_text(encoding='utf-8'))
    selected=INTERFACE_NODES+LOCALIZATION_NODES+NAVIGATION_NODES+['local_costmap','global_costmap']
    cfg={key:copy.deepcopy(source[key]) for key in selected}
    upstream_blocks, upstream_identity = source_profile(reference, control_profile, control_source)
    cfg.update(upstream_blocks)
    for name, limit in upstream_identity.get('max_passable_slope_deg', {}).items():
        cfg[name]['ros__parameters']['maxPassableSlopeDeg'] = limit
    def clock(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if key=='use_sim_time':value[key]=True
                else:clock(item)
        elif isinstance(value,list):
            for item in value:clock(item)
    clock(cfg)
    cfg['/**']={'ros__parameters':{'use_sim_time':True}}
    get=lambda name:cfg[name]['ros__parameters']
    base=robot+'/base_link';imu=robot+'/imu_link';odom=robot+'/odom';fake=robot+'/navigation_frame';world=robot+'/map'
    stabilized=robot+'/base_stabilized'
    get('loam_interface').update(odom_frame=odom,base_frame=base,lidar_frame=imu)
    # Point-LIO's pose is the IMU pose; using the LiDAR center would introduce a lever-arm error.
    get('sensor_scan_generation').update(lidar_frame=imu,base_frame=base,robot_base_frame=base)
    for name in ('terrain_analysis','terrain_analysis_ext'):get(name)['odom_frame']=odom
    get('fake_vel_transform').update(robot_base_frame=base,fake_robot_base_frame=fake,translation_scale=1.0,output_cmd_vel_topic='/'+robot+'/cmd_vel',init_spin_speed=0.0,
                                     planar_navigation_frames=True,stabilized_base_frame=stabilized)
    get('pointcloud_to_laserscan').update(target_frame=stabilized,scan_time=.1)
    get('amcl').update(base_frame_id=stabilized,odom_frame_id=odom,global_frame_id=world,set_initial_pose=False)
    get('map_server').update(yaml_filename=str(map_yaml),frame_id=world)
    get('controller_server').pop('progress_checker_plugins',None)
    get('controller_server')['progress_checker_plugin']='progress_checker' # Humble1.1.20 uses singular.
    if control_profile=='legacy':
        get('controller_server')['general_goal_checker'].update(xy_goal_tolerance=.10)
        # Stable historical diagnostics only. The user console uses the actual sentry
        # simulation profile, including the native curvature/approach limits.
        get('controller_server')['FollowPath'].update(v_linear_max=.35,min_lookahead_dist=.25,lookahead_dist=.4)
        get('velocity_smoother').update(max_velocity=[.35,.35,2.0],min_velocity=[-.35,-.35,-2.0],max_accel=[.8,.8,3.0],max_decel=[-3.0,-3.0,-5.0],deadband_velocity=[0.0,0.0,0.0])
    bt=get('bt_navigator');bt.update(global_frame=world,robot_base_frame=fake)
    trees=reference.parents[2]/'behavior_trees'
    for key,file in [('default_nav_to_pose_bt_xml','navigate_to_pose_w_replanning_and_recovery.xml'),('default_nav_through_poses_bt_xml','navigate_through_poses_w_replanning_and_recovery.xml')]:
        bt[key]=str(trees/file)
        if not Path(bt[key]).is_file():raise ValueError('Missing original behavior tree '+bt[key])
    get('behavior_server').update(local_frame=odom,global_frame=odom,robot_base_frame=fake)
    # In RMSim, vehicle capabilities come from the same package as manual driving.
    # Keep the selected source algorithms and the active original velocity smoother.
    follow = get('controller_server')['FollowPath']
    if drive_profile and control_profile in ('simulation', 'reality'):
        # Smooth translation before it is projected to the rotating chassis.
        # The vehicle still applies its body-axis speed and acceleration limits.
        fixed_frame = follow.setdefault('smooth_in_navigation_frame', True)
        follow.setdefault('allow_heading_rate_shaping', fixed_frame)
    drive_identity=apply_drive_profile(cfg,drive_profile,robot) if drive_profile else None
    follow['collision_body_frame'] = stabilized
    layer = cfg['local_costmap']['local_costmap']['ros__parameters']['intensity_voxel_layer']
    follow['collision_observation_range'] = min(
        float(layer[name]['obstacle_max_range']) for name in layer['observation_sources'].split())
    if drive_identity:
        # Native navigation also accepts the operator's independent spin command.
        follow['external_rotation_possible'] = True
        get('fake_vel_transform')['project_heading_to_stamp'] = True
        vehicle = drive_identity['vehicle_control']
        decel = vehicle['LinearDecelerationMps2']
        update_hz = cfg['local_costmap']['local_costmap']['ros__parameters']['update_frequency']
        follow['collision_deceleration'] = decel
        follow['tracking_deceleration'] = min(
            float(follow.get('tracking_deceleration', vehicle['LinearAccelerationMps2'])), decel)
        # Map/controller latency plus time to slew from acceleration into braking.
        follow['collision_reaction_time'] = (1.0 / update_hz + 1.0 / get('controller_server')['controller_frequency']
            + (vehicle['LinearAccelerationMps2'] + decel) / vehicle['DecelerationJerkMps3'])
    footprint='[[-0.26,-0.26],[-0.26,0.26],[0.26,0.26],[0.26,-0.26]]'
    for key in ['local_costmap','global_costmap']:
        params=cfg[key][key]['ros__parameters'];params.update(global_frame=odom if key=='local_costmap' else world,robot_base_frame=fake,footprint=footprint,footprint_padding=.01)
        params.pop('robot_radius',None)
        if drive_identity and key=='local_costmap':
            # Idle-spin safety must receive current geometry even when the rolling origin is stationary.
            params['always_send_full_costmap'] = True
        layer=params['intensity_voxel_layer']
        for name in layer['observation_sources'].split():
            layer[name]['topic']='/'+name;layer[name]['sensor_frame']=robot+'/lidar_link'
    metadata=json.loads((map_yaml.parent/'metadata.json').read_text(encoding='utf-8-sig'))
    ground = metadata.get('surveyed_ground')
    clearances = metadata.get('navigation_clearance')
    default_source = reference.parent.parent/'simulation/nav2_params.yaml'
    if (clearances and control_profile=='simulation' and
            Path(upstream_identity['source_path']).resolve()==default_source.resolve()):
        planner = get('planner_server')['GridBased']
        if planner['plugin'] != 'pb_nav2_plugins::ClearancePlanner':
            planner['route_backend'] = planner['plugin']
        planner.update(plugin='pb_nav2_plugins::ClearancePlanner',
                       curve_clearance=float(clearances['preferred_curve_m']))
        for name in ('local_costmap','global_costmap'):
            cfg[name][name]['ros__parameters']['inflation_layer']['inflation_radius'] = float(clearances['inflation_radius_m'])
    if ground:
        import hashlib
        file = map_yaml.parent/'terrain-ground.bin'
        if ground.get('file') != file.name or hashlib.sha256(file.read_bytes()).hexdigest() != ground.get('sha256'):
            raise ValueError('Invalid surveyed ground reference for the selected map')
        for name in ('terrain_analysis','terrain_analysis_ext'):
            get(name).update(surveyed_ground_file=str(file), surveyed_ground_frame=world,
                             surveyed_ground_align_xy=True, surveyed_ground_observed_plane=True)
    if drive_identity:metadata['drive_profile']=drive_identity
    if 'source_md5' not in metadata:raise ValueError('Re-export the map with source identity metadata.')
    localization_nodes=list(LOCALIZATION_NODES)
    if localization_map_yaml:
        identity=validate_localization_map(map_yaml,localization_map_yaml)
        source_topic=get('amcl').get('map_topic','map')
        if not isinstance(source_topic,str) or not source_topic:
            raise ValueError('Source AMCL map_topic must be a nonempty ROS topic')
        target_topic='localization_map'
        if get('map_server').get('topic_name','map').lstrip('/')==target_topic:
            raise ValueError('Navigation and localization map publishers require distinct topics')
        cfg['localization_map_server']=copy.deepcopy(cfg['map_server'])
        get('localization_map_server').update(yaml_filename=identity['localization']['yaml_path'],topic_name=target_topic)
        localization_nodes=['map_server','localization_map_server','amcl']
        metadata['localization_map']=identity
        metadata['localization_map_binding']={'source_topic':source_topic,'target_topic':target_topic}
    # Explicit initial localization is a prior; no simulator truth is consulted here.
    for name,nodes in [('lifecycle_manager_localization',localization_nodes),('lifecycle_manager_navigation',NAVIGATION_NODES)]:
        cfg[name]={'ros__parameters':{'use_sim_time':True,'autostart':True,'node_names':nodes,'bond_timeout':4.0}}
    return cfg,metadata
