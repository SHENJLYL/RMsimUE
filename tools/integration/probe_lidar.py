"""Real UE LiDAR and original Livox messages; optional original Point-LIO closure."""
import argparse,json,math,os,shutil,struct,subprocess,time
from pathlib import Path
from probe_ros_topics import Observer,ns,rotate
import rclpy
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import PointCloud2
from nav_msgs.msg import Odometry
from std_msgs.msg import String
from probe_native_bridge import multiply
from rmsim_bridge.control_dlls import load_package_dlls
load_package_dlls('livox_ros_driver2')
from livox_ros_driver2.msg import CustomMsg

class LidarObserver(Observer):
    def __init__(self,motor=False):
        super().__init__();self.clouds={};self.raw={};self.odom=[];self.localization=None;self.motor=motor
        if motor:
            load_package_dlls('custom_msgs','chassis_controllers')
            from chassis_controllers.msg import ChassisControl
            self.motor_type=ChassisControl;self.motor_pub=self.create_publisher(ChassisControl,'/rmsim/aim_sentry/chassis_command',1)
        self.create_subscription(PointCloud2,'/aim_sentry/lidar/points',lambda m:self.keep(self.clouds,ns(m.header.stamp),m),qos_profile_sensor_data)
        self.create_subscription(CustomMsg,'/aim_sentry/lidar/livox',lambda m:self.keep(self.raw,ns(m.header.stamp),m),qos_profile_sensor_data)
        self.create_subscription(Odometry,'/aft_mapped_to_init',lambda m:self.odom.append(m),20)
        self.create_subscription(String,'/rmsim/aim_sentry/localization',lambda m:setattr(self,'localization',json.loads(m.data)),10)
    def keep(self,where,key,value):
        where[key]=value
        while len(where)>8:del where[next(iter(where))]
    def command(self,vx=0,vy=0,wz=0):
        if not self.motor:return super().command(vx,vy,wz)
        msg=self.motor_type();msg.x_speed=float(vx);msg.y_speed=float(vy);msg.spin_speed=float(wz);msg.power_limit=100;self.motor_pub.publish(msg)

def intersection(origin,direction,box):
    lo,hi=box;near=0.;far=math.inf
    for i in range(3):
        if abs(direction[i])<1e-12:
            if not lo[i]<=origin[i]<=hi[i]:return math.inf
        else:
            a=(lo[i]-origin[i])/direction[i];b=(hi[i]-origin[i])/direction[i]
            near=max(near,min(a,b));far=min(far,max(a,b))
    return near if far>=near and far>0 else math.inf

