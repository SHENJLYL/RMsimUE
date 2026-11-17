"""Native original Point-LIO gate on a labelled analytic sensor replay, not UE proof."""
import argparse,json,math,os,subprocess,time
from pathlib import Path
os.environ['ROS_DOMAIN_ID']='174'
os.environ['ROS_LOCALHOST_ONLY']='1'
root=Path(__file__).resolve().parents[2]
_dll=os.add_dll_directory(str(root/'ros2_ws/install_lio/livox_ros_driver2/bin')) if os.name=='nt' else None
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Imu,PointCloud2
from nav_msgs.msg import Odometry
from rosgraph_msgs.msg import Clock
from livox_ros_driver2.msg import CustomMsg,CustomPoint

def stamp(field,t):
    ns=round(t*1e9);field.sec=ns//1_000_000_000;field.nanosec=ns%1_000_000_000

def motion(t):
    # Piecewise physically consistent position/acceleration; static until t=4.
    if t<4:return 0.,0.
    if t<5:return .1*(t-4)**2,.2
    if t<8:return .1+.2*(t-5),0.
    if t<9:return .7+.2*(t-8)-.1*(t-8)**2,-.2
    return .8,0.

class Probe(Node):
    def __init__(self):
        super().__init__('original_point_lio_native_probe')
        self.clouds=[];self.odom=[]
        self.lidar=self.create_publisher(CustomMsg,'/probe/lidar',qos_profile_sensor_data)
        self.imu=self.create_publisher(Imu,'/probe/imu',qos_profile_sensor_data)
        self.clock=self.create_publisher(Clock,'/clock',10)
        self.create_subscription(Odometry,'/aft_mapped_to_init',self.observe,20)
        self.create_subscription(PointCloud2,'/cloud_registered',lambda m:self.clouds.append(dict(stamp=m.header.stamp.sec+m.header.stamp.nanosec*1e-9,points=m.width*m.height,frame=m.header.frame_id)),20)

    def observe(self,m):
        p=m.pose.pose.position;q=m.pose.pose.orientation
        self.odom.append(dict(t=m.header.stamp.sec+m.header.stamp.nanosec*1e-9,p=[p.x,p.y,p.z],q=[q.x,q.y,q.z,q.w],frame=m.header.frame_id))

    def imu_at(self,t):
        m=Imu();stamp(m.header.stamp,t);m.header.frame_id='probe/imu_link';m.orientation.w=1.;m.orientation_covariance[0]=-1.
        m.linear_acceleration.x=motion(t)[1];m.linear_acceleration.z=9.8
        self.imu.publish(m);clock=Clock();stamp(clock.clock,t);self.clock.publish(clock)

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    config=args.output/'parameters.yaml'
    config.write_text('/**:\n  ros__parameters:\n    use_sim_time: true\n    common.lid_topic: /probe/lidar\n    common.imu_topic: /probe/imu\n    mapping.acc_norm: 9.81\n    mapping.gravity: [0.0, 0.0, -9.8]\n    mapping.gravity_init: [0.0, 0.0, -9.8]\n    mapping.extrinsic_T: [0.0, 0.0, 0.0]\n    filter_size_surf: 0.15\n    filter_size_map: 0.15\n    init_map_size: 10\n    publish.tf_send_en: false\n',encoding='utf-8')
    executable=root/'ros2_ws/install_lio/point_lio/lib/point_lio/pointlio_mapping.exe'
    base=root/'ros2_ws/install_lio/point_lio/share/point_lio/config/mid360.yaml'
    report=dict(status='running',scope='Analytic synthetic ROS replay against original native estimator; not UE LiDAR or localization acceptance',checks=[],odometry=[])
    process=None;node=None;rclpy.init()
    def save():
        report['odometry']=node.odom if node else []
        (args.output/'verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    def check(name,passed,**details):
        report['checks'].append(dict(name=name,passed=bool(passed),**details));save()
        if not passed:raise RuntimeError(name)
    try:
        node=Probe()
        with (args.output/'pointlio.log').open('w',encoding='utf-8') as log:
            process=subprocess.Popen([str(executable),'--ros-args','--params-file',str(base),'--params-file',str(config)],stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
            deadline=time.monotonic()+30
            while time.monotonic()<deadline and (node.lidar.get_subscription_count()<1 or node.imu.get_subscription_count()<1):
                rclpy.spin_once(node,timeout_sec=.05)
                if process.poll() is not None:raise RuntimeError('Original node exited: '+str(process.returncode))
            check('original_native_subscribers',node.lidar.get_subscription_count()>0 and node.imu.get_subscription_count()>0)
            # Six fixed planes at x/y +/-3, z=-1/+3. Each return has its own acquisition time.
            world=[]
            for face in range(6):
                for i in range(21):
                    for j in range(21):
                        a=-2.5+i*.25;b=-.8+j*.18
                        world.append(([3. if face==0 else -3.,a,b] if face<2 else [a,3. if face==2 else -3.,b] if face<4 else [a,-2.5+j*.25,-1. if face==4 else 3.]))
            # Interleave faces to avoid spatially biased point decimation.
            world=[world[face*441+i] for i in range(441) for face in range(6)]
            tick=200;frames=0;start=time.monotonic()
            for frame in range(100):
                frame_start=1.+frame*.1
                for k in range(20):
                    t=frame_start+k*.005
                    node.imu_at(t);rclpy.spin_once(node,timeout_sec=0)
                    # At least real-time delivery so the receiver can consume its sensor QoS queue.
                    time.sleep(.005)
                cloud=CustomMsg();stamp(cloud.header.stamp,frame_start);cloud.header.frame_id='probe/lidar_link';cloud.timebase=round(frame_start*1e9)
                points=[]
                for i,position in enumerate(world):
                    dt=i/len(world)*.099
                    point=CustomPoint();point.offset_time=round(dt*1e9);point.x=float(position[0]-motion(frame_start+dt)[0]);point.y=float(position[1]);point.z=float(position[2]);point.reflectivity=100;point.tag=0;point.line=i%4;points.append(point)
                cloud.points=points;cloud.point_num=len(points);node.lidar.publish(cloud);frames+=1
                rclpy.spin_once(node,timeout_sec=.005)
                if process.poll() is not None:raise RuntimeError('Estimator exited during replay: '+str(process.returncode))
            for i in range(50):node.imu_at(11.+i*.005);rclpy.spin_once(node,timeout_sec=.01)
            report['frames_sent']=frames;report['points_per_frame']=len(world);report['wall_seconds']=time.monotonic()-start
            check('original_estimator_outputs',len(node.odom)>20 and len(node.clouds)>20,odometry_count=len(node.odom),cloud_count=len(node.clouds))
            check('finite_estimates',all(math.isfinite(v) for m in node.odom for v in [*m['p'],*m['q']]))
            stationary=[m for m in node.odom if 2.5<m['t']<3.8]
            check('stationary_estimate',bool(stationary) and max(math.dist(m['p'],[0,0,0]) for m in stationary)<.05)
            last=node.odom[-1];error=math.dist(last['p'],[.8,0,0])
            check('tracks_analytic_translation',error<.15,last=last,expected=[.8,0,0],error_m=error)
            report['status']='passed'
    except Exception as e:
        report['status']='failed';report['error']=repr(e);raise
    finally:
        if process and process.poll() is None:process.terminate();process.wait(timeout=15)
        report['process_exit']=process.returncode if process else None;save()
        if node:node.destroy_node()
        rclpy.shutdown()
    print('Original native Point-LIO analytic replay PASS',len(report['checks']),'checks')

if __name__=='__main__':main()
