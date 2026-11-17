"""Native original frame adapter: tilted odometry, planar frames, velocity and stale data."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time

os.environ['ROS_DOMAIN_ID']='179'
os.environ['ROS_LOCALHOST_ONLY']='1'
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from nav_msgs.msg import Odometry
from geometry_msgs.msg import Twist, TwistStamped
from tf2_msgs.msg import TFMessage
from probe_native_bridge import multiply, rotate


def quaternion(r,p,y):
    r,p,y=[math.radians(v)/2 for v in (r,p,y)]
    sr,cr,sp,cp,sy,cy=math.sin(r),math.cos(r),math.sin(p),math.cos(p),math.sin(y),math.cos(y)
    return [sr*cp*cy-cr*sp*sy,cr*sp*cy+sr*cp*sy,cr*cp*sy-sr*sp*cy,cr*cp*cy+sr*sp*sy]


def qvalue(q):return [q.x,q.y,q.z,q.w]
def stamp(value):return value.sec*1_000_000_000+value.nanosec
def axes_error(a,b):return max(math.dist(rotate(a,v),rotate(b,v)) for v in ([1,0,0],[0,1,0],[0,0,1]))


class Probe(Node):
    def __init__(self,prefix):
        super().__init__(prefix)
        self.prefix=prefix;self.frames={};self.command=None
        self.odom=self.create_publisher(Odometry,prefix+'/odom',qos_profile_sensor_data)
        self.velocity=self.create_publisher(Twist,prefix+'/nav_cmd',10)
        self.create_subscription(TwistStamped,prefix+'/body_cmd',lambda m:setattr(self,'command',m),10)
        self.create_subscription(TFMessage,'/tf',self.observe,100)
    def observe(self,message):
        for t in message.transforms:
            if t.header.frame_id==self.prefix+'/base_link':self.frames[t.child_frame_id]=t


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--program',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--planar',action='store_true');args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False);prefix='rmsim_frame_probe_'+str(os.getpid())
    base=prefix+'/base_link';nav=prefix+'/navigation_frame';stable=prefix+'/base_stabilized'
    params={'/**':{'ros__parameters':{'use_sim_time':False,'robot_base_frame':base,'fake_robot_base_frame':nav,
        'planar_navigation_frames':args.planar,'stabilized_base_frame':stable,'odom_topic':prefix+'/odom',
        'input_cmd_vel_topic':prefix+'/nav_cmd','output_cmd_vel_topic':prefix+'/body_cmd','translation_scale':1.0}}}
    config=args.output/'parameters.yaml';config.write_text(json.dumps(params),encoding='utf-8')
    report={'status':'running','planar':args.planar,'checks':[],'scope':'native original C++ adapter on synthetic estimated odometry; no simulator truth input'}
    rclpy.init();node=Probe(prefix);log=(args.output/'adapter.log').open('w',encoding='utf-8')
    options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
    child=subprocess.Popen([str(args.program),'--ros-args','--params-file',str(config)],stdout=log,stderr=subprocess.STDOUT,**options)
    def send_until(q,condition,old=False):
        marker=node.get_clock().now().nanoseconds;deadline=time.monotonic()+6;next_send=0.
        while time.monotonic()<deadline:
            if child.poll() is not None:raise RuntimeError('Original frame node exited: '+str(child.returncode))
            if time.monotonic()>=next_send:
                next_send=time.monotonic()+.05
                m=Odometry();m.header.frame_id=prefix+'/odom';m.child_frame_id=base
                t=node.get_clock().now().nanoseconds-(1_000_000_000 if old else 0)
                m.header.stamp.sec=t//1_000_000_000;m.header.stamp.nanosec=t%1_000_000_000
                m.pose.pose.position.x=1.2;m.pose.pose.position.y=-.7;m.pose.pose.position.z=.4
                m.pose.pose.orientation.x,m.pose.pose.orientation.y,m.pose.pose.orientation.z,m.pose.pose.orientation.w=q
                cmd=Twist();cmd.linear.x=.4;cmd.linear.y=-.2;cmd.angular.z=.3
                node.odom.publish(m);node.velocity.publish(cmd)
            rclpy.spin_once(node,timeout_sec=.01)
            if condition(marker):return
        raise TimeoutError('Native frame/velocity contract did not converge')
    try:
        for angles in [(0,0,90),(20,-25,35),(-15,30,160),(12,18,-170)]:
            q=quaternion(*angles);yaw=quaternion(0,0,angles[2]);counter=quaternion(0,0,-angles[2])
            def correct(marker):
                if nav not in node.frames or node.command is None:return False
                t=node.frames[nav];command=node.command
                if stamp(t.header.stamp)<marker or stamp(command.header.stamp)<marker:return False
                expected=[0,0,0,1] if args.planar else multiply(q,counter)
                if axes_error(multiply(q,qvalue(t.transform.rotation)),expected)>1e-5:return False
                if args.planar:
                    if stable not in node.frames or stamp(node.frames[stable].header.stamp)<marker:return False
                    if axes_error(multiply(q,qvalue(node.frames[stable].transform.rotation)),yaw)>1e-5:return False
                reconstructed=rotate(yaw,[command.twist.linear.x,command.twist.linear.y,0])
                return command.header.frame_id==base and math.dist(reconstructed,[.4,-.2,0])<1e-5 and abs(command.twist.angular.z-.3)<1e-6
            send_until(q,correct)
            for frame in node.frames.values():
                v=frame.transform.translation;assert abs(v.x)+abs(v.y)+abs(v.z)<1e-12,'Virtual origin shifted'
            if not args.planar:assert stable not in node.frames,'Legacy mode changed its TF surface'
            report['checks'].append({'name':'tilt_yaw_velocity_contract','rpy_deg':angles,'passed':True})
        send_until(q,lambda marker:node.command is not None and stamp(node.command.header.stamp)>=marker and
                   node.command.twist.linear.x==0 and node.command.twist.linear.y==0 and node.command.twist.angular.z==0,old=True)
        report['checks'].append({'name':'stale_odometry_stops_command','passed':True})
        report['status']='passed'
    except Exception as error:report.update(status='failed',error=repr(error));raise
    finally:
        if child.poll() is None:child.terminate();child.wait(timeout=10)
        log.close();node.destroy_node();rclpy.shutdown()
        report['program_sha256']=hashlib.sha256(args.program.read_bytes()).hexdigest()
        report['adapter_sha256']=hashlib.sha256((args.program.parent/'fake_vel_transform.dll').read_bytes()).hexdigest()
        (args.output/'verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('Native frame adapter PASS',len(report['checks']),'checks, planar='+str(args.planar),flush=True)


if __name__=='__main__':main()