def main():
    argspec=argparse.ArgumentParser();argspec.add_argument('--port',type=int,default=19481);argspec.add_argument('--output',type=Path,required=True);argspec.add_argument('--with-lio',action='store_true');argspec.add_argument('--wait-reset',action='store_true');argspec.add_argument('--motor-control',action='store_true');args=argspec.parse_args()
    root=Path(__file__).resolve().parents[2];args.output.mkdir(parents=True,exist_ok=False)
    profile=json.loads((args.output.parent/'lidar.json').read_text(encoding='utf-8-sig'))
    roll,pitch,yaw=profile['origin']['rpy'];mount_position=profile['origin']['xyz']
    mount_q=multiply(multiply([0,0,math.sin(yaw/2),math.cos(yaw/2)],[0,math.sin(pitch/2),0,math.cos(pitch/2)]),[math.sin(roll/2),0,0,math.cos(roll/2)])
    report=dict(status='running',scope='Real UE raycast, native ROS original Livox messages; truth only for independent evaluation',with_lio=args.with_lio,motor_control=args.motor_control,checks=[])
    rclpy.init();node=LidarObserver(args.motor_control);children=[];logs=[]
    def save():
        (args.output/'verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    def check(name,passed,**details):
        report['checks'].append(dict(name=name,passed=bool(passed),**details));save()
        if not passed:raise AssertionError(name)
    def launch(command,name):
        log=(args.output/(name+'.log')).open('w',encoding='utf-8');logs.append(log)
        child=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=os.environ.copy(),creationflags=subprocess.CREATE_NO_WINDOW);children.append(child);return child
    def pump(seconds,velocity=None):
        end=node.clock+round(seconds*1e9);deadline=time.monotonic()+120;next_command=0
        while node.clock<end:
            if time.monotonic()>deadline:raise TimeoutError('physical simulation clock stalled')
            rclpy.spin_once(node,timeout_sec=.002)
            if velocity is not None and time.monotonic()>=next_command:node.command(*velocity);next_command=time.monotonic()+.03
    def points(cloud):return list(struct.iter_unpack('<IfffBBBB',cloud.data))
    def body(stamp):
        key=min(node.truths,key=lambda x:abs(x-stamp));p=node.truths[key].pose
        return [p.position.x,p.position.y,p.position.z],[p.orientation.x,p.orientation.y,p.orientation.z,p.orientation.w]
    def distances(cloud,end_pose=False):
        errors=[];rows=points(cloud);end=ns(cloud.header.stamp)+max(r[0] for r in rows)
        for dt,x,y,z,*_ in rows[::17]:
            distance=math.sqrt(x*x+y*y+z*z);pos,q=body(end if end_pose else ns(cloud.header.stamp)+dt)
            mount=rotate(q,mount_position);origin=[pos[i]+mount[i] for i in range(3)];direction=rotate(multiply(q,mount_q),[x/distance,y/distance,z/distance])
            expected=min(intersection(origin,direction,box) for box in boxes)
            errors.append(abs(expected-distance))
        errors.sort();return errors
    mapdata=json.loads((args.output.parent/'map.json').read_text(encoding='utf-8-sig'))
    boxes=[([-10,-10,-.2],[10,10,0]),([-10,-10,0],[-9.8,10,3]),([9.8,-10,0],[10,10,3]),([-10,-10,0],[10,-9.8,3]),([-10,9.8,0],[10,10,3])]
    flat=mapdata['kind']=='rm_flat_map';report['map_id']=mapdata['id'];report['geometry_oracle']='analytic first intersection' if flat else 'mesh scene; distance oracle separately verified in flat fixture'
    for ob in mapdata.get('obstacles',[]):
        c=ob['center_m'];s=ob['size_m'];boxes.append(([c[i]-s[i]/2 for i in range(3)],[c[i]+s[i]/2 for i in range(3)]))
    try:
        if args.with_lio:
            launch([shutil.which('ros2'),'run','rmsim_bridge','point_lio','--ros-args','-p','output_directory:='+str(args.output/'localization')],'pointlio-runner')
        if args.motor_control:
            launch([shutil.which('ros2'),'run','chassis_controllers','lk_full_wheel_chassis_controller','--ros-args','--params-file',str(root/'assets/vehicles/aim_sentry/adapters/sentry_lk.yaml')],'lk-controller')
        launch([shutil.which('ros2'),'run','rmsim_bridge','bridge','--ros-args','-p',f'port:={args.port}','-p','control_backend:='+('lk' if args.motor_control else 'velocity')],'bridge')
        node.wait(lambda:node.truth and node.diag and node.diag.get('lidar') and len(node.raw)>=4 and len(node.images)>=2,90)
        pump(2.)
        keys=set(node.raw)&set(node.clouds);check('original_livox_and_pointcloud2_received',len(keys)>=3,matching_scans=len(keys))
        key=max(keys);raw=node.raw[key];cloud=node.clouds[key];rows=points(cloud)
        check('original_message_fields_and_payload',raw.timebase==key and raw.point_num==len(raw.points)==cloud.width==len(rows) and all((p.offset_time,p.x,p.y,p.z,p.reflectivity,p.tag,p.line)==r[:7] for p,r in zip(raw.points,rows)),points=len(rows),frame=raw.header.frame_id)
        if flat:
            errors=distances(cloud);p95=errors[int(.95*len(errors))]
            check('actual_scene_first_return_distance',p95<.015 and max(errors)<.06,p95_error_m=p95,max_error_m=max(errors),checked_rays=len(errors))
        else:check('mesh_scene_has_measured_returns',len(rows)>1000,points=len(rows))
        pose_wire=node.diag['lidar']['pose_in_base']
        check('configured_mount_preserved',math.dist(pose_wire['position_m'],mount_position)<1e-8 and abs(abs(sum(a*b for a,b in zip(pose_wire['rotation_xyzw'],mount_q)))-1)<1e-8,mount=pose_wire)
        offsets=[r[0] for r in rows];unique=sorted(set(offsets));check('rolling_actual_acquisition_times',len(unique)>=80 and max(offsets)-min(offsets)>80_000_000,distinct_times=len(unique),span_ns=max(offsets)-min(offsets))
        data=node.diag['lidar'];check('physical_sampling_budget',data['traced_rays']>100000 and data['missed_rays']==0 and data['max_physics_step_s']<.00101,metadata=data)
        report['initial_cloud_points']=rows[::17]
        initial=node.position();initial_odom=node.odom[-1] if node.odom else None
        initial_q=[node.truth.pose.orientation.x,node.truth.pose.orientation.y,node.truth.pose.orientation.z,node.truth.pose.orientation.w]
        initial_imu=[a+b for a,b in zip(initial,rotate(initial_q,[0,0,.15]))]
        pump(4.,(.35,0,0));moving=node.clouds[max(node.clouds)]
        if flat:
            rolling=distances(moving);snapshot=distances(moving,True)
            rolling_mean=sum(rolling)/len(rolling);snapshot_mean=sum(snapshot)/len(snapshot)
            check('motion_distortion_follows_sample_time',rolling_mean<.02 and snapshot_mean>rolling_mean*1.5,rolling_error_m=rolling_mean,snapshot_error_m=snapshot_mean)
        pump(.8,(0,0,0));delta=[a-b for a,b in zip(node.position(),initial)]
        check('chassis_control_remains_live',.7<delta[0]<1.6 and abs(delta[1])<.1,actual_displacement_m=delta)
        if args.motor_control:
            adapter=node.diag.get('lk_adapter',{})
            check('original_lk_current_loop_with_sensors',adapter.get('status')=='current_control' and adapter.get('feedback_samples',0)>100,adapter=adapter)
        if args.with_lio:
            check('original_lio_received_real_sensors',initial_odom is not None and len(node.odom)>30,odometry_count=len(node.odom))
            topics=node.get_subscriber_names_and_types_by_node('laserMapping','/')
            names={name for name,types in topics}
            check('original_lio_has_no_truth_subscription','/aim_sentry/lidar/livox' in names and '/aim_sentry/imu/data_raw' in names and not any('/evaluation/' in name for name in names),subscriptions=topics)
            final=node.odom[-1];a=initial_odom.pose.pose.position;b=final.pose.pose.position;estimated=[b.x-a.x,b.y-a.y,b.z-a.z]
            q0=initial_odom.pose.pose.orientation;alignment=multiply(initial_q,[-q0.x,-q0.y,-q0.z,q0.w])
            aligned=rotate(alignment,estimated)
            final_q=[node.truth.pose.orientation.x,node.truth.pose.orientation.y,node.truth.pose.orientation.z,node.truth.pose.orientation.w]
            actual_imu=[a+b-c for a,b,c in zip(node.position(),rotate(final_q,[0,0,.15]),initial_imu)]
            error=math.dist(aligned,actual_imu)
            check('original_lio_tracks_ue_motion',all(math.isfinite(v) for v in aligned) and error<.15,estimate_delta_m=estimated,aligned_estimate_delta_m=aligned,truth_imu_delta_m=actual_imu,error_m=error,evaluation='one initial orientation alignment; actual IMU mounting point; no truth enters estimator')
            report['odometry']=[dict(t=ns(m.header.stamp),p=[m.pose.pose.position.x,m.pose.pose.position.y,m.pose.pose.position.z]) for m in node.odom]
        if args.wait_reset:
            epoch=node.diag['epoch'];old_clock=node.clock;old_starts=node.localization['starts'] if node.localization else 0
            (args.output/'ready-for-reset.json').write_text(json.dumps(dict(epoch=epoch)),encoding='utf-8')
            node.wait(lambda:node.diag and node.diag.get('epoch',epoch)>epoch and node.clock<old_clock,300)
            node.raw.clear();node.clouds.clear();node.odom.clear();pump(2.)
            check('reset_discards_old_lidar_scans',len(node.raw)>3 and all(k<3_000_000_000 for k in node.raw),new_scan_times=list(node.raw))
            if args.with_lio:
                fresh=[m for m in node.odom if ns(m.header.stamp)<3_000_000_000]
                check('original_lio_restarts_after_reset',node.localization and node.localization['starts']==old_starts+1 and len(fresh)>5,supervisor=node.localization,new_odometry_count=len(fresh))
                position=fresh[-1].pose.pose.position
                check('new_epoch_localization_origin',math.sqrt(position.x**2+position.y**2+position.z**2)<.08,position=[position.x,position.y,position.z])
        report['status']='passed'
    except Exception as e:report['status']='failed';report['error']=repr(e);raise
    finally:
        for child in children:
            if child.poll() is None:subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);child.wait(timeout=15)
        for log in logs:log.close()
        save();node.destroy_node();rclpy.shutdown()
    print('Native LiDAR PASS',len(report['checks']),'checks')

if __name__=='__main__':main()
