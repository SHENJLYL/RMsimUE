"""Native adapter contract: tilted mounting must not apply gravity alignment twice."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'ros2_ws/src/rmsim_bridge'))
from rmsim_bridge.native_transport import loopback_environment
from probe_native_bridge import multiply,rotate
os.environ.update(loopback_environment(os.environ,ROOT/'ros2_ws/src/rmsim_bridge/config/fastdds-loopback-udp.xml')[0])
os.environ['ROS_DOMAIN_ID']='182'
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile,DurabilityPolicy,ReliabilityPolicy
from nav_msgs.msg import Odometry
from geometry_msgs.msg import TransformStamped
from tf2_msgs.msg import TFMessage


def q_rpy(r,p,y):
    sr,cr,sp,cp,sy,cy=math.sin(r/2),math.cos(r/2),math.sin(p/2),math.cos(p/2),math.sin(y/2),math.cos(y/2)
    return [sr*cp*cy-cr*sp*sy,cr*sp*cy+sr*cp*sy,cr*cp*sy-sr*sp*cy,cr*cp*cy+sr*sp*sy]


def compose(a,b):
    pa,qa=a;pb,qb=b;v=rotate(qa,pb)
    return [pa[i]+v[i] for i in range(3)],multiply(qa,qb)


def pose_error(msg,expected):
    p=msg.pose.pose.position;q=msg.pose.pose.orientation;ep,eq=expected
    return math.dist([p.x,p.y,p.z],ep),min(math.dist([q.x,q.y,q.z,q.w],eq),math.dist([-q.x,-q.y,-q.z,-q.w],eq))


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
    args.output.mkdir(parents=True,exist_ok=False);program=ROOT/'artifacts/runtime/navigation/windows/loam_interface_node.exe'
    report={'status':'running','scope':'native C++ adapter on synthetic estimated poses and measured-mount contract; no UE route claim','checks':[]}
    rclpy.init();node=Node('rmsim_tilt_alignment_probe');children=[];logs=[]
    try:
        for case,enabled in [('legacy',False),('gravity_aligned',True)]:
            prefix='alignment_'+case;received=[]
            pub=node.create_publisher(Odometry,prefix+'/source',10)
            sub=node.create_subscription(Odometry,prefix+'/result',received.append,10)
            tf=node.create_publisher(TFMessage,'/tf_static',QoSProfile(depth=1,reliability=ReliabilityPolicy.RELIABLE,durability=DurabilityPolicy.TRANSIENT_LOCAL))
            mount=([.1747,.097,.3222],q_rpy(-.03,math.pi/4,.1))
            msg=TransformStamped();msg.header.frame_id=prefix+'/base';msg.child_frame_id=prefix+'/imu'
            msg.transform.translation.x,msg.transform.translation.y,msg.transform.translation.z=mount[0]
            msg.transform.rotation.x,msg.transform.rotation.y,msg.transform.rotation.z,msg.transform.rotation.w=mount[1]
            params={'/**':{'ros__parameters':{'use_sim_time':False,'base_frame':prefix+'/base','lidar_frame':prefix+'/imu','odom_frame':prefix+'/odom','state_estimation_topic':prefix+'/source','registered_scan_topic':prefix+'/cloud','align_gravity_initialized_pose':enabled}}}
            config=args.output/(case+'.yaml');config.write_text(json.dumps(params),encoding='utf-8')
            log=(args.output/(case+'.log')).open('w',encoding='utf-8');logs.append(log)
            child=subprocess.Popen([str(program),'--ros-args','--params-file',str(config),'-r','lidar_odometry:='+prefix+'/result'],stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW);children.append(child)
            body0=([2.,-1.,.4],q_rpy(.12,-.08,.7));heading=q_rpy(0,0,-.7)
            alignment=([-v for v in rotate(heading,body0[0])],heading) if enabled else mount
            for phase,body in [('initial',body0),('translated',([2.5,-.8,.5],q_rpy(.08,-.1,1.)) )]:
                source=compose(body,mount);expected=compose(alignment,source);deadline=time.monotonic()+12;next_send=0.
                seen=False;last_errors=None
                while time.monotonic()<deadline:
                    if child.poll() is not None:raise RuntimeError('Native alignment node exited')
                    if time.monotonic()>=next_send:
                        next_send=time.monotonic()+.04
                        odom=Odometry();odom.header.frame_id='estimated_gravity_world';odom.child_frame_id=prefix+'/imu';odom.header.stamp=node.get_clock().now().to_msg()
                        odom.pose.pose.position.x,odom.pose.pose.position.y,odom.pose.pose.position.z=source[0]
                        odom.pose.pose.orientation.x,odom.pose.pose.orientation.y,odom.pose.pose.orientation.z,odom.pose.pose.orientation.w=source[1]
                        tf.publish(TFMessage(transforms=[msg]));pub.publish(odom)
                    rclpy.spin_once(node,timeout_sec=.01)
                    if received:
                        last_errors=pose_error(received[-1],expected)
                        if max(last_errors)<1e-6:seen=True;break
                item={'name':case+'_'+phase,'passed':seen,'translation_and_quaternion_errors':last_errors};report['checks'].append(item)
                if not seen:raise AssertionError(item)
                print('PASS',item['name'],flush=True)
            child.terminate();child.wait(timeout=10);node.destroy_subscription(sub);node.destroy_publisher(pub);node.destroy_publisher(tf)
        report['status']='passed'
    except Exception as e:report.update(status='failed',error=repr(e));raise
    finally:
        for child in children:
            if child.poll() is None:child.terminate();child.wait(timeout=10)
        for log in logs:log.close()
        report['module_sha256']=hashlib.sha256((program.parent/'loam_interface.dll').read_bytes()).hexdigest()
        (args.output/'verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        node.destroy_node();rclpy.shutdown()
    print('Native tilted-mount alignment PASS',len(report['checks']),'checks',flush=True)

if __name__=='__main__':main()
