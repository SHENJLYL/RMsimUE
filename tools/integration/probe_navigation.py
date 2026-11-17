"""Independent physical-scene navigation observer; truth never feeds localization/controllers."""
import argparse,json,math,os,shutil,subprocess,time,sys
from pathlib import Path
from probe_ros_topics import Observer,ns
from navigation_recovery import fresh_reconnect
import rclpy
from rclpy.action import ActionClient
from rclpy.qos import qos_profile_sensor_data
from geometry_msgs.msg import PoseWithCovarianceStamped
from nav_msgs.msg import Odometry,Path as NavPath
from sensor_msgs.msg import LaserScan,PointCloud2
from std_msgs.msg import String
from nav2_msgs.action import NavigateToPose

class NavigationObserver(Observer):
    def __init__(self):
        super().__init__();self.odom=None;self.amcl=None;self.scans=[];self.terrain=None;self.nav=None;self.poses=[];self.plan=None;self.nav_trace=[];self.nav_transforms={};self.last_trace=0
        self.create_subscription(Odometry,'/odometry',lambda m:setattr(self,'odom',m),qos_profile_sensor_data)
        self.create_subscription(PoseWithCovarianceStamped,'/amcl_pose',lambda m:setattr(self,'amcl',m),1)
        self.create_subscription(LaserScan,'/obstacle_scan',lambda m:self.scans.append(m),qos_profile_sensor_data)
        self.create_subscription(PointCloud2,'/terrain_map_ext',lambda m:setattr(self,'terrain',m),qos_profile_sensor_data)
        self.create_subscription(String,'/rmsim/aim_sentry/navigation',lambda m:setattr(self,'nav',json.loads(m.data)),1)
        self.create_subscription(NavPath,'/plan',lambda m:setattr(self,'plan',m),1)
        self.navigator=ActionClient(self,NavigateToPose,'navigate_to_pose')
    def on_truth(self,m):
        super().on_truth(m)
        if hasattr(self,'poses'):
            self.poses.append([ns(m.header.stamp),m.pose.position.x,m.pose.position.y,m.pose.position.z])
            if ns(m.header.stamp)-self.last_trace>=50_000_000:
                self.last_trace=ns(m.header.stamp)
                row={'stamp_ns':self.last_trace,'truth':self.pose_row(m.pose)}
                if self.odom:row['odom']={'stamp_ns':ns(self.odom.header.stamp),**self.pose_row(self.odom.pose.pose)}
                if self.amcl:row['amcl']={'stamp_ns':ns(self.amcl.header.stamp),**self.pose_row(self.amcl.pose.pose),'cov':[self.amcl.pose.covariance[i] for i in (0,7,35)]}
                row['tf']=self.nav_transforms.copy();self.nav_trace.append(row)
    @staticmethod
    def pose_row(p):return {'p':[p.position.x,p.position.y,p.position.z],'q':[p.orientation.x,p.orientation.y,p.orientation.z,p.orientation.w]}
    def on_tf(self,m):
        super().on_tf(m)
        if not hasattr(self,'nav_transforms'):return
        for tf in m.transforms:
            if tf.header.frame_id in ('aim_sentry/map','aim_sentry/odom'):
                t=tf.transform;self.nav_transforms[tf.header.frame_id+'->'+tf.child_frame_id]={'stamp_ns':ns(tf.header.stamp),'p':[t.translation.x,t.translation.y,t.translation.z],'q':[t.rotation.x,t.rotation.y,t.rotation.z,t.rotation.w]}

