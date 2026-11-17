"""Supervise the original sentry navigation stack on real simulator sensing."""
import datetime,json,math,os,subprocess,time
from pathlib import Path
import rclpy
from rclpy.node import Node
from rclpy.clock import Clock,ClockType
from std_msgs.msg import String
from geometry_msgs.msg import PoseWithCovarianceStamped
from sensor_msgs.msg import LaserScan
from lifecycle_msgs.srv import GetState
from std_srvs.srv import Empty,Trigger
from rclpy.qos import qos_profile_sensor_data
from ament_index_python.packages import get_package_prefix
from .navigation_config import prepare,INTERFACE_NODES,LOCALIZATION_NODES,NAVIGATION_NODES
from .protocol import identifier
from .service_poll import ReadOnlyServicePoll

PROGRAMS={
 'loam_interface':('loam_interface','loam_interface_node'),
 'sensor_scan_generation':('sensor_scan_generation','sensor_scan_generation_node'),
 'terrain_analysis':('terrain_analysis','terrainAnalysis'),
 'terrain_analysis_ext':('terrain_analysis_ext','terrainAnalysisExt'),
 'pointcloud_to_laserscan':('pointcloud_to_laserscan','pointcloud_to_laserscan_node'),
 'fake_vel_transform':('fake_vel_transform','fake_vel_transform_node'),
 'map_server':('nav2_map_server','map_server'),'amcl':('nav2_amcl','amcl'),
 'controller_server':('nav2_controller','controller_server'),'planner_server':('nav2_planner','planner_server'),
 'smoother_server':('nav2_smoother','smoother_server'),'behavior_server':('nav2_behaviors','behavior_server'),
 'bt_navigator':('nav2_bt_navigator','bt_navigator'),'waypoint_follower':('nav2_waypoint_follower','waypoint_follower'),
 'velocity_smoother':('nav2_velocity_smoother','velocity_smoother'),
 'lifecycle_manager_localization':('nav2_lifecycle_manager','lifecycle_manager'),
 'lifecycle_manager_navigation':('nav2_lifecycle_manager','lifecycle_manager')}
REMAPS={'pointcloud_to_laserscan': [('cloud_in','terrain_map_ext'),('scan','obstacle_scan')],
        'controller_server':[('cmd_vel','cmd_vel_controller')],
        'velocity_smoother':[('cmd_vel','cmd_vel_controller'),('cmd_vel_smoothed','cmd_vel_nav2_result')],
        'behavior_server':[('cmd_vel','cmd_vel_nav2_result')]}

