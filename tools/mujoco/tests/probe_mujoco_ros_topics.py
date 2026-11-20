"""Native Windows installed ROS 2 sensor smoke against the full MuJoCo scene."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import struct
import subprocess
import sys
import time
os.environ["ROS_DOMAIN_ID"]="173";os.environ["ROS_LOCALHOST_ONLY"]="1"
import rclpy
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import QoSProfile,ReliabilityPolicy,DurabilityPolicy,qos_profile_sensor_data
from sensor_msgs.msg import Image,CameraInfo,Imu,PointCloud2,JointState
from rosgraph_msgs.msg import Clock
from std_msgs.msg import String
from tf2_msgs.msg import TFMessage
from rmsim_bridge.control_dlls import load_package_dlls
import rmsim_bridge.node as installed_node
load_package_dlls("livox_ros_driver2")
from livox_ros_driver2.msg import CustomMsg
from probe_mujoco_sensors import png
ROOT=Path(__file__).resolve().parents[3]
def ns(stamp):return stamp.sec*1_000_000_000+stamp.nanosec
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

class Observer(Node):
    def __init__(self):
        super().__init__("rmsim_mujoco_sensor_observer",parameter_overrides=[Parameter("use_sim_time",value=True)])
        self.clock=0;self.clocks=[];self.diag=None;self.images={};self.infos={};self.cameras={}
        self.clouds={};self.raw={};self.imus={"imu":[],"mid360_imu":[]};self.mounts={};self.edges=set();self.joints={}
        self.create_subscription(Clock,"/clock",self.on_clock,QoSProfile(depth=100))
        self.create_subscription(String,"/rmsim/aim_sentry/state",lambda m:setattr(self,"diag",json.loads(m.data)),20)
        for topic,typ,where in (("camera/image_raw",Image,self.images),("camera/camera_info",CameraInfo,self.infos),
                ("lidar/points",PointCloud2,self.clouds),("lidar/livox",CustomMsg,self.raw),("gimbal/joint_states",JointState,self.joints)):
            self.create_subscription(typ,"/aim_sentry/"+topic,lambda m,w=where:self.store(w,ns(m.header.stamp),m),qos_profile_sensor_data)
        qos=QoSProfile(depth=2000,reliability=ReliabilityPolicy.BEST_EFFORT)
        for name in self.imus:
            self.create_subscription(Imu,"/aim_sentry/"+name+"/data_raw",lambda m,n=name:self.imu(n,m),qos)
        self.create_subscription(TFMessage,"/tf",self.tf,200)
        self.create_subscription(TFMessage,"/tf_static",self.static,
            QoSProfile(depth=20,reliability=ReliabilityPolicy.RELIABLE,durability=DurabilityPolicy.TRANSIENT_LOCAL))
    def on_clock(self,m):self.clock=ns(m.clock);self.clocks.append(self.clock)
    def store(self,where,key,value):
        where[key]=value
        while len(where)>20:del where[next(iter(where))]
    def imu(self,name,m):
        self.imus[name].append({"stamp_ns":ns(m.header.stamp),"frame":m.header.frame_id,
            "a":[m.linear_acceleration.x,m.linear_acceleration.y,m.linear_acceleration.z],
            "w":[m.angular_velocity.x,m.angular_velocity.y,m.angular_velocity.z],
            "orientation_covariance":list(m.orientation_covariance)})
    def tf(self,m):
        for t in m.transforms:
            self.edges.add((t.header.frame_id,t.child_frame_id))
            if t.child_frame_id=="aim_sentry/aim_camera_optical_frame":self.store(self.cameras,ns(t.header.stamp),t)
    def static(self,m):
        for t in m.transforms:self.mounts[t.child_frame_id]=t
    def wait(self,predicate,timeout):
        deadline=time.monotonic()+timeout
        while not predicate():
            if time.monotonic()>deadline:raise TimeoutError("Native ROS sensor observation timed out")
            rclpy.spin_once(self,timeout_sec=.01)
    def pump(self,seconds):
        stop=self.clock+round(seconds*1e9);self.wait(lambda:self.clock>=stop,180)

def main():
    p=argparse.ArgumentParser();p.add_argument("--port",type=int,required=True);p.add_argument("--output",type=Path,required=True)
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    report={"schema":"rmsim_mujoco_ros_sensor_v1","status":"running","checks":[],
        "scope":"Native Windows ROS2 installed bridge, actual MuJoCo clock and real UE sensors; observer has no evaluation-truth subscription",
        "command":sys.argv,"python":sys.executable,"probe_sha256":sha(__file__),
        "installed_node":installed_node.__file__,"installed_node_sha256":sha(installed_node.__file__),
        "source_node_sha256":sha(ROOT/"ros2_ws/src/rmsim_bridge/rmsim_bridge/node.py"),
        "domain_id":os.environ["ROS_DOMAIN_ID"],"localhost_only":os.environ["ROS_LOCALHOST_ONLY"]}
    assert os.name=="nt" and "install_bridge" in str(Path(installed_node.__file__).resolve())
    rclpy.init();node=Observer();bridge=None;stream=None
    def save():(args.output/"verification.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    def check(name,ok,**detail):
        report["checks"].append(dict(name=name,passed=bool(ok),**detail));save()
        print(("PASS " if ok else "FAIL ")+name,flush=True)
        if not ok:raise AssertionError(name)
    try:
        check("installed_bridge_matches_current_source",report["installed_node_sha256"]==report["source_node_sha256"])
        command=[shutil.which("ros2"),"run","rmsim_bridge","bridge","--ros-args","-p",f"port:={args.port}"]
        report["bridge_command"]=command;stream=(args.output/"bridge.log").open("w",encoding="utf-8")
        bridge=subprocess.Popen(command,stdout=stream,stderr=subprocess.STDOUT,env=os.environ.copy(),creationflags=subprocess.CREATE_NO_WINDOW)
        report["bridge_pid"]=bridge.pid;save()
        node.wait(lambda:node.diag and node.diag.get("connected") and len(node.images)>=3 and len(node.raw)>=3 and all(node.imus.values()),120)
        node.pump(2.)
        d=node.diag
        check("ros_clock_identifies_mujoco",d.get("physics_backend")=="mujoco" and
            d.get("clock_source")=="synchronous_MuJoCo_solver" and abs(d.get("dynamics_step_s",0)-.001)<1e-12
            and abs(d.get("fixed_step_s",0)-.005)<1e-12 and d["map"]["id"]=="rmuc2027",
            physics_backend=d.get("physics_backend"),clock_source=d.get("clock_source"),dynamics_step_s=d.get("dynamics_step_s"),map=d["map"])
        check("native_ros_sim_time",len(node.clocks)>20 and all(b>=a for a,b in zip(node.clocks,node.clocks[1:]))
            and abs(node.get_clock().now().nanoseconds-node.clock)<100_000_000,
            samples=len(node.clocks),clock_ns=node.clock,ros_time_ns=node.get_clock().now().nanoseconds)
        for name,values in node.imus.items():
            latest=values[-1]["stamp_ns"];cutoff=latest-1_000_000_000
            rows=[r for r in values if cutoff<=r["stamp_ns"]<=latest]
            intervals=[b["stamp_ns"]-a["stamp_ns"] for a,b in zip(rows,rows[1:])]
            check(name+"_ros_200hz_raw_contract",195<=len(rows)<=202 and abs(latest-node.clock)<100_000_000 and intervals and all(v==5_000_000 for v in intervals)
                and all(r["orientation_covariance"][0]==-1 and r["frame"]=="aim_sentry/"+name+"_link" for r in rows),
                samples_last_sim_second=len(rows),sample_clock_lag_ns=node.clock-latest,interval_min_ns=min(intervals) if intervals else None,
                interval_max_ns=max(intervals) if intervals else None)
        expected_parents={"aim_sentry/imu_link":"aim_sentry/base_link","aim_sentry/mid360_imu_link":"aim_sentry/yaw_link",
            "aim_sentry/lidar_link":"aim_sentry/yaw_link"}
        check("ros_articulated_sensor_tf",all(name in node.mounts and node.mounts[name].header.frame_id==parent for name,parent in expected_parents.items())
            and ("aim_sentry/base_link","aim_sentry/yaw_link") in node.edges,
            static_parents={n:t.header.frame_id for n,t in node.mounts.items()},dynamic_edges=sorted(node.edges))
        check("no_truth_localization_tf",node.edges=={("aim_sentry/base_link","aim_sentry/yaw_link"),
            ("aim_sentry/base_link","aim_sentry/aim_camera_optical_frame")},
            dynamic_edges=sorted(node.edges),observer_subscriptions=node.get_subscriber_names_and_types_by_node(node.get_name(),"/"))
        matching=set(node.raw)&set(node.clouds);check("original_livox_and_cloud_topics",len(matching)>=3,matching_scans=len(matching))
        key=max(matching);raw=node.raw[key];cloud=node.clouds[key];rows=list(struct.iter_unpack("<IfffBBBB",bytes(cloud.data)))
        check("livox_cloud_payload_identity",raw.timebase==key and raw.point_num==len(raw.points)==cloud.width==len(rows)
            and len(rows)>1000 and all((p.offset_time,p.x,p.y,p.z,p.reflectivity,p.tag,p.line)==r[:7] for p,r in zip(raw.points,rows)),
            scan_start_ns=key,point_count=len(rows),frame_id=raw.header.frame_id)
        offsets=sorted({r[0] for r in rows})
        clock_before_wait=node.clock
        # DDS callbacks from different topics need not arrive in publish order.
        # Keep this exact scan fixed and wait for its clock; retain the strict stamp check.
        node.wait(lambda:node.clock>=key+offsets[-1],15)
        check("ros_lidar_actual_time_offsets",19<=len(offsets)<=20 and offsets[-1]-offsets[0]>=90_000_000 and key+offsets[-1]<=node.clock,
            distinct_times=len(offsets),span_ns=offsets[-1]-offsets[0],last_sample_ns=key+offsets[-1],
            clock_before_wait_ns=clock_before_wait,clock_ns=node.clock)
        matching=set(node.images)&set(node.infos)&set(node.cameras);check("image_info_tf_same_physical_stamp",len(matching)>=2,matching_frames=len(matching))
        key=max(matching);image=node.images[key];info=node.infos[key];tf=node.cameras[key];rgb=bytes(image.data)
        focal=image.width/(2*math.tan(math.pi/6))
        check("ros_real_camera_and_intrinsics",image.encoding=="rgb8" and image.width==640 and image.height==360
            and len(rgb)==image.width*image.height*3 and max(rgb)-min(rgb)>80 and abs(info.k[0]-focal)<.01
            and image.header.frame_id==info.header.frame_id==tf.child_frame_id,
            stamp_ns=key,width=image.width,height=image.height,focal_pixels=info.k[0],rgb_sha256=hashlib.sha256(rgb).hexdigest())
        png(args.output/"ros-camera.png",image.width,image.height,rgb)
        check("ros_sensor_state_uncalibrated_and_live",not d["imu"]["calibrated"] and not d["lidar"]["calibrated"]
            and d["lidar"]["missed_rays"]==0 and d["lidar"]["published_scans"]>10
            and all(s["invalid_samples"]==0 for s in d["imu"]["stats"]),
            imu_statistics=d["imu"]["stats"],lidar_statistics=d["lidar"])
        bridge_subscriptions=node.get_subscriber_names_and_types_by_node("rmsim_bridge","/")
        check("bridge_has_no_evaluation_input",bridge_subscriptions and not any("/evaluation/" in name for name,types in bridge_subscriptions),
            bridge_subscriptions=bridge_subscriptions)
        report.update(status="passed",final_diagnostic=d,rmw=rclpy.get_rmw_implementation_identifier(),
            received_imu_counts={name:len(v) for name,v in node.imus.items()})
    except Exception as error:report.update(status="failed",error=repr(error));raise
    finally:
        if bridge and bridge.poll() is None:
            subprocess.run(["taskkill","/PID",str(bridge.pid),"/T","/F"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
            bridge.wait(timeout=15)
        if stream:stream.close()
        (args.output/"imu-samples.json").write_text(json.dumps(node.imus,separators=(",",":"))+"\n",encoding="utf-8")
        node.destroy_node();rclpy.shutdown();save()
    print("MuJoCo native ROS sensor PASS",len(report["checks"]),"checks",flush=True)
if __name__=="__main__":main()
