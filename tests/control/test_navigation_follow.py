"""Pinned target-shaper and explicit articulated-mount boundaries."""
import copy
import math
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'ros2_ws/src/rmsim_bridge'))
from rmsim_bridge.navigation_follow import FollowConfig,NavigationYaw,wrap
from rmsim_bridge.protocol import mount_pose,validate_sensor_mounts,aim_packet,hold_aim,ProtocolError


class FollowTests(unittest.TestCase):
    def sample(self, follower, i, **values):
        data=dict(dt=.01,now_ns=1_000_000_000+i*10_000_000,stamp_ns=1_000_000_000+i*10_000_000,
                  vx=0.,vy=.5,body_yaw=0.,joint_yaw=0.,joint_pitch=.12)
        data.update(values)
        return follower.step(**data)
    def test_rate_and_acceleration_match_original_limits(self):
        f=NavigationYaw();previous=0.
        for i in range(400):
            out=self.sample(f,i)
            self.assertLessEqual(abs(out['yaw_rate']),1.+1e-12)
            self.assertLessEqual(abs(out['yaw_rate']-previous),.02000000001)
            previous=out['yaw_rate']
        self.assertLess(abs(wrap(out['yaw']-math.pi/2)),.03)
    def test_first_tick_original_acceleration(self):
        out=self.sample(NavigationYaw(),0)
        self.assertAlmostEqual(out['yaw_rate'],.02);self.assertAlmostEqual(out['yaw'],.0002)
    def test_positive_left_and_reverse(self):
        for vx,vy,target in [(0,.5,math.pi/2),(0,-.5,-math.pi/2),(-.5,0,math.pi)]:
            f=NavigationYaw()
            for i in range(600):out=self.sample(f,i,vx=vx,vy=vy)
            self.assertLess(abs(wrap(out['yaw']-target)),.03)
    def test_wrap_uses_shortest_direction(self):
        f=NavigationYaw();out=self.sample(f,0,vx=math.cos(-3.1),vy=math.sin(-3.1),joint_yaw=3.1)
        self.assertGreater(out['yaw_rate'],0)
    def test_zero_velocity_does_not_turn_to_zero_heading(self):
        f=NavigationYaw()
        for i in range(300):out=self.sample(f,i,vx=0,vy=0,joint_yaw=.8)
        self.assertAlmostEqual(out['yaw'],.8);self.assertEqual(out['yaw_rate'],0)
    def test_pitch_is_preserved(self):
        f=NavigationYaw();self.sample(f,0,joint_pitch=.2)
        self.assertEqual(self.sample(f,1,joint_pitch=-.3)['pitch'],.2)
    def test_body_frame_and_estimated_heading_equivalence(self):
        a=self.sample(NavigationYaw(),0,body_yaw=.7,joint_yaw=.2)
        b=self.sample(NavigationYaw(),0,body_yaw=0,joint_yaw=.2)
        self.assertAlmostEqual(a['yaw'],b['yaw']);self.assertAlmostEqual(wrap(a['target_in_odom']-b['target_in_odom']),.7)
    def test_stale_future_and_bad_dt_reset(self):
        for update in [dict(stamp_ns=700_000_000),dict(stamp_ns=1_100_000_000),dict(dt=0),dict(dt=.21),dict(vx=float('nan'))]:
            f=NavigationYaw();self.assertIsNone(self.sample(f,0,**update));self.assertFalse(f.active)
    def test_backward_timestamp_resets(self):
        f=NavigationYaw();self.sample(f,5);self.assertIsNone(self.sample(f,4));self.assertFalse(f.active)
    def test_invalid_configuration(self):
        for k,v in [('max_yaw_rate',0),('min_speed',-1),('max_yaw_accel',float('nan')),('min_yaw_rate',1),('input_timeout_s',True)]:
            with self.assertRaises(ValueError):FollowConfig(**{k:v})


class MountTests(unittest.TestCase):
    def setUp(self):self.pose={'position_m':[.2,.07,.17],'rotation_xyzw':[0.,0.,0.,1.]}
    def test_v1_retained(self):self.assertEqual(mount_pose({'parent_frame_id':'aim_sentry/base_link','pose_in_base':self.pose},'aim_sentry'),self.pose)
    def test_articulated_fixed_mount(self):self.assertEqual(mount_pose({'mount_schema':2,'parent_frame_id':'aim_sentry/yaw_link','pose_in_parent':self.pose},'aim_sentry'),self.pose)
    def test_parent_and_conflicting_fields_rejected(self):
        valid={'mount_schema':2,'parent_frame_id':'aim_sentry/yaw_link','pose_in_parent':self.pose}
        for change in [{'parent_frame_id':'rmsim_world'},{'parent_frame_id':'other/yaw_link'},{'pose_in_base':self.pose},{'mount_schema':True},{'mount_schema':1}]:
            with self.assertRaises(ProtocolError):mount_pose(dict(valid,**change),'aim_sentry')
    def test_joint_tf_batch_only_relative_and_ordered(self):
        s={'parent_frame_id':'aim_sentry/base_link','frame_id':'aim_sentry/yaw_link','stamp_ns':10,'pose_in_parent':self.pose}
        validate_sensor_mounts([s,dict(s,stamp_ns=11)],'aim_sentry',11)
        for samples in [[s,s],[dict(s,frame_id='aim_sentry/map')],[dict(s,stamp_ns=12)],[],[s]*65]:
            with self.assertRaises(ProtocolError):validate_sensor_mounts(samples,'aim_sentry',11)
    def test_body_aim_explicit_and_no_fire(self):
        import json
        state={'robot_id':'aim_sentry','session':'test','epoch':0,'sim_time_ns':1_000_000_000}
        raw=aim_packet(state,1,state['sim_time_ns'],hold_aim(),'aim_sentry/base_link');d=json.loads(raw)
        self.assertEqual(d['frame_id'],'aim_sentry/base_link');self.assertEqual(d['fire'],0)
        for frame in ['aim_sentry/map','other/base_link','']:
            with self.assertRaises(ProtocolError):aim_packet(state,1,state['sim_time_ns'],hold_aim(),frame)

if __name__=='__main__':unittest.main()
