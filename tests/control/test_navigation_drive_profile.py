"""Shared vehicle limits, body-frame routing and callback freshness without ROS nodes."""
import ast
import copy
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'ros2_ws/src/rmsim_bridge'))
from rmsim_bridge.navigation_drive_profile import load_drive_profile,velocity_routes
from rmsim_bridge.navigation_config import prepare,NAVIGATION_NODES


def twist(x=0.,y=0.,yaw=0.):
    return SimpleNamespace(linear=SimpleNamespace(x=x,y=y,z=0.),
                           angular=SimpleNamespace(x=0.,y=0.,z=yaw))


class Stamped:
    def __init__(self):
        self.header=SimpleNamespace(frame_id='',stamp=SimpleNamespace(sec=0,nanosec=0))
        self.twist=twist()


def adapter_fixture():
    # Execute the production callbacks directly; no ROS node or executor is started.
    path=ROOT/'ros2_ws/src/rmsim_bridge/rmsim_bridge/navigation_runner.py'
    tree=ast.parse(path.read_text(encoding='utf-8'))
    original=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='NavigationRunner')
    names={'velocity_adapter_ready','reset_velocity_adapter','finite_planar_velocity','stop_velocity',
           'on_body_velocity','publish_body_velocity','on_smoothed_body_velocity','observe',
           'on_idle_spin_action_status','publish_navigation_brake','on_navigation_velocity'}
    body=[node for node in original.body if isinstance(node,ast.FunctionDef) and node.name in names]
    test_class=ast.ClassDef(name='Adapter',bases=[],keywords=[],body=body,decorator_list=[])
    statuses=SimpleNamespace(STATUS_ACCEPTED=1,STATUS_EXECUTING=2,STATUS_CANCELING=3)
    namespace={'math':math,'json':json,'time':time,'Twist':twist,'TwistStamped':Stamped,
               'GoalStatus':statuses}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[test_class],type_ignores=[])),str(path),'exec'),namespace)
    adapter=namespace['Adapter']()
    adapter.motion_released=True
    adapter.robot='aim_sentry';adapter.drive_identity={'enabled':True};adapter.nav_started=True
    adapter.state={'connected':True,'state_fresh':True,'session':'session-a','epoch':0}
    adapter.identity=['session-a',0];adapter.now_ns=2_000_000_000
    adapter.get_clock=lambda:SimpleNamespace(now=lambda:SimpleNamespace(nanoseconds=adapter.now_ns))
    adapter.smoother_config={'velocity_timeout':1.,'smoothing_frequency':20.}
    adapter.inputs=[];adapter.outputs=[]
    adapter.idle_spin_active_goals={}
    adapter.navigation_executing_goals={}
    adapter.body_velocity_pub=SimpleNamespace(publish=adapter.inputs.append)
    adapter.public_velocity_pub=SimpleNamespace(publish=adapter.outputs.append)
    adapter.velocity_adapter_counts=dict(accepted_inputs=0,published_outputs=0,rejected_inputs=0,rejected_outputs=0,
                                         immediate_stops=0,suppressed_smoother_outputs=0,rejected_stop_releases=0)
    adapter.reset_velocity_adapter()
    return adapter


class NavigationDriveProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)
        self.profile=ROOT/'assets/vehicles/aim_sentry/drive-profile.json'
        self.reference=ROOT/'ros2_ws/src/pb2025_nav_bringup/config/reality/nav2_params.yaml'
        self.map=self.directory/'map.yaml';self.map.write_text('image: map.pgm\n')
        (self.directory/'metadata.json').write_text(json.dumps({'map_id':'fixture','source_md5':'1'*32}))

    def adapted(self,mode='simulation',profile=None):
        return prepare(self.reference,self.map,'aim_sentry',[.6,-.4,.15],mode,
                       drive_profile=str(profile or self.profile))

    def test_velocity_waits_for_display_release(self):
        adapter=adapter_fixture()
        adapter.motion_released=False
        adapter.on_navigation_velocity(twist(1.0))
        self.assertIsNone(adapter.navigation_motion_ns)
        adapter.motion_released=True
        adapter.on_navigation_velocity(twist(1.0))
        self.assertEqual(adapter.navigation_motion_ns,adapter.now_ns)

    def test_standard_vehicle_caps_and_all_shaper_controls_are_source_bound(self):
        data=load_drive_profile(self.profile);control=json.loads(self.profile.read_text())['control']
        self.assertEqual(data['vehicle_control'],control)
        self.assertEqual(data['source_sha256'],hashlib.sha256(self.profile.read_bytes()).hexdigest())
        self.assertEqual(data['smoother_limits']['max_velocity'],[5.,4.,math.pi/2])
        self.assertEqual(data['smoother_limits']['min_velocity'],[-2.5,-4.,-math.pi/2])
        self.assertEqual(data['smoother_limits']['max_accel'],[2.,2.,3.])
        self.assertEqual(data['smoother_limits']['max_decel'],[-4.,-4.,-5.])
        self.assertEqual([control[key] for key in ('BrakeDecelerationMps2','LinearJerkMps3',
            'DecelerationJerkMps3','LateralAccelerationMps2','DirectionJerkMps3')],[7.,6.,20.,8.,70.])

    def test_both_simulated_behavior_presets_use_same_caps_and_keep_original_nodes(self):
        for mode in ('simulation','reality'):
            with self.subTest(mode=mode):
                baseline,_=prepare(self.reference,self.map,'aim_sentry',[.6,-.4,.15],mode)
                cfg,metadata=self.adapted(mode)
                identity=metadata['drive_profile']
                self.assertEqual(cfg['lifecycle_manager_navigation']['ros__parameters']['node_names'],NAVIGATION_NODES)
                self.assertIn('velocity_smoother',NAVIGATION_NODES)
                original=copy.deepcopy(baseline['controller_server']['ros__parameters']['FollowPath'])
                original.update(identity['controller_limits'])
                original.update(collision_deceleration=4.0, tracking_deceleration=4.0 if mode=='simulation' else 2.0,
                                collision_reaction_time=.45, external_rotation_possible=True)
                self.assertEqual(cfg['controller_server']['ros__parameters']['FollowPath'],original)
                smoother=copy.deepcopy(baseline['velocity_smoother']['ros__parameters'])
                smoother.update(identity['smoother_limits'])
                self.assertEqual(cfg['velocity_smoother']['ros__parameters'],smoother)
                local = baseline['local_costmap']['local_costmap']['ros__parameters']
                local['always_send_full_costmap'] = True
                self.assertTrue(cfg['fake_vel_transform']['ros__parameters']['project_heading_to_stamp'])
                for name in ('planner_server','local_costmap','global_costmap','amcl','behavior_server'):
                    self.assertEqual(cfg[name],baseline[name])

    def test_optional_profile_keeps_standalone_source_values_and_file_bytes(self):
        sources=[self.profile,self.reference,self.reference.parent.parent/'simulation/nav2_params.yaml']
        before={path:path.read_bytes() for path in sources}
        cfg,metadata=prepare(self.reference,self.map,'aim_sentry',[.6,-.4,.15],'reality')
        self.assertNotIn('drive_profile',metadata)
        self.assertEqual(cfg['controller_server']['ros__parameters']['FollowPath']['v_linear_max'],1.)
        self.assertEqual(cfg['velocity_smoother']['ros__parameters']['max_velocity'],[1.,1.,3.])
        self.adapted()
        self.assertEqual(before,{path:path.read_bytes() for path in sources})

    def test_different_vehicle_values_propagate_without_second_numeric_profile(self):
        data=json.loads(self.profile.read_text());control=data['control']
        control.update(MaxForwardSpeedMps=2.2,MaxReverseSpeedMps=1.1,MaxLateralSpeedMps=1.8,
                       LinearAccelerationMps2=1.7,LinearDecelerationMps2=3.8,
                       ManualYawRateRadS=.9,AngularAccelerationRadS2=2.3,AngularDecelerationRadS2=4.2)
        path=self.directory/'vehicle-drive.json';path.write_text(json.dumps(data))
        cfg,metadata=self.adapted(profile=path)
        smooth=cfg['velocity_smoother']['ros__parameters']
        self.assertEqual(smooth['max_velocity'],[2.2,2.2,.9])
        self.assertEqual(smooth['min_velocity'],[-2.2,-2.2,-.9])
        self.assertEqual(smooth['max_accel'],[math.hypot(1.7,8.)]*2+[2.3])
        self.assertEqual(smooth['max_decel'],[-math.hypot(3.8,8.)]*2+[-4.2])
        controller = cfg['controller_server']['ros__parameters']['FollowPath']
        self.assertEqual(controller['v_linear_min'], -1.1)
        self.assertEqual(controller['v_linear_max'], 2.2)
        self.assertEqual(metadata['drive_profile']['vehicle_control'],control)

    def test_invalid_profile_never_silently_falls_back(self):
        original=json.loads(self.profile.read_text())
        for key,value in [('MaxForwardSpeedMps',True),('LinearJerkMps3',float('nan')),
                          ('ManualYawRateRadS',-1.),('DirectionJerkMps3',None)]:
            with self.subTest(key=key):
                data=copy.deepcopy(original);data['control'][key]=value
                path=self.directory/'bad.json';path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):self.adapted(profile=path)

    def test_body_routing_is_acyclic_and_preserves_behavior_configured_frame(self):
        cfg,metadata=self.adapted();identity=metadata['drive_profile']
        topics,remaps=velocity_routes('aim_sentry')
        self.assertEqual(len(set(topics.values())),5)
        fake=cfg['fake_vel_transform']['ros__parameters']
        self.assertEqual(identity['smoother_frame'], 'navigation')
        self.assertEqual(fake['input_cmd_vel_topic'],identity['topics']['navigation_smoothed'])
        self.assertEqual(identity['topics']['public'],topics['public'])
        self.assertNotIn('public_odometry',identity['topics'])
        self.assertEqual(fake['output_cmd_vel_topic'],topics['body_stamped'])
        self.assertNotEqual(fake['output_cmd_vel_topic'],topics['public'])
        self.assertEqual(cfg['behavior_server']['ros__parameters']['robot_base_frame'],'aim_sentry/navigation_frame')
        self.assertEqual(remaps['controller_server'],remaps['behavior_server'])
        self.assertEqual(identity['topic_remaps']['controller_server'],remaps['controller_server'])
        self.assertEqual(identity['topic_remaps']['behavior_server'],remaps['behavior_server'])
        self.assertEqual(identity['topic_remaps']['velocity_smoother'],[
            ('cmd_vel', topics['navigation_raw']), ('cmd_vel_smoothed', identity['topics']['navigation_smoothed'])])
        # The final command uses the same local axes throughout a rotation.
        caps=identity['vehicle_control']
        for angle in (0.,math.pi/2,math.pi,-math.pi/2):
            for x,y in ((5.,0.),(-2.5,0.),(0.,4.),(0.,-4.)):
                nav_x=x*math.cos(angle)-y*math.sin(angle)
                nav_y=x*math.sin(angle)+y*math.cos(angle)
                body=(nav_x*math.cos(angle)+nav_y*math.sin(angle),-nav_x*math.sin(angle)+nav_y*math.cos(angle))
                self.assertAlmostEqual(body[0],x);self.assertAlmostEqual(body[1],y)
                self.assertGreaterEqual(body[0]+1e-12,-caps['MaxReverseSpeedMps'])
                self.assertLessEqual(body[1]-1e-12,caps['MaxLateralSpeedMps'])

    def test_callback_preserves_values_body_frame_and_actual_sim_clock_stamp(self):
        adapter=adapter_fixture();message=Stamped()
        message.header.frame_id='aim_sentry/base_link';message.header.stamp.sec=2
        message.twist=twist(4.,-.5,.3);adapter.on_body_velocity(message)
        self.assertEqual(adapter.inputs,[message.twist])
        adapter.now_ns=2_050_000_123;filtered=twist(3.,-.4,.2)
        adapter.on_smoothed_body_velocity(filtered)
        self.assertEqual(len(adapter.outputs),1);output=adapter.outputs[0]
        self.assertIs(output.twist,filtered)
        self.assertEqual(output.header.frame_id,'aim_sentry/base_link')
        self.assertEqual((output.header.stamp.sec,output.header.stamp.nanosec),(2,50_000_123))

    def test_bad_frame_nan_stale_and_reordered_inputs_do_not_refresh_command(self):
        adapter=adapter_fixture()
        for frame,sec,nsec,x in [('other/base_link',2,0,1.),('aim_sentry/base_link',2,0,float('nan')),
                                ('aim_sentry/base_link',0,1,1.),('aim_sentry/base_link',3,0,1.)]:
            message=Stamped();message.header.frame_id=frame;message.header.stamp.sec=sec
            message.header.stamp.nanosec=nsec;message.twist=twist(x)
            adapter.on_body_velocity(message)
        self.assertEqual(adapter.inputs,[])
        self.assertIsNone(adapter.body_input_ns)
        message=Stamped();message.header.frame_id='aim_sentry/base_link';message.header.stamp.sec=2
        adapter.on_body_velocity(message);message.header.stamp.sec=1;message.header.stamp.nanosec=999_999_999
        adapter.on_body_velocity(message)
        self.assertEqual(len(adapter.inputs),1)

    def test_epoch_and_reset_block_buffered_outputs_until_fresh_input(self):
        adapter=adapter_fixture();message=Stamped();message.header.frame_id='aim_sentry/base_link';message.header.stamp.sec=2
        message.twist=twist(1.)
        adapter.on_body_velocity(message);adapter.state['epoch']=1
        adapter.on_smoothed_body_velocity(twist(1.));self.assertEqual(adapter.outputs,[])
        adapter.reset_velocity_adapter();adapter.identity=['session-a',1]
        adapter.on_smoothed_body_velocity(twist(1.));self.assertEqual(adapter.outputs,[])
        adapter.on_body_velocity(message);adapter.on_smoothed_body_velocity(twist(0.))
        self.assertEqual(len(adapter.outputs),1)

    def test_timeout_ramp_remains_owned_by_original_smoother_and_no_heartbeat_republish(self):
        adapter=adapter_fixture();message=Stamped();message.header.frame_id='aim_sentry/base_link';message.header.stamp.sec=2
        message.twist=twist(1.)
        adapter.on_body_velocity(message);adapter.now_ns=3_100_000_000
        adapter.on_smoothed_body_velocity(twist(.2))
        adapter.now_ns=3_150_000_000;adapter.on_smoothed_body_velocity(twist())
        self.assertEqual([value.twist.linear.x for value in adapter.outputs],[.2,0.])
        adapter.now_ns=4_000_000_000
        self.assertEqual(len(adapter.outputs),2)