class NavigationRunner(Node):
    def __init__(self):
        super().__init__('rmsim_navigation_runner')
        self.robot=identifier(self.declare_parameter('robot_id','aim_sentry').value)
        reference=self.declare_parameter('reference_config','').value;map_yaml=self.declare_parameter('map_yaml','').value
        initial=self.declare_parameter('initial_pose',[.6,-.4,.15]).value;self.initial_pose=initial
        self.runtime=self.declare_parameter('native_runtime','').value
        self.localization_only=self.declare_parameter('localization_only',False).value
        self.control_profile=self.declare_parameter('control_profile','legacy').value
        self.control_source=self.declare_parameter('control_source','').value
        base=Path(self.declare_parameter('output_directory','artifacts/navigation/live').value)
        self.output=base/(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()));self.output.mkdir(parents=True,exist_ok=False)
        cfg,self.map_metadata=prepare(reference,map_yaml,self.robot,initial,self.control_profile,self.control_source)
        from .sentry_navigation_source import source_profile
        self.source_identity=source_profile(reference,self.control_profile,self.control_source)[1]
        self.controller_config=cfg['controller_server']['ros__parameters']['FollowPath']
        self.smoother_config=cfg['velocity_smoother']['ros__parameters']
        self.params=self.output/'parameters.yaml';self.params.write_text(json.dumps(cfg,indent=2)+'\n',encoding='utf-8')
        self.state=None;self.lio=None;self.received=0.;self.identity=None;self.children={};self.streams=[];self.starts=0;self.phase='waiting'
        self.scan=None;self.pose=None;self.seeded=False;self.localized=False;self.nav_started=False;self.seed_ns=0;self.pose_stamp=0;self.good_poses=0;self.pending_state=None;self.pending_update=None;self.next_update=0.
        self.initial_pub=self.create_publisher(PoseWithCovarianceStamped,'initialpose',1)
        self.amcl_state=self.create_client(GetState,'amcl/get_state');self.nomotion=self.create_client(Empty,'request_nomotion_update')
        self.active_client=self.create_client(Trigger,'lifecycle_manager_navigation/is_active');self.pending_active=None;self.nav_active=False
        self.state_query=ReadOnlyServicePoll(2.0);self.active_query=ReadOnlyServicePoll(2.0)
        self.create_subscription(PoseWithCovarianceStamped,'amcl_pose',lambda m:setattr(self,'pose',m),1)
        self.create_subscription(LaserScan,'obstacle_scan',lambda m:setattr(self,'scan',m),qos_profile_sensor_data)
        self.create_subscription(String,'/rmsim/'+self.robot+'/state',self.observe,1)
        self.create_subscription(String,'/rmsim/'+self.robot+'/localization',lambda m:setattr(self,'lio',json.loads(m.data)),1)
        self.status=self.create_publisher(String,'/rmsim/'+self.robot+'/navigation',1)
        self.create_timer(.1,self.poll,clock=Clock(clock_type=ClockType.STEADY_TIME))

    def observe(self,message):
        value=json.loads(message.data)
        if value.get('robot_id')==self.robot:self.state=value;self.received=time.monotonic()

    def stop(self):
        for child in reversed(list(self.children.values())):
            if child.poll() is None:child.terminate()
        for child in self.children.values():
            try:child.wait(timeout=10)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
        self.children={}
        self.seeded=False;self.localized=False;self.nav_started=False;self.scan=None;self.pose=None;self.good_poses=0;self.pose_stamp=0
        for future in (self.pending_state,self.pending_update,self.pending_active):
            if future and not future.done():future.cancel()
        self.pending_state=None;self.pending_update=None;self.pending_active=None;self.nav_active=False
        self.state_query.clear(self.amcl_state);self.active_query.clear(self.active_client)
        for stream in self.streams:stream.close()
        self.streams=[]

    def start(self):
        reference=self.lio.get('reference_frame',self.robot+'/imu_link')
        if not reference.startswith(self.robot+'/') or not reference.endswith('_link'):raise ValueError('Invalid estimator reference frame')
        identifier(reference[len(self.robot)+1:-5])
        cfg=json.loads(self.params.read_text(encoding='utf-8'))
        for name in ('loam_interface','sensor_scan_generation'):cfg[name]['ros__parameters']['lidar_frame']=reference
        cfg['loam_interface']['ros__parameters']['align_gravity_initialized_pose']=self.lio.get('reference_parent_frame')==self.robot+'/yaw_link'
        self.params.write_text(json.dumps(cfg,indent=2)+'\n',encoding='utf-8')
        self.starts+=1
        names=INTERFACE_NODES+LOCALIZATION_NODES+['lifecycle_manager_localization']
        self.start_nodes(names)

    def start_nodes(self,names):
        for name in names:
            package,exe=PROGRAMS[name]
            path=Path(self.runtime)/(exe+'.exe') if os.name=='nt' else Path(get_package_prefix(package))/'lib'/package/exe
            if not path.is_file():raise RuntimeError('Missing native program '+str(path))
            command=[str(path),'--ros-args','-r','__node:='+name,'--params-file',str(self.params)]
            for before,after in REMAPS.get(name,[]):command+=['-r',before+':='+after]
            stream=(self.output/f'{self.starts:02d}-{name}.log').open('w',encoding='utf-8');self.streams.append(stream)
            options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
            self.children[name]=subprocess.Popen(command,stdout=stream,stderr=subprocess.STDOUT,**options)

    def initialize_pose(self):
        if not self.seeded:
            if not self.scan or not self.amcl_state.service_is_ready():return
            response=self.state_query.poll(self.amcl_state,GetState.Request(),time.monotonic())
            if response is None:return
            active=response.current_state.id==3
            if not active:return
            m=PoseWithCovarianceStamped();self.seed_ns=self.state['sim_time_ns'];m.header.stamp.sec=self.seed_ns//1_000_000_000;m.header.stamp.nanosec=self.seed_ns%1_000_000_000;m.header.frame_id=self.robot+'/map'
            m.pose.pose.position.x=float(self.initial_pose[0]);m.pose.pose.position.y=float(self.initial_pose[1]);yaw=float(self.initial_pose[2]);m.pose.pose.orientation.z=math.sin(yaw/2);m.pose.pose.orientation.w=math.cos(yaw/2)
            m.pose.covariance[0]=.25;m.pose.covariance[7]=.25;m.pose.covariance[35]=.09
            self.initial_pub.publish(m);self.seeded=True;self.get_logger().info('Sent explicit uncertain initial pose; no evaluation truth used.')
        if self.pending_update and self.pending_update.done():self.pending_update.result();self.pending_update=None
        if not self.localized and self.pending_update is None and time.monotonic()>=self.next_update and self.nomotion.service_is_ready():
            self.pending_update=self.nomotion.call_async(Empty.Request());self.next_update=time.monotonic()+.5
        if self.pose:
            stamp=self.pose.header.stamp.sec*1_000_000_000+self.pose.header.stamp.nanosec
            if stamp!=self.pose_stamp and stamp>=self.seed_ns:
                self.pose_stamp=stamp;cov=[self.pose.pose.covariance[i] for i in (0,7,35)]
                p=self.pose.pose.pose.position;q=self.pose.pose.pose.orientation
                good=all(math.isfinite(v) for v in (p.x,p.y,p.z,q.x,q.y,q.z,q.w)) and abs(q.x*q.x+q.y*q.y+q.z*q.z+q.w*q.w-1)<.01 and all(math.isfinite(v) and -1e-6<=v<.04 for v in cov)
                self.good_poses=self.good_poses+1 if good else 0
                if self.good_poses>=3:self.localized=True
        if self.localized and not self.localization_only and not self.nav_started:
            self.start_nodes(NAVIGATION_NODES+['lifecycle_manager_navigation']);self.nav_started=True
        if self.nav_started:
            response=self.active_query.poll(self.active_client,Trigger.Request(),time.monotonic())
            if response is not None:self.nav_active=response.success

    def poll(self):
        if self.state and self.identity is not None and self.identity!=[self.state.get('session'),self.state.get('epoch')]:
            self.stop();self.phase='waiting'
        ready=self.state and self.state.get('connected') and self.state.get('state_fresh') and time.monotonic()-self.received<1 and self.lio and self.lio.get('phase')=='running'
        if ready:
            scene=self.state.get('map',{})
            if scene.get('id')!=self.map_metadata['map_id'] or scene.get('source_md5','').lower()!=self.map_metadata['source_md5'].lower():
                self.stop();self.phase='map_mismatch';ready=False
            identity=[self.state['session'],self.state['epoch']]
            if self.lio.get('identity')!=identity:ready=False
        if ready:
            for name,child in self.children.items():
                if child.poll() is not None:raise RuntimeError('Original navigation process exited: '+name+' '+str(child.returncode))
            if self.identity!=identity or not self.children:
                self.stop();self.identity=identity;self.start()
            self.initialize_pose();self.phase='running' if self.localized and (self.localization_only or self.nav_active) else 'activating' if self.nav_started else 'localizing'
        elif time.monotonic()-self.received>1 or (self.lio and self.lio.get('phase')!='running'):
            self.stop();self.phase='waiting'
        report=dict(phase=self.phase,starts=self.starts,identity=self.identity,pids={k:v.pid for k,v in self.children.items()},
            output_directory=str(self.output),map_id=self.map_metadata['map_id'],initial_pose_sent=self.seeded,
            control_profile=self.control_profile,control_source=self.source_identity,
            navigation_velocity_limit_mps=self.controller_config['v_linear_max'],
            smoother_max_velocity=self.smoother_config['max_velocity'],
            smoother_max_accel=self.smoother_config['max_accel'],smoother_max_decel=self.smoother_config['max_decel'],localization_candidate=self.localized,read_only_rpc_timeouts=self.state_query.timeouts+self.active_query.timeouts,transport=getattr(self,'transport_profile',{'mode':'unreported'}),scope='original sentry/Nav2 nodes; no truth input; single robot')
        self.status.publish(String(data=json.dumps(report)))
        (self.output/'status.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')

    def destroy_node(self):self.stop();return super().destroy_node()

def main(args=None):
    from .native_transport import configure_navigation_transport
    transport=configure_navigation_transport()
    rclpy.init(args=args);node=NavigationRunner();node.transport_profile=transport
    try:rclpy.spin(node)
    except KeyboardInterrupt:pass
    finally:node.destroy_node();rclpy.shutdown()
