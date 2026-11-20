"""Native Nav2 short-goal/cancel acceptance; simulator truth is evaluation only."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
import uuid


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def measurement_window(state, session, identity, now):
    """Validate the existing producer-clock window without changing its limits."""
    if Path(state.get('session_directory', '')).resolve() != session.resolve():
        raise RuntimeError('Clock gate belongs to a different session directory')
    if state.get('phase') == 'waiting_for_warmup':
        return None
    if state.get('phase') != 'measuring':
        raise RuntimeError('Clock gate is not measuring: '+str(state.get('phase')))
    if state.get('session_identity') != list(identity):
        raise RuntimeError('Clock gate belongs to a different simulation epoch')
    keys = ('window_start_monotonic_s', 'window_end_monotonic_s',
            'window_start_sim_time_s', 'requested_wall_seconds')
    if not all(isinstance(state.get(k), (int, float)) and not isinstance(state[k], bool)
               and math.isfinite(state[k]) for k in keys):
        raise RuntimeError('Clock gate has invalid window timestamps')
    start, finish = state['window_start_monotonic_s'], state['window_end_monotonic_s']
    if state['requested_wall_seconds'] != 60 or abs(finish-start-60) > 1.e-6:
        raise RuntimeError('Action acceptance requires the unchanged 60 s clock window')
    if now >= finish:
        raise TimeoutError('Clock measurement window ended before goal dispatch')
    return state if now >= start else None


def parameter_number(value):
    """Decode only numeric ROS parameter types used by deployed motion limits."""
    if value.type == 2:
        return int(value.integer_value)
    if value.type == 3:
        return float(value.double_value)
    if value.type == 7:
        return [int(item) for item in value.integer_array_value]
    if value.type == 8:
        return [float(item) for item in value.double_array_value]
    raise ValueError('Expected numeric deployed ROS parameter, got type '+str(value.type))


def same_numbers(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and set(actual)==set(expected) and all(
            same_numbers(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual)==len(expected) and all(
            same_numbers(a,b) for a,b in zip(actual,expected))
    return (type(actual) in (int,float) and type(expected) in (int,float)
            and math.isfinite(actual) and math.isfinite(expected)
            and abs(actual-expected)<=1.e-12)


def deployed_motion_configuration(canonical, navigation, bridge, actual_nodes, topics, remaps):
    """Compare live node parameters and live vehicle limits to the same source bytes."""
    expected_nodes={
        'controller_server': {'FollowPath.'+name:value for name,value in canonical['controller_limits'].items()},
        'velocity_smoother':canonical['smoother_limits'],
    }
    if not same_numbers(actual_nodes,expected_nodes):
        raise AssertionError('Running Nav2 motion limits differ from the manual vehicle profile')
    live=navigation.get('drive_profile') or {}
    if (live.get('source_sha256')!=canonical['source_sha256']
            or Path(live.get('source_path','')).resolve()!=Path(canonical['source_path']).resolve()
            or not same_numbers(live.get('vehicle_control'),canonical['vehicle_control'])
            or not same_numbers(live.get('controller_limits'),canonical['controller_limits'])
            or not same_numbers(live.get('smoother_limits'),canonical['smoother_limits'])):
        raise AssertionError('Navigation runtime drive identity differs from canonical source')
    expected_remaps={name:[list(pair) for pair in values] for name,values in remaps.items()}
    if (live.get('smoother_frame')!='base_link' or live.get('velocity_smoother_active') is not True
            or live.get('adapter_clock')!='ROS /clock' or live.get('topics')!=topics
            or live.get('topic_remaps')!=expected_remaps):
        raise AssertionError('Navigation runtime does not declare the approved body-frame topic chain')
    drive=bridge.get('drive') or {}
    if drive.get('output_velocity_shaper')!='shared_drive_profile_v1':
        raise AssertionError('Bridge vehicle does not report the shared manual/navigation output shaper')
    control=canonical['vehicle_control']
    if len(control)!=27 or not same_numbers(drive.get('control_parameters'),control):
        raise AssertionError('Actual vehicle control parameters differ from the 27 manual profile values')
    return dict(source_path=canonical['source_path'],source_sha256=canonical['source_sha256'],
                actual_node_parameters=actual_nodes,expected_node_parameters=expected_nodes,
                actual_vehicle_control=drive['control_parameters'],
                output_velocity_shaper=drive['output_velocity_shaper'],
                body_frame='aim_sentry/base_link',smoother_frame=live['smoother_frame'],
                topics=topics,topic_remaps=expected_remaps,command_chain=live.get('command_chain'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--domain', type=int, default=174)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    clock_state_path = args.session/'clock/measurement-state.json'
    clock_window_required = clock_state_path.is_file()
    scene_path = args.session/'scene/scene.json'
    scene = read_json(scene_path)
    spawn = next(s for s in scene['spawns'] if s['id'] == 'default')
    origin = list(spawn['position_m'][:2])
    yaw = float(spawn['yaw_rad'])
    target = [origin[0]+math.cos(yaw), origin[1]+math.sin(yaw)]
    os.environ['ROS_DOMAIN_ID'] = str(args.domain)
    from rmsim_bridge.native_transport import configure_navigation_transport
    configure_navigation_transport()
    import rclpy
    from rclpy.node import Node
    from rclpy.action import ActionClient
    from rclpy.qos import qos_profile_sensor_data
    from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped, TwistStamped
    from nav_msgs.msg import Odometry, Path as NavPath
    from nav2_msgs.action import NavigateToPose
    from rosgraph_msgs.msg import Clock
    from std_msgs.msg import String
    from unique_identifier_msgs.msg import UUID
    from rcl_interfaces.srv import GetParameters
    from rmsim_bridge.navigation_drive_profile import load_drive_profile, velocity_routes
    from rmsim_bridge.service_poll import ReadOnlyServicePoll

    canonical=load_drive_profile(Path(__file__).resolve().parents[3]/'assets/vehicles/aim_sentry/drive-profile.json')
    expected_topics,expected_remaps=velocity_routes('aim_sentry')
    provenance=read_json(args.session/'runtime-provenance.json')

    rclpy.init()
    node = Node('rmsim_mujoco_navigation_smoke')
    client = ActionClient(node, NavigateToPose, 'navigate_to_pose')
    client_created_monotonic_s = time.monotonic()
    parameter_clients={}
    parameter_queries={}
    actual_node_parameters={}
    parameter_names={
        'controller_server':['FollowPath.'+name for name in canonical['controller_limits']],
        'velocity_smoother':list(canonical['smoother_limits']),
    }
    active = None
    active_result = None
    current = dict(nav=None, diag=None, truth=None, amcl=None, odom=None,
                   path_points=0, phase='waiting', clock_ns=None)
    trace = {name: [] for name in ('truth', 'amcl', 'odometry', 'clock', 'performance', 'commands', 'paths', 'events')}
    warmup_trace = {name: [] for name in trace}
    measurement_started = not clock_window_required
    report = dict(schema='rmsim_mujoco_navigation_smoke_v1', status='running', checks=[],
                  scope='Windows native ROS2 / Point-LIO / AMCL / Nav2; short goal and cancel/stop',
                  truth_role='independent endpoint, braking and drift evaluation; never used for goals, localization or cancel trigger',
                  cancel_trigger='AMCL displacement greater than 0.15 m after return-goal acceptance',
                  target_source='declared scene default spawn plus 1 m along its declared yaw',
                  declared_spawn_xy=origin, short_goal_xy=target,
                  scene_sha256=hashlib.sha256(scene_path.read_bytes()).hexdigest(),
                  script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  command=[sys.executable, *sys.argv], cleanup_errors=[],
                  clock_window_required=clock_window_required,
                  action_client={'created_monotonic_s': client_created_monotonic_s,
                                 'server_ready_monotonic_s': None},
                  deployed_configuration_queries={},
                  canonical_drive_profile=canonical,
                  trace_policy='All pre-gate receipts retained in warmup-trace.json; all post-gate receipts retained in trace.json')

    def record(kind, row):
        (trace if measurement_started else warmup_trace)[kind].append(row)

    def context():
        d = current['diag'] or {}
        return dict(received_monotonic_s=time.monotonic(), sim_time_ns=d.get('sim_time_ns'),
                    session=d.get('session'), epoch=d.get('epoch'), probe_phase=current['phase'])

    def stop_policy_observation():
        nav = current['nav'] or {}
        return dict(nav_received_monotonic_s=current.get('nav_received_monotonic_s'),
                    identity=list(nav.get('identity') or []),
                    velocity_stop_policy=nav.get('velocity_stop_policy'),
                    velocity_stop_latched=nav.get('velocity_stop_latched'),
                    velocity_stop_stamp_ns=nav.get('velocity_stop_stamp_ns'),
                    velocity_adapter_counts=dict(nav.get('velocity_adapter_counts') or {}))

    def stamp(header):
        return header.stamp.sec*1000000000+header.stamp.nanosec

    def pose_record(header, pose):
        p, q = pose.position, pose.orientation
        return dict(**context(), stamp_ns=stamp(header), frame_id=header.frame_id,
                    position=[p.x, p.y, p.z], quaternion_xyzw=[q.x, q.y, q.z, q.w])

    def on_pose(kind, message):
        pose = message.pose if kind == 'truth' else message.pose.pose
        row = pose_record(message.header, pose)
        current[kind if kind != 'odometry' else 'odom'] = row
        record(kind, row)

    def on_diag(message):
        d = json.loads(message.data)
        current['diag'] = d
        current['diag_received_monotonic_s']=time.monotonic()
        record('performance', dict(**context(), **{k: d.get(k) for k in
            ('connected', 'state_fresh', 'clock_source', 'physics_backend', 'dynamics_step_s',
             'fixed_step_s', 'simulation_rate', 'command', 'transport_error', 'command_error')}))
        if d.get('aim', {}).get('trigger_active'):
            report['unexpected_fire'] = True

    def on_clock(message):
        value = message.clock.sec*1000000000+message.clock.nanosec
        current['clock_ns'] = value
        record('clock', dict(received_monotonic_s=time.monotonic(), sim_time_ns=value,
                                   probe_phase=current['phase']))

    def on_command(message):
        record('commands', dict(**context(), stamp_ns=stamp(message.header),
            frame_id=message.header.frame_id,
            velocity_x_y_yaw=[message.twist.linear.x, message.twist.linear.y, message.twist.angular.z]))

    def on_path(message):
        positions = [[p.pose.position.x, p.pose.position.y, p.pose.position.z] for p in message.poses]
        current['path_points'] = max(current['path_points'], len(positions))
        record('paths', dict(**context(), stamp_ns=stamp(message.header),
                                   frame_id=message.header.frame_id, positions=positions))

    node.create_subscription(PoseStamped, '/rmsim/evaluation/aim_sentry/ground_truth',
                             lambda m: on_pose('truth', m), qos_profile_sensor_data)
    node.create_subscription(PoseWithCovarianceStamped, '/amcl_pose', lambda m: on_pose('amcl', m), 1)
    node.create_subscription(Odometry, '/odometry', lambda m: on_pose('odometry', m), qos_profile_sensor_data)
    node.create_subscription(String, '/rmsim/aim_sentry/navigation',
                             lambda m: current.update(nav=json.loads(m.data),nav_received_monotonic_s=time.monotonic()), 1)
    node.create_subscription(String, '/rmsim/aim_sentry/state', on_diag, 1)
    node.create_subscription(NavPath, '/plan', on_path, 1)
    node.create_subscription(Clock, '/clock', on_clock, qos_profile_sensor_data)
    node.create_subscription(TwistStamped, '/aim_sentry/cmd_vel', on_command, 1)

    def save():
        report.update(last_navigation=current['nav'], last_diagnostic=current['diag'],
                      last_truth=current['truth'], last_amcl=current['amcl'])
        (args.output/'verification.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')

    def check(name, passed, fatal=True, **details):
        report['checks'].append(dict(name=name, passed=bool(passed), **details))
        save()
        print(('PASS ' if passed else 'FAIL ')+name, flush=True)
        if not passed and fatal:
            raise AssertionError(name)

    def wait(predicate, seconds, label):
        deadline = time.monotonic()+seconds
        while not predicate():
            if time.monotonic() >= deadline:
                raise TimeoutError(label)
            rclpy.spin_once(node, timeout_sec=.01)

    def pump(seconds):
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=.01)

    def event(name, **details):
        row = dict(**context(), event=name, **details)
        record('events', row)
        print(json.dumps(row), flush=True)
        return row

    def goal_at(xy):
        goal = NavigateToPose.Goal()
        goal.pose.header.frame_id = 'aim_sentry/map'
        # Zero stamp asks Nav2 for latest TF, avoiding any wall/simulation epoch mix.
        goal.pose.pose.position.x = float(xy[0])
        goal.pose.pose.position.y = float(xy[1])
        goal.pose.pose.orientation.z = math.sin(yaw/2)
        goal.pose.pose.orientation.w = math.cos(yaw/2)
        return goal

    def send_goal(xy):
        nonlocal active, active_result
        if clock_window_required and measurement_window(
                read_json(clock_state_path), args.session, identity, time.monotonic()) is None:
            raise RuntimeError('Goal dispatch attempted before the clock measurement gate')
        goal_uuid = UUID(uuid=list(uuid.uuid4().bytes))
        event('goal_request', requested_xy=xy, goal_id=[int(value) for value in goal_uuid.uuid],
              goal_uuid=bytes(goal_uuid.uuid).hex())
        future = client.send_goal_async(goal_at(xy), goal_uuid=goal_uuid)
        wait(future.done, 15, 'Nav2 goal response deadline')
        handle = future.result()
        if bytes(handle.goal_id.uuid) != bytes(goal_uuid.uuid):
            raise RuntimeError('Goal response UUID does not match the owned request')
        active, active_result = handle, None
        event('goal_response', accepted=handle.accepted, goal_id=[int(value) for value in handle.goal_id.uuid])
        return handle

    def truth_for_evaluation():
        value = current['truth']
        check('evaluation_truth_fresh_'+current['phase'], value is not None and
              time.monotonic()-value['received_monotonic_s'] < 1.)
        return list(value['position'])

    def native_ready():
        server_ready = client.server_is_ready()
        if server_ready and report['action_client']['server_ready_monotonic_s'] is None:
            report['action_client']['server_ready_monotonic_s'] = time.monotonic()
            event('action_server_ready')
        configured=deployed_parameters_ready()
        return (current['nav'] and current['nav'].get('phase') == 'running'
                and current['diag'] and current['odom']
                and current['clock_ns'] is not None and server_ready and configured)

    def deployed_parameters_ready():
        nav=current['nav'] or {}
        owned=nav.get('pids') or {}
        graph=node.get_node_names_and_namespaces()
        for name,names in parameter_names.items():
            if name not in owned:
                continue
            if name not in parameter_clients:
                found=[namespace.rstrip('/')+'/'+node_name for node_name,namespace in graph if node_name==name]
                if not found:
                    continue
                if len(found)!=1:
                    raise RuntimeError('Ambiguous deployed Nav2 parameter service: '+name)
                endpoint=found[0]+'/get_parameters'
                parameter_clients[name]=node.create_client(GetParameters,endpoint)
                parameter_queries[name]=ReadOnlyServicePoll(2.)
                report['deployed_configuration_queries'][name]=dict(
                    node_fqn=found[0],owned_pid=owned[name],service=endpoint,
                    created_monotonic_s=time.monotonic(),names=names,attempts=[],
                    maximum_attempts=3,response_timeout_s=2.,
                    overall_readiness_deadline_monotonic_s=readiness_deadline)
            query=parameter_clients[name]
            detail=report['deployed_configuration_queries'][name]
            if name in actual_node_parameters:
                continue
            polling=parameter_queries[name]
            now=time.monotonic()
            if (len(detail['attempts'])>=3 and polling.future is not None
                    and not polling.future.done() and now-polling.started>=polling.timeout_s):
                detail['read_only_response_timeouts']=polling.timeouts+1
                polling.clear(query)
                raise TimeoutError('Three read-only deployed-parameter responses timed out: '+name)
            previous=polling.future
            response=polling.poll(query,GetParameters.Request(names=names),now)
            if polling.future is not None and polling.future is not previous:
                detail['attempts'].append(dict(requested_monotonic_s=polling.started))
            detail['attempt_count']=len(detail['attempts'])
            detail['read_only_response_timeouts']=polling.timeouts
            if response is not None:
                if len(response.values)!=len(names):
                    raise RuntimeError('Incomplete deployed ROS parameter response: '+name)
                actual_node_parameters[name]={key:parameter_number(value) for key,value in zip(names,response.values)}
                detail['received_monotonic_s']=time.monotonic()
                detail['ros_parameter_types']=[int(value.type) for value in response.values]
                detail['values']=actual_node_parameters[name]
                event('deployed_parameter_response',node_fqn=detail['node_fqn'],service=detail['service'])
        return len(actual_node_parameters)==len(parameter_names)

    def clock_ready():
        state = measurement_window(read_json(clock_state_path), args.session, identity, time.monotonic())
        if state is None:
            return False
        start = state['window_start_monotonic_s']
        if not (client_created_monotonic_s < start and
                report['action_client']['server_ready_monotonic_s'] < start):
            raise RuntimeError('ActionClient discovery did not complete during existing clock warmup')
        if not all(detail.get('received_monotonic_s',float('inf'))<start
                   for detail in report['deployed_configuration_queries'].values()):
            raise RuntimeError('Deployed motion parameters were not read during existing clock warmup')
        start_ns = state['window_start_sim_time_s']*1.e9
        if current['diag'].get('sim_time_ns', -1) < start_ns or current['clock_ns'] < start_ns:
            return False
        report['clock_measurement_gate'] = state
        return True

    try:
        if provenance.get('navigation_drive_profile',{}).get('source_sha256')!=canonical['source_sha256']:
            raise RuntimeError('Console provenance is not bound to the current manual drive profile')
        event('action_client_created', created_monotonic_s=client_created_monotonic_s)
        readiness_deadline = time.monotonic()+30
        report['readiness_deadline_monotonic_s']=readiness_deadline
        wait(native_ready, 30, 'Native navigation readiness')
        identity = list(current['nav']['identity'])
        if clock_window_required:
            wait(clock_ready, max(0., readiness_deadline-time.monotonic()), 'Clock measurement gate readiness')
            measurement_started = True
            event('clock_measurement_gate_open',
                  window_start_monotonic_s=report['clock_measurement_gate']['window_start_monotonic_s'],
                  window_end_monotonic_s=report['clock_measurement_gate']['window_end_monotonic_s'])
            wait(lambda: bool(trace['performance']) and bool(trace['clock']),
                 max(0., readiness_deadline-time.monotonic()), 'Fresh measurement telemetry')
        d = current['diag']
        if load_drive_profile(canonical['source_path'])['source_sha256']!=canonical['source_sha256']:
            raise RuntimeError('Manual drive profile changed during deployment verification')
        configuration=deployed_motion_configuration(canonical,current['nav'],d,actual_node_parameters,
                                                   expected_topics,expected_remaps)
        fresh=(d.get('state_fresh') is True and d.get('connected') is True
               and [d.get('session'),d.get('epoch')]==identity
               and current['nav'].get('identity')==identity
               and time.monotonic()-current.get('diag_received_monotonic_s',0)<1.
               and time.monotonic()-current.get('nav_received_monotonic_s',0)<1.)
        check('deployed_manual_navigation_limits',fresh,
              validated_monotonic_s=time.monotonic(),bridge_sim_time_ns=d.get('sim_time_ns'),
              identity=identity,**configuration)
        check('native_mujoco_stack_ready', d.get('state_fresh') is True and
              d.get('physics_backend') == 'mujoco' and abs(float(d.get('dynamics_step_s', 0))-.001) < 1e-12,
              identity=identity, physics_backend=d.get('physics_backend'), dynamics_step_s=d.get('dynamics_step_s'))
        current['phase'] = 'short_goal'
        active = send_goal(target)
        check('short_goal_accepted', active.accepted)
        active_result = active.get_result_async()
        wait(active_result.done, 45, 'Short Nav2 goal deadline')
        pump(.2)
        outcome = active_result.result()
        actual = truth_for_evaluation()
        error = math.dist(actual[:2], target)
        event('short_goal_result', status=int(outcome.status), truth=actual, error_m=error)
        check('short_goal_actual_arrival', outcome.status == 4 and error < .25,
              fatal=False, status=int(outcome.status), error_m=error, strict_threshold_m=.25)
        plans = [p for p in trace['paths'] if p['probe_phase'] == 'short_goal']
        plan = plans[0] if plans else {'positions': [], 'frame_id': None}
        points = plan['positions']
        valid_path = len(points) >= 2 and plan['frame_id'] == 'aim_sentry/map' and all(
            math.isfinite(value) for position in points for value in position)
        start_error = math.dist(points[0][:2], origin) if valid_path else None
        end_error = math.dist(points[-1][:2], target) if valid_path else None
        check('original_planner_short_path', valid_path and start_error < .25 and end_error < .25,
              fatal=False, points=len(points), frame_id=plan['frame_id'],
              first_point=points[0] if points else None, last_point=points[-1] if points else None,
              declared_start_xy=origin, requested_goal_xy=target,
              start_error_m=start_error, end_error_m=end_error,
              structure_contract='A straight short path may consist of exactly two finite endpoints in the requested map frame')
        check('amcl_available_for_cancel_trigger', current['amcl'] is not None)
        current['phase'] = 'return_goal'
        active = send_goal(origin)
        check('return_goal_accepted', active.accepted)
        active_result = active.get_result_async()
        estimated_start = current['amcl']['position'][:2]
        odometry_start = current['odom']['position'][:]
        return_truth_start = truth_for_evaluation()
        wait(lambda: math.dist(estimated_start, current['amcl']['position'][:2]) > .15
             or active_result.done(), 20, 'AMCL movement before cancellation')
        check('return_goal_moving_at_cancel', not active_result.done(),
              estimated_displacement_m=math.dist(estimated_start, current['amcl']['position'][:2]))
        current['phase'] = 'cancel'
        before = truth_for_evaluation()
        report['cancel_request'] = event('cancel_request', independent_truth=before,
            amcl=current['amcl']['position'], independent_return_displacement_m=math.dist(return_truth_start, before),
            odometry_displacement_m=math.dist(odometry_start, current['odom']['position']),
            latest_vehicle_command=trace['commands'][-1] if trace['commands'] else None,
            stop_policy_observation=stop_policy_observation())
        future = active.cancel_goal_async()
        wait(future.done, 10, 'Nav2 cancel response deadline')
        event('cancel_response', return_code=int(future.result().return_code))
        wait(active_result.done, 10, 'Nav2 canceled result deadline')
        event('cancel_result', status=int(active_result.result().status))
        check('native_action_cancelled', active_result.result().status == 5,
              status=int(active_result.result().status), cancel_return_code=int(future.result().return_code))
        current['phase'] = 'cancel_settle'
        pump(4)
        stopped = truth_for_evaluation()
        event('stop_observation', independent_truth=stopped,
              stop_policy_observation=stop_policy_observation())
        current['phase'] = 'cancel_drift'
        pump(2)
        final = truth_for_evaluation()
        event('drift_observation', independent_truth=final)
        braking, drift = math.dist(before, stopped), math.dist(stopped, final)
        check('cancelled_vehicle_stopped', braking < .2 and drift < .02, fatal=False,
              braking_m=braking, drift_m=drift, strict_braking_threshold_m=.2, strict_drift_threshold_m=.02)
        check('independent_estimators_observed', len(trace['odometry']) > 10 and len(trace['amcl']) > 1,
              fatal=False, point_lio_odometry_samples=len(trace['odometry']), amcl_pose_samples=len(trace['amcl']))
        clocks = trace['clock']
        check('ros_clock_monotonic', len(clocks) > 10 and all(b['sim_time_ns'] >= a['sim_time_ns']
              for a, b in zip(clocks, clocks[1:])), fatal=False, samples=len(clocks))
        if len(clocks) > 1:
            report['ros_clock_receipt_rate'] = (clocks[-1]['sim_time_ns']-clocks[0]['sim_time_ns'])*1e-9/(clocks[-1]['received_monotonic_s']-clocks[0]['received_monotonic_s'])
        check('session_backend_and_no_fire_preserved', current['nav']['identity'] == identity and
              all([r.get('session'), r.get('epoch')] == identity and r.get('physics_backend') == 'mujoco'
                  for r in trace['performance']) and not report.get('unexpected_fire'), fatal=False)
        report['status'] = 'passed' if all(c['passed'] for c in report['checks']) else 'failed'
    except Exception as error:
        report.update(status='failed', error=repr(error))
        print('MUJOCO_NAVIGATION_FAILED '+repr(error), flush=True)
    finally:
        if active is not None and active.accepted and (active_result is None or not active_result.done()):
            try:
                future = active.cancel_goal_async()
                wait(future.done, 5, 'Owned goal cleanup cancellation')
            except Exception as error:
                report['cleanup_errors'].append(repr(error))
                report['status'] = 'failed'
        report['finished_monotonic_s'] = time.monotonic()
        try:
            save()
            (args.output/'trace.json').write_text(json.dumps(trace, allow_nan=False)+'\n', encoding='utf-8')
            (args.output/'warmup-trace.json').write_text(json.dumps(warmup_trace, allow_nan=False)+'\n', encoding='utf-8')
        finally:
            for name,query in parameter_clients.items():
                parameter_queries[name].clear(query)
                node.destroy_client(query)
            client.destroy()
            node.destroy_node()
            rclpy.shutdown()
    print('MUJOCO_NAVIGATION '+report['status'], flush=True)
    return 0 if report['status'] == 'passed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
