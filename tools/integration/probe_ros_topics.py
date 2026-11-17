"""Independent native ROS node + installed bridge process against a real UE server."""
import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import shutil
import sys
import time

os.environ['ROS_DOMAIN_ID']='173'
os.environ['ROS_LOCALHOST_ONLY']='1'
import rclpy
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import qos_profile_sensor_data,QoSProfile
from geometry_msgs.msg import TwistStamped,PoseStamped
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import Image,CameraInfo
from std_msgs.msg import String
from tf2_msgs.msg import TFMessage
import rmsim_bridge
from probe_native_bridge import rotate,multiply


def ns(stamp):return stamp.sec*1_000_000_000+stamp.nanosec


class Observer(Node):
    def __init__(self):
        super().__init__('rmsim_native_bridge_probe',parameter_overrides=[Parameter('use_sim_time',value=True)])
        self.clock=0;self.clocks=[];self.truth=None;self.diag=None;self.images={};self.infos={};self.transforms={};self.truths={};self.tf_edges=set()
        self.publish=self.create_publisher(TwistStamped,'/aim_sentry/cmd_vel',QoSProfile(depth=1))
        self.create_subscription(Clock,'/clock',self.on_clock,QoSProfile(depth=1))
        self.create_subscription(Image,'/aim_sentry/camera/image_raw',lambda m:self.store(self.images,ns(m.header.stamp),m),qos_profile_sensor_data)
        self.create_subscription(CameraInfo,'/aim_sentry/camera/camera_info',lambda m:self.store(self.infos,ns(m.header.stamp),m),qos_profile_sensor_data)
        self.create_subscription(PoseStamped,'/rmsim/evaluation/aim_sentry/ground_truth',self.on_truth,qos_profile_sensor_data)
        self.create_subscription(String,'/rmsim/aim_sentry/state',lambda m:setattr(self,'diag',json.loads(m.data)),QoSProfile(depth=1))
        self.create_subscription(TFMessage,'/tf',self.on_tf,QoSProfile(depth=100))

    def store(self,where,key,value):
        where[key]=value
        while len(where)>150:del where[next(iter(where))]

    def on_clock(self,m):self.clock=ns(m.clock);self.clocks.append(self.clock)
    def on_truth(self,m):self.truth=m;self.store(self.truths,ns(m.header.stamp),m)
    def on_tf(self,m):
        for t in m.transforms:
            self.tf_edges.add((t.header.frame_id,t.child_frame_id))
            if t.child_frame_id=='aim_sentry/aim_camera_optical_frame':self.store(self.transforms,ns(t.header.stamp),t)
    def position(self):
        p=self.truth.pose.position;return [p.x,p.y,p.z]
    def command(self,vx=0,vy=0,wz=0,z=0,stamp_offset=0):
        msg=TwistStamped();msg.header.frame_id='aim_sentry/base_link';value=max(0,self.clock+stamp_offset)
        msg.header.stamp.sec=value//1_000_000_000;msg.header.stamp.nanosec=value%1_000_000_000
        msg.twist.linear.x=float(vx);msg.twist.linear.y=float(vy);msg.twist.linear.z=float(z);msg.twist.angular.z=float(wz)
        self.publish.publish(msg)
    def pump(self,seconds,velocity=None):
        deadline=time.monotonic()+seconds;next_command=0
        while time.monotonic()<deadline:
            rclpy.spin_once(self,timeout_sec=.01)
            if velocity is not None and self.clock>0 and time.monotonic()>=next_command:
                self.command(*velocity);next_command=time.monotonic()+.04
    def wait(self,predicate,timeout):
        deadline=time.monotonic()+timeout
        while not predicate():
            if time.monotonic()>deadline:raise TimeoutError('ROS observation timed out')
            rclpy.spin_once(self,timeout_sec=.02)


