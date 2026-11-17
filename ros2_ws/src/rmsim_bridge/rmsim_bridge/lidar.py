"""Publish measured rays without allocating a Python message object per return."""
import array,json,struct
from rclpy.qos import QoSProfile,ReliabilityPolicy,DurabilityPolicy,qos_profile_sensor_data
from rclpy.serialization import serialize_message
from geometry_msgs.msg import TransformStamped
from sensor_msgs.msg import PointCloud2,PointField
from tf2_msgs.msg import TFMessage
from .protocol import ProtocolError,mount_pose
from .imu import set_stamp


def livox_cdr(empty_message,points):
    """Original ROS serializer supplies header/alignment; append verified packed CustomPoint sequence."""
    prefix=serialize_message(empty_message)
    if prefix[:4]!=b'\x00\x01\x00\x00' or len(points)%20:raise ProtocolError('unsupported Livox CDR encoding')
    # Each original CustomPoint is 19 bytes followed by alignment before the next point.
    count=len(points)//20
    return prefix[:-4]+struct.pack('<I',count)+(points[:-1] if count else b'')


class LidarPublisher:
    def __init__(self,node):
        self.node=node;self.identity=None;self.sequence=0;self.count=0;self.points=0;self.fingerprint=None
        self.raw_pub=None;self.cloud_pub=None
        self.static_pub=node.create_publisher(TFMessage,'/tf_static',QoSProfile(depth=1,reliability=ReliabilityPolicy.RELIABLE,durability=DurabilityPolicy.TRANSIENT_LOCAL))

    def observe(self,state,payload):
        data=state.get('lidar')
        if data is None:return
        from .control_dlls import load_package_dlls
        load_package_dlls('livox_ros_driver2')
        from livox_ros_driver2.msg import CustomMsg
        key=(state['session'],state['epoch'])
        local=mount_pose(data,self.node.robot_id)
        config={k:data[k] for k in ('id','profile_md5','model','frame_id','parent_frame_id','rays_per_second','frame_hz','min_range_m','max_range_m','elevation_min_rad','elevation_max_rad')}
        config['fixed_mount']=local
        fingerprint=json.dumps(config,sort_keys=True)
        if key==self.identity and fingerprint!=self.fingerprint:raise ProtocolError('LiDAR configuration changed within a session')
        if key!=self.identity:
            self.identity=key;self.sequence=0;self.fingerprint=fingerprint
            if self.raw_pub:self.node.destroy_publisher(self.raw_pub);self.node.destroy_publisher(self.cloud_pub)
            prefix='/'+self.node.robot_id+'/'+data['id']
            self.raw_pub=self.node.create_publisher(CustomMsg,prefix+'/livox',qos_profile_sensor_data)
            self.cloud_pub=self.node.create_publisher(PointCloud2,prefix+'/points',qos_profile_sensor_data)
            tf=TransformStamped();set_stamp(tf.header.stamp,state['sim_time_ns']);tf.header.frame_id=data['parent_frame_id'];tf.child_frame_id=data['frame_id']
            t=tf.transform;t.translation.x,t.translation.y,t.translation.z=map(float,local['position_m'])
            t.rotation.x,t.rotation.y,t.rotation.z,t.rotation.w=map(float,local['rotation_xyzw']);self.static_pub.publish(TFMessage(transforms=[tf]))
        for scan in data['scans']:
            if scan['seq']<=self.sequence:raise ProtocolError('LiDAR sequence regressed')
            self.sequence=scan['seq'];n=scan['point_count'];offset=scan['byte_offset'];points=payload[offset:offset+scan['byte_length']]
            raw=CustomMsg();set_stamp(raw.header.stamp,scan['start_ns']);raw.header.frame_id=data['frame_id'];raw.timebase=scan['start_ns'];raw.point_num=n
            self.raw_pub.publish(livox_cdr(raw,points))
            cloud=PointCloud2();cloud.header=raw.header;cloud.height=1;cloud.width=n;cloud.is_bigendian=False;cloud.is_dense=True;cloud.point_step=20;cloud.row_step=n*20
            cloud.fields=[PointField(name=name,offset=pos,datatype=kind,count=1) for name,pos,kind in [('offset_time',0,PointField.UINT32),('x',4,PointField.FLOAT32),('y',8,PointField.FLOAT32),('z',12,PointField.FLOAT32),('reflectivity',16,PointField.UINT8),('tag',17,PointField.UINT8),('line',18,PointField.UINT8)]]
            cloud.data=array.array('B',points);self.cloud_pub.publish(cloud);self.count+=1;self.points+=n
