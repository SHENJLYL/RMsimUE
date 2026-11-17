"""Restart native navigation interfaces on synthetic, timestamped sensor data."""
import argparse, hashlib, json, os, struct, subprocess, time, sys
from pathlib import Path


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--runtime',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--cycles',type=int,default=5)
    parser.add_argument('--transport',choices=('default','loopback_udp'),default='default');args=parser.parse_args()
    if not 2<=args.cycles<=20:parser.error('cycles must be 2..20')
    args.output.mkdir(parents=True,exist_ok=False)
    os.environ['ROS_DOMAIN_ID']='181'
    os.environ['ROS_LOCALHOST_ONLY']='1'
    if args.transport=='loopback_udp':
        package=Path(__file__).resolve().parents[2]/'ros2_ws/src/rmsim_bridge'
        sys.path.insert(0,str(package))
        from rmsim_bridge.native_transport import loopback_environment
        environment,_=loopback_environment(os.environ,package/'config/fastdds-loopback-udp.xml')
        os.environ.update(environment)
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy, qos_profile_sensor_data
    from geometry_msgs.msg import TransformStamped
    from nav_msgs.msg import Odometry
    from rosgraph_msgs.msg import Clock
    from sensor_msgs.msg import PointCloud2, PointField
    from tf2_msgs.msg import TFMessage
    prefix='restart_probe_'+str(os.getpid());base=prefix+'/base';imu=prefix+'/imu';odomframe=prefix+'/odom';stable=prefix+'/stabilized'
    config=args.output/'params.yaml'
    config.write_text(json.dumps({'/**':{'ros__parameters':{'use_sim_time':True,
        'state_estimation_topic':prefix+'/source_odom','registered_scan_topic':prefix+'/source_cloud',
        'odom_frame':odomframe,'base_frame':base,'robot_base_frame':base,'lidar_frame':imu,
        'fake_robot_base_frame':prefix+'/navigation','planar_navigation_frames':True,'stabilized_base_frame':stable,
        'odom_topic':prefix+'/odometry','input_cmd_vel_topic':prefix+'/nav_cmd','output_cmd_vel_topic':prefix+'/cmd_vel'}}}),encoding='utf-8')
    rclpy.init();node=Node(prefix);received={};counter=0
    clock=node.create_publisher(Clock,'/clock',10)
    static=node.create_publisher(TFMessage,'/tf_static',QoSProfile(depth=1,reliability=ReliabilityPolicy.RELIABLE,durability=DurabilityPolicy.TRANSIENT_LOCAL))
    def tf_callback(message):
        for t in message.transforms:
            if t.child_frame_id==stable:received['tf']=t.header.stamp.sec*1_000_000_000+t.header.stamp.nanosec
    node.create_subscription(TFMessage,'/tf',tf_callback,100)
    node.create_subscription(Odometry,prefix+'/odometry',lambda m:received.update(odom=m.header.stamp.sec*1_000_000_000+m.header.stamp.nanosec),qos_profile_sensor_data)
    node.create_subscription(PointCloud2,prefix+'/sensor_scan',lambda m:received.update(scan=m.header.stamp.sec*1_000_000_000+m.header.stamp.nanosec),qos_profile_sensor_data)
    children=[];logs=[];pubs=[];report={'status':'running','transport':args.transport,'checks':[],
        'scope':'native interface restart on synthetic data; independent of physical navigation'}
    def stop():
        for p in children:
            if p.poll() is None:p.terminate()
        for p in children:p.wait(timeout=10)
        children.clear()
        for log in logs:log.close()
        logs.clear()
    try:
        for cycle in range(args.cycles):
            for pub in pubs:node.destroy_publisher(pub)
            pubs=[node.create_publisher(Odometry,prefix+'/source_odom',5),node.create_publisher(PointCloud2,prefix+'/source_cloud',5)]
            for package,exe in [('loam_interface','loam_interface_node'),('sensor_scan_generation','sensor_scan_generation_node'),('fake_vel_transform','fake_vel_transform_node')]:
                command=[str(args.runtime/(exe+'.exe')),'--ros-args','-r','__node:='+prefix+'_'+package,'--params-file',str(config)]
                for topic in ('lidar_odometry','registered_scan','sensor_scan','odometry'):command+=['-r',topic+':='+prefix+'/'+topic]
                log=(args.output/f'{cycle+1:02d}-{package}.log').open('w',encoding='utf-8');logs.append(log)
                children.append(subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0))
            received.clear();marker=counter;deadline=time.monotonic()+10;next_send=0.;ready_at=None
            while time.monotonic()<deadline:
                if any(p.poll() is not None for p in children):raise RuntimeError('A native interface exited')
                if time.monotonic()>=next_send:
                    next_send=time.monotonic()+.1;counter+=100_000_000
                    c=Clock();c.clock.sec=counter//1_000_000_000;c.clock.nanosec=counter%1_000_000_000;clock.publish(c)
                    tf=TransformStamped();tf.header.frame_id=base;tf.child_frame_id=imu;tf.transform.rotation.w=1.;static.publish(TFMessage(transforms=[tf]))
                    m=Odometry();m.header.stamp=c.clock;m.header.frame_id='camera_init';m.child_frame_id=imu;m.pose.pose.orientation.w=1.
                    cloud=PointCloud2();cloud.header=m.header;cloud.height=1;cloud.width=1;cloud.is_dense=True;cloud.point_step=12;cloud.row_step=12
                    cloud.fields=[PointField(name=n,offset=i*4,datatype=PointField.FLOAT32,count=1) for i,n in enumerate(('x','y','z'))];cloud.data=struct.pack('<fff',1.,0.,0.)
                    pubs[0].publish(m);pubs[1].publish(cloud)
                rclpy.spin_once(node,timeout_sec=.01)
                ready=all(received.get(k,0)>marker and counter-received[k]<400_000_000 for k in ('odom','scan','tf'))
                if ready:
                    if ready_at is None:ready_at=time.monotonic()
                    if time.monotonic()-ready_at>.7:break
                else:ready_at=None
            passed=ready_at is not None and time.monotonic()-ready_at>.7
            report['checks'].append({'cycle':cycle+1,'passed':passed,'received_stamps':received.copy(),'sim_time_ns':counter})
            print('NATIVE_RESTART',cycle+1,'PASS' if passed else 'FAIL',flush=True)
            stop()
            if not passed:raise AssertionError('Native interface data stopped after restart')
        report['status']='passed'
    except Exception as e:report.update(status='failed',error=repr(e));raise
    finally:
        stop();node.destroy_node();rclpy.shutdown()
        report['runtime_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in args.runtime.glob('*.dll') if p.name in ('loam_interface.dll','sensor_scan_generation.dll','fake_vel_transform.dll')}
        (args.output/'verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':main()