class NavigationStopIntentTests(unittest.TestCase):
    @staticmethod
    def target(adapter,x=0.,y=0.,yaw=0.,stamp=None,frame='aim_sentry/base_link'):
        message=Stamped();message.header.frame_id=frame
        stamp=adapter.now_ns if stamp is None else stamp
        message.header.stamp.sec=stamp//1_000_000_000
        message.header.stamp.nanosec=stamp%1_000_000_000
        message.twist=twist(x,y,yaw)
        return message

    def assert_zero(self,message):
        self.assertEqual((message.twist.linear.x,message.twist.linear.y,message.twist.linear.z,
                          message.twist.angular.x,message.twist.angular.y,message.twist.angular.z),
                         (0.,0.,0.,0.,0.,0.))

    def test_explicit_zero_reaches_public_brake_without_waiting_for_smoother(self):
        adapter=adapter_fixture();adapter.now_ns=2_010_000_123
        adapter.on_body_velocity(self.target(adapter,stamp=2_000_000_000))
        self.assertEqual(len(adapter.outputs),1);self.assert_zero(adapter.outputs[0])
        self.assertEqual(adapter.outputs[0].header.frame_id,'aim_sentry/base_link')
        self.assertEqual((adapter.outputs[0].header.stamp.sec,adapter.outputs[0].header.stamp.nanosec),
                         (2,10_000_123))
        self.assertEqual(adapter.body_stop_ns,2_000_000_000)
        self.assertEqual(adapter.velocity_adapter_counts['immediate_stops'],1)
        self.assertEqual(len(adapter.inputs),1)

    def test_action_cancels_without_a_velocity_zero_and_rejects_smoother_tail(self):
        adapter=adapter_fixture()
        def status(action, phase):
            message = SimpleNamespace(status_list=[SimpleNamespace(status=phase)])
            adapter.on_idle_spin_action_status(action, message)

        status('navigate_to_pose', 2)
        status('follow_path', 2)
        adapter.on_navigation_velocity(twist(2.))
        status('follow_path', 4)
        self.assertEqual(adapter.outputs, [])
        status('navigate_to_pose', 3)
        self.assertTrue(adapter.navigation_result_brake)
        self.assert_zero(adapter.outputs[-1])
        adapter.now_ns += 25_000_000
        adapter.on_navigation_velocity(twist(2.))
        self.assertIsNone(adapter.navigation_motion_ns)
        adapter.on_body_velocity(self.target(adapter,2.))
        adapter.on_smoothed_body_velocity(twist(2.))
        self.assert_zero(adapter.outputs[-1])
        status('navigate_to_pose', 5)
        status('navigate_to_pose', 1)
        self.assertFalse(adapter.navigation_result_brake)
        adapter.on_navigation_velocity(twist(2.))
        self.assertEqual(adapter.navigation_motion_ns, adapter.now_ns)

    def test_stop_threshold_matches_bridge_and_normalizes_all_axes_to_exact_zero(self):
        adapter=adapter_fixture()
        adapter.on_body_velocity(self.target(adapter,9.999e-5,-9.999e-5,9.999e-5))
        self.assert_zero(adapter.outputs[-1])
        self.assertEqual((adapter.inputs[-1].linear.x,adapter.inputs[-1].linear.y,
                          adapter.inputs[-1].angular.z),(0.,0.,0.))
        for axis in range(3):
            with self.subTest(axis=axis):
                adapter=adapter_fixture();values=[0.,0.,0.];values[axis]=1e-4
                adapter.on_body_velocity(self.target(adapter,*values))
                self.assertIsNone(adapter.body_stop_ns);self.assertEqual(adapter.outputs,[])

    def test_cancel_suppresses_complete_smoother_tail_until_newer_nonzero_target(self):
        adapter=adapter_fixture();adapter.on_body_velocity(self.target(adapter,1.))
        adapter.on_smoothed_body_velocity(twist(.8))
        adapter.now_ns+=50_000_000;adapter.on_body_velocity(self.target(adapter))
        for residual in (.6,.4,.2,.01):
            adapter.now_ns+=50_000_000;adapter.on_smoothed_body_velocity(twist(residual,-residual,.2))
        self.assertEqual(adapter.velocity_adapter_counts['suppressed_smoother_outputs'],4)
        for output in adapter.outputs[1:]:self.assert_zero(output)
        self.assertEqual(adapter.outputs[0].twist.linear.x,.8)
        adapter.now_ns+=50_000_000;resume=self.target(adapter,.7,-.3,.1)
        adapter.on_body_velocity(resume);self.assertIsNone(adapter.body_stop_ns)
        count=len(adapter.outputs);adapter.now_ns+=50_000_000
        filtered=twist(.1,-.05,.02);adapter.on_smoothed_body_velocity(filtered)
        self.assertEqual(len(adapter.outputs),count+1);self.assertIs(adapter.outputs[-1].twist,filtered)
        self.assertIs(adapter.inputs[-1],resume.twist)

    def test_stale_invalid_and_same_tick_nonzero_inputs_cannot_release_stop(self):
        adapter=adapter_fixture();adapter.on_body_velocity(self.target(adapter))
        stop_stamp=adapter.body_stop_ns
        cases=[dict(stamp=stop_stamp-1),dict(stamp=stop_stamp),
               dict(stamp=stop_stamp+1,frame='other/base_link'),dict(x=float('nan'),stamp=stop_stamp+1),
               dict(stamp=stop_stamp+50_000_001),dict(stamp=1)]
        for case in cases:
            with self.subTest(case=case):
                values={'x':1.};values.update(case)
                adapter.on_body_velocity(self.target(adapter,**values))
                self.assertEqual(adapter.body_stop_ns,stop_stamp)
                self.assertEqual(len(adapter.inputs),1)
                self.assertEqual(len(adapter.outputs),1)
        self.assertEqual(adapter.velocity_adapter_counts['rejected_stop_releases'],1)
        adapter.state['epoch']=1
        adapter.on_body_velocity(self.target(adapter,1.,stamp=stop_stamp+1))
        self.assertEqual(adapter.body_stop_ns,stop_stamp)
        self.assertEqual(len(adapter.inputs),1)

    def test_invalid_outputs_do_not_refresh_public_lease_while_stop_is_latched(self):
        adapter=adapter_fixture();adapter.on_body_velocity(self.target(adapter))
        adapter.on_smoothed_body_velocity(twist(float('nan')))
        invalid=twist();invalid.angular.y=.01;adapter.on_smoothed_body_velocity(invalid)
        adapter.now_ns-=1;adapter.on_smoothed_body_velocity(twist(.5))
        adapter.now_ns+=2;adapter.state['state_fresh']=False
        adapter.on_smoothed_body_velocity(twist(.5))
        self.assertEqual(len(adapter.outputs),1)
        self.assertEqual(adapter.velocity_adapter_counts['rejected_outputs'],4)
        self.assertEqual(adapter.velocity_adapter_counts['suppressed_smoother_outputs'],0)

    def test_epoch_change_clears_stop_and_blocks_buffered_smoother_output(self):
        adapter=adapter_fixture();adapter.on_body_velocity(self.target(adapter))
        state=dict(robot_id=adapter.robot,connected=True,state_fresh=True,session='session-a',epoch=1)
        adapter.observe(SimpleNamespace(data=json.dumps(state)))
        self.assertIsNone(adapter.body_stop_ns);self.assertIsNone(adapter.body_input_ns)
        adapter.on_body_velocity(self.target(adapter,1.));adapter.on_smoothed_body_velocity(twist(.5))
        self.assertEqual(len(adapter.outputs),1)
        adapter.identity=['session-a',1]
        adapter.on_smoothed_body_velocity(twist(.5));self.assertEqual(len(adapter.outputs),1)
        adapter.now_ns+=50_000_000;adapter.on_body_velocity(self.target(adapter,1.))
        adapter.on_smoothed_body_velocity(twist(.1));self.assertEqual(adapter.outputs[-1].twist.linear.x,.1)

    def test_disconnect_reconnect_requires_new_valid_input_and_clears_stop(self):
        adapter=adapter_fixture();adapter.on_body_velocity(self.target(adapter))
        state=dict(robot_id=adapter.robot,connected=False,state_fresh=False,session='session-a',epoch=0)
        adapter.observe(SimpleNamespace(data=json.dumps(state)))
        self.assertIsNone(adapter.body_stop_ns);self.assertIsNone(adapter.body_input_ns)
        adapter.on_smoothed_body_velocity(twist(.5))
        state.update(connected=True,state_fresh=True);adapter.observe(SimpleNamespace(data=json.dumps(state)))
        adapter.on_smoothed_body_velocity(twist(.5));self.assertEqual(len(adapter.outputs),1)
        adapter.now_ns+=50_000_000;adapter.on_body_velocity(self.target(adapter,1.))
        adapter.on_smoothed_body_velocity(twist(.1));self.assertEqual(adapter.outputs[-1].twist.linear.x,.1)

    def test_zero_latch_creates_no_timer_republish_and_legacy_mode_stays_inactive(self):
        adapter=adapter_fixture();adapter.on_body_velocity(self.target(adapter))
        adapter.now_ns+=5_000_000_000;self.assertEqual(len(adapter.outputs),1)
        adapter.reset_velocity_adapter();self.assertIsNone(adapter.body_stop_ns)
        adapter.on_smoothed_body_velocity(twist(.5));self.assertEqual(len(adapter.outputs),1)
        adapter.drive_identity=None;adapter.on_body_velocity(self.target(adapter))
        self.assertIsNone(adapter.body_stop_ns);self.assertEqual(len(adapter.outputs),1)


if __name__=='__main__':unittest.main()
