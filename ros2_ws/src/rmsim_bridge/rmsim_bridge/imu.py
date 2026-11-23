"""Validate and publish physical six-axis samples; no orientation or localization synthesis."""
import json
from rclpy.qos import QoSProfile,ReliabilityPolicy,DurabilityPolicy,qos_profile_sensor_data
from sensor_msgs.msg import Imu
from geometry_msgs.msg import TransformStamped
from tf2_msgs.msg import TFMessage
from .protocol import ProtocolError,validate_imu,mount_pose



def set_stamp(field,value):field.sec=value//1_000_000_000;field.nanosec=value%1_000_000_000


class ImuPublisher:
    def __init__(self,node):
        self.node=node;self.identity=None;self.profiles={};self.fingerprint=None;self.publishers={};self.previous={};self.count=0
        self.static_pub=node.create_publisher(TFMessage,'/tf_static',QoSProfile(depth=1,reliability=ReliabilityPolicy.RELIABLE,durability=DurabilityPolicy.TRANSIENT_LOCAL))

    def observe(self,state):
        data=state.get('imu')
        if data is None:return
        validate_imu(data,self.node.robot_id,state['sim_time_ns'])
        key=(state['session'],state['epoch'])
        fingerprint=json.dumps(data['profiles'],sort_keys=True,separators=(',',':'))
        if key==self.identity and fingerprint!=self.fingerprint:raise ProtocolError('IMU configuration changed within a session')
        if key!=self.identity:
            self.identity=key;self.fingerprint=fingerprint;self.previous={};self.profiles={p['id']:p for p in data['profiles']}
            for name in list(self.publishers):
                if name not in self.profiles:self.node.destroy_publisher(self.publishers.pop(name))
            transforms=[]
            for name,p in self.profiles.items():
                if name not in self.publishers:
                    self.publishers[name]=self.node.create_publisher(Imu,'/'+self.node.robot_id+'/'+name+'/data_raw',QoSProfile(depth=128,reliability=ReliabilityPolicy.RELIABLE))
                tf=TransformStamped();set_stamp(tf.header.stamp,state['sim_time_ns']);tf.header.frame_id=p['parent_frame_id'];tf.child_frame_id=p['frame_id']
                local=mount_pose(p,self.node.robot_id)
                t=tf.transform;t.translation.x,t.translation.y,t.translation.z=map(float,local['position_m'])
                t.rotation.x,t.rotation.y,t.rotation.z,t.rotation.w=map(float,local['rotation_xyzw']);transforms.append(tf)
            self.static_pub.publish(TFMessage(transforms=transforms))
        for s in data['samples']:
            old=self.previous.get(s['id']);seq=s['seq'];stamp=s['stamp_ns']
            if old and (seq<=old[0] or stamp<=old[1]):raise ProtocolError('IMU sequence/time regressed')
            self.previous[s['id']]=(seq,stamp);p=self.profiles[s['id']]
            m=Imu();set_stamp(m.header.stamp,stamp);m.header.frame_id=p['frame_id'];m.orientation.w=1.
            m.orientation_covariance=[-1.,0.,0.,0.,0.,0.,0.,0.,0.]
            m.angular_velocity.x,m.angular_velocity.y,m.angular_velocity.z=map(float,s['angular_velocity_rad_s'])
            m.linear_acceleration.x,m.linear_acceleration.y,m.linear_acceleration.z=map(float,s['specific_force_mps2'])
            ac=[0.]*9;gc=[0.]*9
            for i in range(3):ac[i*4]=float(p['accel_noise_std_mps2'][i]**2);gc[i*4]=float(p['gyro_noise_std_rad_s'][i]**2)
            m.linear_acceleration_covariance=ac;m.angular_velocity_covariance=gc;self.publishers[s['id']].publish(m);self.count+=1