def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=19501);p.add_argument('--output',type=Path,required=True);p.add_argument('--localization-only',action='store_true');p.add_argument('--mismatched-map',action='store_true');p.add_argument('--public-client',action='store_true');p.add_argument('--wait-reset',action='store_true')
    for name,default in [('goal-x',5.5),('goal-y',1.),('initial-x',.6),('initial-y',-.4),('initial-yaw',.15)]:p.add_argument('--'+name,type=float,default=default)
    p.add_argument('--round-trip',action='store_true');p.add_argument('--return-x',type=float,default=0.);p.add_argument('--return-y',type=float,default=0.)
    p.add_argument('--recover-session',action='store_true',help='Interrupt an active goal by restarting only the owned bridge; verify fresh localization and no stale goal replay')
    p.add_argument('--loopback-udp',action='store_true',help='Diagnostic: explicit Fast DDS IPv4 loopback profile, no shared-memory transport')
    args=p.parse_args();initial=[args.initial_x,args.initial_y,args.initial_yaw]
    if args.recover_session and (args.localization_only or args.mismatched_map):p.error('--recover-session requires actual goal navigation')
    root=Path(__file__).resolve().parents[2];args.output.mkdir(parents=True,exist_ok=False)
    report=dict(status='running',scope='real UE sensors, original sentry interfaces/AMCL/Nav2; truth only evaluates',checks=[],localization_only=args.localization_only)
    scene=json.loads((args.output.parent/'map.json').read_text(encoding='utf-8-sig'));report['map_id']=scene['id'];report['goal']=[args.goal_x,args.goal_y]
    if args.loopback_udp:
        from rmsim_bridge.native_transport import loopback_environment
        environment,transport=loopback_environment(os.environ,root/'ros2_ws/src/rmsim_bridge/config/fastdds-loopback-udp.xml')
        os.environ.update(environment)
    report['dds_transport']='diagnostic_all_loopback_udp' if args.loopback_udp else 'navigation_runner_selected'
    rclpy.init();node=NavigationObserver();children=[];logs=[];ros2=shutil.which('ros2')
    from navigation_diagnostics import NavigationDiagnostics
    diagnostics=NavigationDiagnostics(node,args.output)
    def save():
        report['last_navigation']=node.nav;report['received_scans']=len(node.scans)
        report['has_odometry']=node.odom is not None;report['has_terrain']=node.terrain is not None
        if node.odom:report['odometry_frames']=[node.odom.header.frame_id,node.odom.child_frame_id]
        if node.terrain:report['terrain_frame']=node.terrain.header.frame_id
        if node.amcl:report['last_amcl']=[node.amcl.pose.pose.position.x,node.amcl.pose.pose.position.y]
        (args.output/'verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    def check(name,passed,**data):
        report['checks'].append(dict(name=name,passed=bool(passed),**data));save()
        if not passed:raise AssertionError(name)
        print('PASS',name,flush=True)
    def launch(name,argv):
        log=(args.output/(name+'.log')).open('w',encoding='utf-8');logs.append(log)
        child=subprocess.Popen([ros2,'run']+argv,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW);children.append(child)
        return child
    def wait(predicate,timeout=90):
        deadline=time.monotonic()+timeout
        while not predicate():
            if time.monotonic()>deadline:raise TimeoutError('navigation observation timeout')
            rclpy.spin_once(node,timeout_sec=.01)
            if any(c.poll() is not None for c in children):raise RuntimeError('a supervised ROS process exited')
    try:
        launch('pointlio',['rmsim_bridge','point_lio','--ros-args','-p','output_directory:='+str(args.output/'pointlio')])
        nav=['rmsim_bridge','navigation','--ros-args','-p','reference_config:='+str(root/'ros2_ws/src/pb2025_nav_bringup/config/reality/nav2_params.yaml'),
             '-p','map_yaml:='+str(args.output.parent/'map/map.yaml'),'-p','native_runtime:='+str(root/'artifacts/runtime/navigation/windows'),'-p','output_directory:='+str(args.output/'navigation'),
             '-p','localization_only:='+str(args.localization_only).lower(),'-p','initial_pose:='+json.dumps(initial)]
        launch('navigation',nav)
        bridge=launch('bridge',['rmsim_bridge','bridge','--ros-args','-p',f'port:={args.port}'])
        if args.mismatched_map:
            wait(lambda:node.diag and node.truth and node.nav and node.nav['phase']=='map_mismatch',120)
            check('mismatched_scene_prevents_navigation',node.nav['starts']==0 and not node.nav['pids'],navigation=node.nav)
            before=node.position();start=node.clock;wait(lambda:node.clock-start>1_000_000_000,30)
            check('mismatch_leaves_vehicle_stopped',math.dist(before,node.position())<.02 and node.diag['command']['accepted_seq']==-1,drift_m=math.dist(before,node.position()))
            report['status']='passed';return
        wait(lambda:node.diag and node.diag.get('connected') and node.truth and node.nav and node.nav['phase'] in ('localizing','activating','running'),120)
        check('matching_map_identity_and_launched_processes',node.diag['map']['id']==scene['id'],navigation=node.nav,map=node.diag['map'])
        wait(lambda:node.odom and node.terrain and len(node.scans)>=5 and node.amcl,120)
        check('estimated_frame_and_scan_chain',node.odom.header.frame_id=='aim_sentry/odom' and node.odom.child_frame_id=='aim_sentry/base_link' and node.scans[-1].header.frame_id=='aim_sentry/base_stabilized' and node.terrain.width>0,odom_frame=node.odom.header.frame_id,scan_frame=node.scans[-1].header.frame_id,terrain_points=node.terrain.width,scan_bins=len(node.scans[-1].ranges))
        wait(lambda:node.nav and node.nav['localization_candidate'],90)
        estimate=node.amcl.pose.pose.position;truth=node.position();error=math.hypot(estimate.x-truth[0],estimate.y-truth[1])
        check('amcl_converges_from_perturbed_prior',error<.2,initial_prior=initial,estimated=[estimate.x,estimate.y],truth_xy=truth[:2],error_m=error,covariance=[node.amcl.pose.covariance[i] for i in (0,7,35)])
        topics=node.get_subscriber_names_and_types_by_node('amcl','/')
        check('amcl_has_no_truth_subscription',not any('/evaluation/' in name for name,types in topics),subscriptions=topics)
        if not args.localization_only:
            wait(lambda:node.nav and node.nav['phase']=='running' and node.navigator.server_is_ready(),90)
            goal=NavigateToPose.Goal();goal.pose.header.frame_id='aim_sentry/map';goal.pose.header.stamp.sec=node.clock//1_000_000_000;goal.pose.header.stamp.nanosec=node.clock%1_000_000_000
            goal.pose.pose.position.x=args.goal_x;goal.pose.pose.position.y=args.goal_y;goal.pose.pose.orientation.w=1.
            if args.recover_session:
                future=node.navigator.send_goal_async(goal);wait(future.done,20);interrupted=future.result()
                check('recovery_goal_accepted',interrupted.accepted)
                origin=node.position();wait(lambda:math.dist(origin,node.position())>.12,60)
                before=node.position();old_identity=list(node.nav['identity']);old_starts=node.nav['starts']
                # Fault injection touches only the bridge process created by this fixture.
                if os.name=='nt':
                    stopped=subprocess.run(['taskkill','/PID',str(bridge.pid),'/T','/F'],capture_output=True)
                    if stopped.returncode and bridge.poll() is None:raise RuntimeError('Could not stop the owned bridge tree')
                else:bridge.terminate()
                bridge.wait(timeout=15);children.remove(bridge)
                wait(lambda:node.nav and node.nav['phase']=='waiting' and not node.nav['pids'],45)
                check('disconnect_stops_owned_navigation_stack',not node.nav['pids'],old_identity=old_identity)
                node.amcl=None
                bridge=launch('bridge-reconnect',['rmsim_bridge','bridge','--ros-args','-p',f'port:={args.port}'])
                wait(lambda:node.diag and node.diag.get('connected') and node.nav and node.nav['phase']=='running' and node.nav['identity']!=old_identity and node.amcl,150)
                estimate=node.amcl.pose.pose.position;actual=node.position();error=math.hypot(estimate.x-actual[0],estimate.y-actual[1])
                check('reconnect_rebuilds_estimated_navigation',fresh_reconnect(old_identity,old_starts,node.nav,node.diag,error),starts=node.nav['starts'],identity=node.nav['identity'],error_m=error)
                check('disconnect_physical_braking',math.dist(before,actual)<.2,travel_m=math.dist(before,actual))
                start=node.clock;settled=node.position();wait(lambda:node.clock-start>1_000_000_000,30)
                check('reconnect_does_not_replay_interrupted_goal',math.dist(settled,node.position())<.02,drift_m=math.dist(settled,node.position()))
                node.navigator.destroy();node.navigator=ActionClient(node,NavigateToPose,'navigate_to_pose')
                wait(node.navigator.server_is_ready,30)
                goal.pose.header.stamp.sec=node.clock//1_000_000_000;goal.pose.header.stamp.nanosec=node.clock%1_000_000_000
            if args.public_client:
                log=(args.output/'goal-client.log').open('w',encoding='utf-8');logs.append(log)
                client=subprocess.Popen([sys.executable,str(root/'tools/integration/send_navigation_goal.py'),str(args.goal_x),str(args.goal_y)],stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
                deadline=time.monotonic()+320
                while client.poll() is None and time.monotonic()<deadline:rclpy.spin_once(node,timeout_sec=.01)
                if client.poll() is None:client.terminate();client.wait(timeout=5)
                check('public_goal_client_succeeds',client.returncode==0,process_exit=client.returncode);status=4
            else:
                future=node.navigator.send_goal_async(goal);wait(future.done,20);handle=future.result();check('original_nav2_accepts_goal',handle.accepted)
                result=handle.get_result_async();wait(result.done,300);status=result.result().status
            position=node.position();error=math.hypot(position[0]-args.goal_x,position[1]-args.goal_y)
            check('real_vehicle_reaches_goal',status==4 and error<.25,status=status,position=position,error_m=error)
            check('original_planner_published_path',node.plan is not None and len(node.plan.poses)>2,path_points=len(node.plan.poses) if node.plan else 0)
            if args.round_trip:
                goal.pose.pose.position.x=args.return_x;goal.pose.pose.position.y=args.return_y;goal.pose.header.stamp.sec=0;goal.pose.header.stamp.nanosec=0
                future=node.navigator.send_goal_async(goal);wait(future.done,20);handle=future.result();check('round_trip_return_accepted',handle.accepted)
                result=handle.get_result_async();wait(result.done,300);position=node.position();error=math.hypot(position[0]-args.return_x,position[1]-args.return_y)
                check('round_trip_reaches_return_goal',result.result().status==4 and error<.25,status=result.result().status,position=position,error_m=error)
            if args.wait_reset:
                goal.pose.pose.position.x=0.;goal.pose.pose.position.y=0.;goal.pose.header.stamp.sec=0;goal.pose.header.stamp.nanosec=0
                future=node.navigator.send_goal_async(goal);wait(future.done,20);handle=future.result();check('return_goal_accepted',handle.accepted)
                start=node.clock;wait(lambda:node.clock-start>2_000_000_000,40);before=node.position()
                cancel=handle.cancel_goal_async();wait(cancel.done,20);result=handle.get_result_async();wait(result.done,20)
                check('active_goal_cancelled',result.result().status==5,status=result.result().status)
                start=node.clock;wait(lambda:node.clock-start>800_000_000,30);settled=node.position();start=node.clock;wait(lambda:node.clock-start>300_000_000,20)
                check('cancel_stops_physical_vehicle',math.dist(before,settled)<.2 and math.dist(settled,node.position())<.01,braking_distance_m=math.dist(before,settled),settled_drift_m=math.dist(settled,node.position()))
                epoch=node.diag['epoch'];old_clock=node.clock;starts=node.nav['starts']
                (args.output/'ready-for-reset.json').write_text(json.dumps(dict(epoch=epoch)),encoding='utf-8')
                wait(lambda:node.diag and node.diag.get('epoch',epoch)>epoch and node.clock<old_clock,300)
                node.amcl=None;wait(lambda:node.nav and node.nav['starts']==starts+1 and node.nav['phase']=='running' and node.amcl and ns(node.amcl.header.stamp)<10_000_000_000,120)
                estimate=node.amcl.pose.pose.position;actual=node.position();error=math.hypot(estimate.x-actual[0],estimate.y-actual[1])
                check('reset_rebuilds_navigation_and_localization',error<.2,starts=node.nav['starts'],new_pose=[estimate.x,estimate.y],error_m=error)
                before=node.position();start=node.clock;wait(lambda:node.clock-start>1_000_000_000,30)
                check('reset_does_not_replay_old_goal',math.dist(before,node.position())<.02,drift_m=math.dist(before,node.position()))
        report['status']='passed'
    except Exception as e:report.update(status='failed',error=repr(e));raise
    finally:
        diagnostics.save()
        for child in children:
            if child.poll() is None:subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);child.wait(timeout=15)
        for log in logs:log.close()
        save();(args.output/'trajectory.json').write_text(json.dumps(node.poses),encoding='utf-8');(args.output/'navigation-trace.json').write_text(json.dumps(node.nav_trace),encoding='utf-8');node.destroy_node();rclpy.shutdown()
    print('Native navigation PASS',len(report['checks']),'checks')

if __name__=='__main__':main()