def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=19452);p.add_argument('--output',type=Path,required=True);p.add_argument('--wait-reset',action='store_true')
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    report=dict(status='running',scope='actual native ROS2 processes and UE; evaluation truth only for test measurement',python=sys.executable,bridge_module=rmsim_bridge.__file__,checks=[])
    assert os.name=='nt','this gate records native Windows, not WSL'
    assert 'install_bridge' in str(Path(rmsim_bridge.__file__).resolve()),'probe must use the installed ROS package'
    rclpy.init();node=Observer();bridge=None;logs=[]
    ros2=shutil.which('ros2')
    if not ros2:raise RuntimeError('ros2 executable is not available in the native environment')
    def terminate_tree(process):
        if process and process.poll() is None:
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
            process.wait(timeout=5)
    def start_bridge(index):
        stream=(args.output/f'bridge-{index}.log').open('w',encoding='utf-8');logs.append(stream)
        return subprocess.Popen([ros2,'run','rmsim_bridge','bridge','--ros-args','-p',f'port:={args.port}'],stdout=stream,stderr=subprocess.STDOUT,env=os.environ.copy())
    def save():
        (args.output/'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    def record(name,passed,**details):
        report['checks'].append(dict(name=name,passed=bool(passed),**details));save()
        if not passed:raise AssertionError(name)
    try:
        bridge=start_bridge(1)
        node.wait(lambda:node.diag and node.diag['connected'] and node.truth is not None and len(node.images)>=4,90)
        matching=set(node.images)&set(node.infos)&set(node.transforms)&set(node.truths)
        node.wait(lambda:len(set(node.images)&set(node.infos)&set(node.transforms)&set(node.truths))>=3,10)
        matching=set(node.images)&set(node.infos)&set(node.transforms)&set(node.truths);key=max(matching)
        img=node.images[key];info=node.infos[key];tf=node.transforms[key];data=bytes(img.data)
        expected_f=img.width/(2*math.tan(math.pi/6))
        record('actual_ros_image_info_tf',img.encoding=='rgb8' and len(data)==img.width*img.height*3 and img.step==img.width*3 and
               info.width==img.width and info.height==img.height and abs(info.k[0]-expected_f)<.01 and max(data)-min(data)>80 and
               tf.header.frame_id=='aim_sentry/base_link' and img.header.frame_id==tf.child_frame_id,
               matching_stamps=len(matching),stamp_ns=key,width=img.width,height=img.height,focal_pixels=info.k[0])
        config=node.diag.get('camera_config',{})
        record('fixed_sensor_optics_runtime',abs(config.get('hfov_rad',0)-math.pi/3)<1.e-5 and abs(config.get('observer_hfov_rad',0)-math.pi/6)<1.e-5,
               camera_config=config)
        (args.output/'ros-camera.rgb').write_bytes(data)
        record('physics_clock_drives_ros_time',len(node.clocks)>5 and node.clocks[-1]>node.clocks[0] and abs(node.get_clock().now().nanoseconds-node.clock)<100_000_000,
               clock_samples=len(node.clocks),ros_time_ns=node.get_clock().now().nanoseconds)
        record('truth_not_localization_tf',node.tf_edges=={('aim_sentry/base_link','aim_sentry/aim_camera_optical_frame')},tf_edges=sorted(node.tf_edges))
        start=node.position();node.pump(3,(.4,0,0));end=node.position()
        record('ros_twist_moves_native_vehicle',end[0]-start[0]>.6 and abs(end[1]-start[1])<.2 and node.diag['forwarded_commands']>10,
               displacement=[end[i]-start[i] for i in range(3)],ack=node.diag['command'])
        node.pump(1);before=node.position();node.pump(.5)
        record('ros_publisher_stop_brakes',node.diag['command']['status']=='expired' and math.dist(before,node.position())<.02,
               drift_m=math.dist(before,node.position()),ack=node.diag['command'])
        start=node.position();node.pump(2,(0,.3,0));end=node.position();node.pump(1)
        record('ros_left_axis',end[1]-start[1]>.3 and abs(end[0]-start[0])<.2,displacement=[end[i]-start[i] for i in range(3)])
        rejected=node.diag['rejected_commands'];node.command(z=.1)
        node.wait(lambda:node.diag['rejected_commands']>rejected,3)
        record('unsupported_component_rejected', 'planar' in node.diag['command_error'],error=node.diag['command_error'])
        rejected=node.diag['rejected_commands'];node.command(stamp_offset=-1_000_000_000)
        node.wait(lambda:node.diag['rejected_commands']>rejected,3)
        record('stale_ros_command_rejected','stale' in node.diag['command_error'],error=node.diag['command_error'])
        before_demo=node.position();stream=(args.output/'drive-demo.log').open('w',encoding='utf-8');logs.append(stream)
        demo=subprocess.Popen([ros2,'run','rmsim_bridge','drive_demo','--ros-args','-p','vx:=0.2','-p','duration_s:=2.0'],stdout=stream,stderr=subprocess.STDOUT,env=os.environ.copy())
        try:
            deadline=time.monotonic()+15
            while demo.poll() is None and time.monotonic()<deadline:rclpy.spin_once(node,timeout_sec=.01)
            record('installed_ros2_run_demo',demo.poll()==0 and node.position()[0]-before_demo[0]>.2,displacement_m=node.position()[0]-before_demo[0])
        finally:terminate_tree(demo)
        node.pump(.5)
        node.pump(.6,(.3,0,0));old_session=node.diag['session'];terminate_tree(bridge);node.pump(.8)
        bridge=start_bridge(2)
        node.wait(lambda:node.diag and node.diag.get('session')!=old_session and node.diag['connected'],10)
        node.pump(.4);before=node.position();node.pump(.5)
        record('sidecar_restart_no_replay',math.dist(before,node.position())<.02 and node.diag['command']['accepted_seq']==-1,
               drift_m=math.dist(before,node.position()),new_session=node.diag['session'])
        if args.wait_reset:
            old_epoch=node.diag['epoch'];old_time=node.clock
            (args.output/'ready-for-reset.json').write_text(json.dumps(dict(epoch=old_epoch,sim_time_ns=old_time)),encoding='utf-8')
            node.wait(lambda:node.diag and node.diag.get('epoch',old_epoch)>old_epoch and node.diag['connected'],300)
            node.pump(.3)
            record('operator_reset_new_epoch',node.diag['epoch']>old_epoch and node.clock<old_time and node.diag['command']['accepted_seq']==-1,
                   before_epoch=old_epoch,after_epoch=node.diag['epoch'],before_time_ns=old_time,after_time_ns=node.clock)
            node.images.clear();node.infos.clear();node.transforms.clear();node.truths.clear()
            node.wait(lambda:len(set(node.images)&set(node.infos)&set(node.transforms)&set(node.truths))>=2,10)
            key=max(set(node.images)&set(node.infos)&set(node.transforms)&set(node.truths));img=node.images[key];info=node.infos[key];tf=node.transforms[key];truth=node.truths[key]
            b=truth.pose;q=[b.orientation.x,b.orientation.y,b.orientation.z,b.orientation.w];t=tf.transform
            local=[t.translation.x,t.translation.y,t.translation.z];offset=rotate(q,local)
            origin=[b.position.x+offset[0],b.position.y+offset[1],b.position.z+offset[2]]
            optical=multiply(q,[t.rotation.x,t.rotation.y,t.rotation.z,t.rotation.w])
            # This off-axis armor is inside the sensor's60deg view but outside
            # the observer's30deg sight view, so zoom leakage cannot pass by chance.
            point=rotate([-optical[0],-optical[1],-optical[2],optical[3]],[4.-origin[0],-1.7-origin[1],.64-origin[2]])
            u=round(info.k[0]*point[0]/point[2]+info.k[2]);v=round(info.k[4]*point[1]/point[2]+info.k[5]);pixels=bytes(img.data);red=0
            for yy in range(v-3,v+4):
                for xx in range(u-3,u+4):
                    if 0<=xx<img.width and 0<=yy<img.height:
                        i=(yy*img.width+xx)*3;r,g,b=pixels[i:i+3];red+=r>100 and r>1.4*g and r>1.4*b
            record('fixed_optics_after_reset',red>=25 and abs(info.k[0]-img.width/(2*math.tan(math.pi/6)))<.01,red_pixels=red,projected_pixel=[u,v])
        report.update(status='passed',image_messages=node.diag['published_images'],states=node.diag['received_states'],rmw=rclpy.get_rmw_implementation_identifier())
    except Exception as error:
        report.update(status='failed',error=repr(error));raise
    finally:
        terminate_tree(bridge)
        node.destroy_node();rclpy.shutdown()
        for stream in logs:stream.close()
        save()
    print('Native ROS2 bridge PASS',len(report['checks']),'checks')


if __name__=='__main__':main()
