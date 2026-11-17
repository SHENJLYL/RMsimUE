"""Native scan adapter: absent mount TF and stopped simulation clock must not block ROS."""
import argparse, hashlib, json, os, struct, subprocess, time
from pathlib import Path
os.environ['ROS_DOMAIN_ID']='180'
os.environ['ROS_LOCALHOST_ONLY']='1'
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy, qos_profile_sensor_data
from rcl_interfaces.srv import GetParameters
from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import PointCloud2, PointField
from tf2_msgs.msg import TFMessage


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--program',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--expect-blocked',action='store_true')
    parser.add_argument('--kind',choices=('scan','loam'),default='scan')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    prefix='rmsim_scan_startup_'+str(os.getpid());name=prefix+'_adapter'
    params={'/**':{'ros__parameters':{'use_sim_time':True,'lidar_frame':prefix+'/imu',
                                   'base_frame':prefix+'/base','robot_base_frame':prefix+'/base'}}}
    if args.kind=='loam':
        params['/**']['ros__parameters'].update(state_estimation_topic=prefix+'/source_odometry',registered_scan_topic=prefix+'/source_cloud',odom_frame=prefix+'/odom')
    config=args.output/'params.yaml';config.write_text(json.dumps(params),encoding='utf-8')
    report={'status':'running','expected_blocked':args.expect_blocked,'kind':args.kind,'checks':[],
            'scope':'native adapter with synthetic synchronized sensors; no physical-navigation claim'}
    rclpy.init();node=Node(prefix);scans=[]
    odom=node.create_publisher(Odometry,prefix+('/source_odometry' if args.kind=='loam' else '/lidar_odometry'),10)
    cloud=node.create_publisher(PointCloud2,prefix+('/source_cloud' if args.kind=='loam' else '/registered_scan'),10)
    clock=node.create_publisher(Clock,'/clock',10)
    static=node.create_publisher(TFMessage,'/tf_static',QoSProfile(depth=1,reliability=ReliabilityPolicy.RELIABLE,durability=DurabilityPolicy.TRANSIENT_LOCAL))
    node.create_subscription(PointCloud2,prefix+('/registered_scan' if args.kind=='loam' else '/sensor_scan'),scans.append,qos_profile_sensor_data)
    client=node.create_client(GetParameters,name+'/get_parameters')
    log=(args.output/'native.log').open('w',encoding='utf-8')
    command=[str(args.program),'--ros-args','-r','__node:='+name,'--params-file',str(config)]
    for topic in ('lidar_odometry','registered_scan','sensor_scan','odometry'):command+=['-r',topic+':='+prefix+'/'+topic]
    child=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    def pump(seconds,send=None):
        end=time.monotonic()+seconds;next_send=0.
        while time.monotonic()<end:
            if child.poll() is not None:raise RuntimeError('Native node exited')
            if send and time.monotonic()>=next_send:send();next_send=time.monotonic()+.1
            rclpy.spin_once(node,timeout_sec=.01)
    stamp=20_000_000_000
    def pair():
        nonlocal stamp
        stamp+=100_000_000
        m=Odometry();m.header.frame_id=prefix+'/odom';m.header.stamp.sec=stamp//1_000_000_000;m.header.stamp.nanosec=stamp%1_000_000_000
        m.child_frame_id=prefix+'/imu';m.pose.pose.orientation.w=1.
        p=PointCloud2();p.header=m.header;p.height=1;p.width=1;p.is_dense=True;p.point_step=12;p.row_step=12
        p.fields=[PointField(name=n,offset=i*4,datatype=PointField.FLOAT32,count=1) for i,n in enumerate(('x','y','z'))]
        p.data=struct.pack('<fff',1.,0.,0.);odom.publish(m);cloud.publish(p)
    def check(name,passed,**data):
        report['checks'].append(dict(name=name,passed=bool(passed),**data))
        if not passed:raise AssertionError(name)
    try:
        deadline=time.monotonic()+15
        while not client.service_is_ready() and time.monotonic()<deadline:pump(.1)
        request=GetParameters.Request();request.names=['use_sim_time']
        first=client.call_async(request);deadline=time.monotonic()+5
        while not first.done() and time.monotonic()<deadline:pump(.1)
        check('native_node_initially_responsive',first.done() and first.result().values[0].bool_value)
        pump(1.5,pair)
        future=client.call_async(request);pump(2.,pair)
        responsive=future.done()
        check('paused_clock_missing_tf_contract',responsive!=args.expect_blocked,responsive=responsive)
        check('missing_transform_does_not_fabricate_scan',not scans)
        tf=TransformStamped();tf.header.frame_id=prefix+'/base';tf.child_frame_id=prefix+'/imu';tf.transform.rotation.w=1.
        static.publish(TFMessage(transforms=[tf]))
        def restored():
            t=Clock();t.clock.sec=stamp//1_000_000_000;t.clock.nanosec=stamp%1_000_000_000;clock.publish(t);pair()
        pump(3.,restored)
        check('real_mount_transform_allows_output',len(scans)>=3 and all(m.header.frame_id==prefix+('/odom' if args.kind=='loam' else '/imu') for m in scans),scan_count=len(scans))
        report['status']='passed'
    except Exception as error:report.update(status='failed',error=repr(error));raise
    finally:
        if child.poll() is None:child.terminate();child.wait(timeout=10)
        log.close();node.destroy_node();rclpy.shutdown()
        report['module_sha256']=hashlib.sha256((args.program.parent/('loam_interface.dll' if args.kind=='loam' else 'sensor_scan_generation.dll')).read_bytes()).hexdigest()
        (args.output/'verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('Native scan startup PASS',len(report['checks']),'checks')


if __name__=='__main__':main()
