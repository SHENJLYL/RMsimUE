"""Supervise the original sentry navigation stack on real simulator sensing."""
import datetime,json,math,os,subprocess,time
from pathlib import Path
import xml.etree.ElementTree as ET
from .native_transport import load_windows_fastrtps
load_windows_fastrtps()
import rclpy
from rclpy.node import Node
from rclpy.clock import Clock,ClockType
from rclpy.parameter import Parameter
from std_msgs.msg import String
from geometry_msgs.msg import PoseWithCovarianceStamped,Twist,TwistStamped
from example_interfaces.msg import Float32
from sensor_msgs.msg import LaserScan
from nav_msgs.msg import OccupancyGrid,Odometry
from action_msgs.msg import GoalStatus,GoalStatusArray
from lifecycle_msgs.srv import GetState
from std_srvs.srv import Empty,Trigger
from rclpy.qos import qos_profile_sensor_data,QoSProfile,DurabilityPolicy,ReliabilityPolicy
from ament_index_python.packages import get_package_prefix
from .navigation_config import prepare,INTERFACE_NODES,LOCALIZATION_NODES,NAVIGATION_NODES
from .protocol import identifier
from .service_poll import ReadOnlyServicePoll
from .navigation_map_identity import validate_localization_map,map_file_signatures,verify_occupancy_message
from .navigation_drive_profile import load_drive_profile

PROGRAMS={
 'loam_interface':('loam_interface','loam_interface_node'),
 'sensor_scan_generation':('sensor_scan_generation','sensor_scan_generation_node'),
 'terrain_analysis':('terrain_analysis','terrainAnalysis'),
 'terrain_analysis_ext':('terrain_analysis_ext','terrainAnalysisExt'),
 'pointcloud_to_laserscan':('pointcloud_to_laserscan','pointcloud_to_laserscan_node'),
 'fake_vel_transform':('fake_vel_transform','fake_vel_transform_node'),
 'map_server':('nav2_map_server','map_server'),'localization_map_server':('nav2_map_server','map_server'),'amcl':('nav2_amcl','amcl'),
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
        self.map_yaml=str(Path(map_yaml).resolve())
        self.localization_map_yaml=self.declare_parameter('localization_map_yaml','').value
        initial=self.declare_parameter('initial_pose',[.6,-.4,.15]).value;self.initial_pose=initial
        gate = self.declare_parameter('motion_ready_file','').value
        self.motion_ready_file = Path(gate) if gate else None
        self.motion_released = self.motion_ready_file is None
        self.runtime=self.declare_parameter('native_runtime','').value
        self.localization_only=self.declare_parameter('localization_only',False).value
        self.control_profile=self.declare_parameter('control_profile','legacy').value
        self.control_source=self.declare_parameter('control_source','').value
        self.drive_profile=self.declare_parameter('drive_profile','').value
        match_speed=float(self.declare_parameter('match_translation_limit_mps',0.0).value)
        self.native_match_behavior=bool(self.declare_parameter('native_match_behavior',False).value)
        self.behavior_spin_seen=None
        if not math.isfinite(match_speed) or match_speed < 0.:
            raise ValueError('Invalid simulator match translation limit')
        if self.drive_profile:
            self.set_parameters([Parameter('use_sim_time',Parameter.Type.BOOL,True)])
        base=Path(self.declare_parameter('output_directory','artifacts/navigation/live').value)
        self.output=base/(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()));self.output.mkdir(parents=True,exist_ok=False)
        cfg,self.map_metadata=prepare(reference,map_yaml,self.robot,initial,self.control_profile,self.control_source,self.localization_map_yaml,self.drive_profile)
        if self.native_match_behavior:
            bt=cfg['bt_navigator']['ros__parameters']
            tree_key='default_nav_through_poses_bt_xml'
            tree=ET.parse(bt[tree_key])
            passed_goals=tree.findall('.//RemovePassedGoals')
            if not passed_goals:
                raise ValueError('The original through-poses tree lacks RemovePassedGoals')
            for node in passed_goals:
                node.set('global_frame',bt['global_frame'])
                node.set('robot_base_frame',bt['robot_base_frame'])
            runtime_tree=self.output/'navigate_through_poses.xml'
            tree.write(runtime_tree,encoding='utf-8',xml_declaration=True)
            bt[tree_key]=str(runtime_tree)
        self.drive_identity=self.map_metadata.get('drive_profile')
        self.localization_nodes=list(cfg['lifecycle_manager_localization']['ros__parameters']['node_names'])
        self.map_identity=self.map_metadata.get('localization_map')
        self.map_seen={'navigation':False,'localization':False} if self.map_identity else {}
        self.map_fault='';self.next_map_check=0.
        self.map_signatures=map_file_signatures(self.map_identity) if self.map_identity else {}
        self.remaps={name:list(values) for name,values in REMAPS.items()}
        if self.drive_identity:self.remaps.update(self.drive_identity['topic_remaps'])
        if self.map_identity:
            binding=self.map_metadata['localization_map_binding']
            self.remaps.setdefault('amcl',[]).append((binding['source_topic'],binding['target_topic']))
        from .sentry_navigation_source import source_profile
        self.source_identity=source_profile(reference,self.control_profile,self.control_source)[1]
        self.controller_config=cfg['controller_server']['ros__parameters']['FollowPath']
        self.smoother_config=cfg['velocity_smoother']['ros__parameters']
        if match_speed:
            if not self.drive_identity:
                raise ValueError('Match translation limit requires a shared drive profile')
            # Preserve the reviewed profile and clearance logic. Bound simultaneous
            # translation separately from the continuous chassis spin in this mode.
            for key, sign in (('v_linear_max',1),('v_linear_min',-1)):
                value=sign*min(abs(self.controller_config[key]),match_speed)
                self.controller_config[key]=value
                self.drive_identity['controller_limits'][key]=value
            for key, sign in (('max_velocity',1),('min_velocity',-1)):
                limits=self.smoother_config[key]
                limits[:2]=[sign*min(abs(value),match_speed) for value in limits[:2]]
                self.drive_identity['smoother_limits'][key]=list(limits)
            self.drive_identity['match_translation_limit_mps']=match_speed
        if self.controller_config.pop('rmsim_bagged_bt', False):
            cfg['bt_navigator']['ros__parameters'].update(bt_loop_duration=32, default_server_timeout=500)
        self.params=self.output/'parameters.yaml';self.params.write_text(json.dumps(cfg,indent=2)+'\n',encoding='utf-8')
        self.state=None;self.lio=None;self.received=0.;self.identity=None;self.children={};self.streams=[];self.starts=0;self.phase='waiting'
        self.amcl_start_retries=0;self.amcl_active_since=0.
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
        self.reset_velocity_adapter()
        self.velocity_adapter_counts={'accepted_inputs':0,'published_outputs':0,'rejected_inputs':0,'rejected_outputs':0,
                                      'immediate_stops':0,'suppressed_smoother_outputs':0,'rejected_stop_releases':0}
        if self.drive_identity:
            topics=self.drive_identity['topics']
            self.public_velocity_pub=self.create_publisher(TwistStamped,topics['public'],1)
            if self.drive_identity.get('smoother_frame')=='navigation':
                self.create_subscription(Twist,topics['navigation_raw'],self.on_navigation_velocity,1)
                if self.drive_identity.get('actuator_command_frame')=='odometry':
                    self.world_velocity_pub=self.create_publisher(TwistStamped,topics['public_odometry'],1)
                    self.create_subscription(Twist,topics['navigation_smoothed'],self.on_odometry_smoothed_velocity,1)
                else:
                    self.create_subscription(TwistStamped,topics['body_stamped'],self.on_world_smoothed_velocity,1)
            else:
                self.body_velocity_pub=self.create_publisher(Twist,topics['body_raw'],1)
                self.create_subscription(TwistStamped,topics['body_stamped'],self.on_body_velocity,1)
                self.create_subscription(Twist,topics['body_smoothed'],self.on_smoothed_body_velocity,1)
            self.idle_spin_pub=self.create_publisher(TwistStamped,'/'+self.robot+'/navigation/cmd_vel_operator_idle',1)
            self.idle_spin_odom=None
            self.idle_spin_active_goals={}
            self.navigation_executing_goals={}
            self.idle_spin_last_motion=0.
            self.idle_spin_sent=False
            self.idle_spin_block='waiting'
            self.idle_spin_blocked_cell=None
            qos=QoSProfile(depth=1,durability=DurabilityPolicy.TRANSIENT_LOCAL,reliability=ReliabilityPolicy.RELIABLE)
            for name in ('navigate_to_pose','navigate_through_poses','follow_waypoints','follow_path'):
                self.create_subscription(GoalStatusArray,'/'+name+'/_action/status',
                    lambda msg,name=name:self.on_idle_spin_action_status(name,msg),qos)
            self.create_subscription(Odometry,'/odometry',
                lambda msg:setattr(self,'idle_spin_odom',(msg,time.monotonic())),qos_profile_sensor_data)
            self.create_subscription(Twist,topics['navigation_raw'],self.on_idle_spin_motion,1)
            if self.native_match_behavior:
                prefix='/'+self.robot+'/behavior/'
                self.create_subscription(Float32,prefix+'cmd_spin',self.on_behavior_spin,1)
                self.create_subscription(Twist,prefix+'cmd_vel',self.on_behavior_stop,1)
        if self.map_identity:
            qos=QoSProfile(depth=1,reliability=ReliabilityPolicy.RELIABLE,durability=DurabilityPolicy.TRANSIENT_LOCAL)
            self.create_subscription(OccupancyGrid,cfg['map_server']['ros__parameters'].get('topic_name','map'),
                                     lambda message:self.observe_map('navigation',message),qos)
            self.create_subscription(OccupancyGrid,self.map_metadata['localization_map_binding']['target_topic'],
                                     lambda message:self.observe_map('localization',message),qos)
        self.create_timer(.1,self.poll,clock=Clock(clock_type=ClockType.STEADY_TIME))

    def velocity_adapter_ready(self):
        return bool(self.motion_released and self.drive_identity and self.nav_started and self.state
                    and self.state.get('connected') and self.state.get('state_fresh')
                    and self.identity==[self.state.get('session'),self.state.get('epoch')])

    def reset_velocity_adapter(self):
        self.body_input_ns=None;self.body_output_ns=None;self.body_stop_ns=None
        self.navigation_motion_ns=None
        self.navigation_result_brake=False

    @staticmethod
    def finite_planar_velocity(twist):
        return (all(math.isfinite(value) for value in (
                    twist.linear.x,twist.linear.y,twist.linear.z,
                    twist.angular.x,twist.angular.y,twist.angular.z))
                and twist.linear.z==0 and twist.angular.x==0 and twist.angular.y==0)

    @staticmethod
    def stop_velocity(twist):
        # Match the bridge's physical-brake threshold after planar validation.
        return all(abs(value)<1e-4 for value in (twist.linear.x,twist.linear.y,twist.angular.z))

    def on_idle_spin_action_status(self, name, message):
        executing={GoalStatus.STATUS_ACCEPTED,GoalStatus.STATUS_EXECUTING}
        busy=executing | {GoalStatus.STATUS_CANCELING}
        running=any(goal.status in executing for goal in message.status_list)
        was_running=self.navigation_executing_goals.get(name,False)
        self.navigation_executing_goals[name]=running
        self.idle_spin_active_goals[name]=any(goal.status in busy for goal in message.status_list)
        outer_running=any(value for key,value in self.navigation_executing_goals.items() if key!='follow_path')
        outer_busy=any(value for key,value in self.idle_spin_active_goals.items() if key!='follow_path')
        if was_running and not running and not outer_running:
            # An action result can arrive without a surviving cmd_vel zero.
            # Stop at the result and block any residual smoother or controller output.
            self.navigation_result_brake=True
            self.publish_navigation_brake()
        elif running and not was_running and (name!='follow_path' or not outer_busy):
            self.navigation_result_brake=False

    def on_idle_spin_motion(self, message):
        if self.finite_planar_velocity(message) and not self.stop_velocity(message):
            self.idle_spin_last_motion=time.monotonic()

    def update_operator_idle(self):
        if not self.drive_identity:
            return
        wall=time.monotonic()
        active=any(self.idle_spin_active_goals.values()) or wall-self.idle_spin_last_motion<.35
        drive=(self.state or {}).get('drive') or {}
        requested=drive.get('operator_gyro_setpoint_rad_s',0.)
        if self.native_match_behavior:
            sample=self.behavior_spin_seen
            if not sample or wall-sample[1]>.3 or sample[0]<=0.:
                requested=0.
        ready=(self.phase=='running' and self.velocity_adapter_ready()
               and wall-self.received<=.25 and not active)
        self.idle_spin_block='navigation_active' if active else ''
        self.idle_spin_blocked_cell=None
        # Keep stationary gyro spinning up to GyroMaxRadS (12 rad/s) in match autonomy
        max_spin=float((self.drive_identity.get('vehicle_control') or {}).get('GyroMaxRadS', 5.0))
        allow=(ready and type(requested) in (int,float) and 0.<requested<=max_spin
               and drive.get('operator_gyro_available') is True)
        if allow or (ready and self.idle_spin_sent):
            stamp=self.get_clock().now()
            if stamp.nanoseconds>0:
                cmd=TwistStamped()
                cmd.header.stamp=stamp.to_msg()
                cmd.header.frame_id=self.robot+'/base_link'
                if allow and self.native_match_behavior:
                    cmd.twist.angular.z=float(requested)
                    self.public_velocity_pub.publish(cmd)
                else:
                    (self.idle_spin_pub if allow else self.public_velocity_pub).publish(cmd)
        self.idle_spin_sent=bool(allow)

    def on_behavior_spin(self,message):
        value=float(message.data)
        max_spin=float((self.drive_identity.get('vehicle_control') or {}).get('GyroMaxRadS', 12.0)) if self.drive_identity else 12.0
        if math.isfinite(value) and 0.<=value<=max_spin:
            self.behavior_spin_seen=(value,time.monotonic())

    def on_behavior_stop(self,message):
        # All original match-tree Twist outputs are stops. Nav2 owns translation.
        if self.finite_planar_velocity(message) and self.stop_velocity(message) and self.body_stop_ns is None:
            self.publish_navigation_brake()

    def publish_navigation_brake(self):
        now=self.get_clock().now().nanoseconds
        if not self.velocity_adapter_ready() or now<=0:
            return
        self.navigation_motion_ns=None
        self.body_input_ns=now
        self.body_stop_ns=now
        if self.publish_body_velocity(Twist()):
            self.velocity_adapter_counts['immediate_stops']+=1

    def on_navigation_velocity(self, message):
        if not self.velocity_adapter_ready() or not self.finite_planar_velocity(message):
            return
        now=self.get_clock().now().nanoseconds
        if now<=0:
            return
        if self.stop_velocity(message):
            self.publish_navigation_brake()
        elif not self.navigation_result_brake and (self.body_stop_ns is None or now>self.body_stop_ns):
            self.navigation_motion_ns=now

    def on_odometry_smoothed_velocity(self, message):
        now=self.get_clock().now().nanoseconds
        if (not self.velocity_adapter_ready() or self.navigation_motion_ns is None
                or now-self.navigation_motion_ns>250_000_000
                or not self.finite_planar_velocity(message) or now<=0):
            return
        if self.body_stop_ns is not None and now<=self.body_stop_ns:
            return
        if not self.idle_spin_odom or time.monotonic()-self.idle_spin_odom[1]>.23:
            return
        observed=self.idle_spin_odom[0]
        age=now-observed.header.stamp.sec*1_000_000_000-observed.header.stamp.nanosec
        if not -50_000_000<=age<=230_000_000:
            return

        cap=self.drive_identity['vehicle_control']['MaxForwardSpeedMps']
        magnitude=math.hypot(message.linear.x,message.linear.y)
        ratio=min(1.,cap/magnitude) if magnitude>0 else 1.
        target=TwistStamped()
        target.header.frame_id=self.robot+'/odom'
        target.header.stamp=self.get_clock().now().to_msg()
        target.twist.linear.x=ratio*message.linear.x
        target.twist.linear.y=ratio*message.linear.y
        target.twist.angular.z=message.angular.z
        self.body_input_ns=now
        self.body_output_ns=now
        self.body_stop_ns=None
        self.world_velocity_pub.publish(target)
        self.velocity_adapter_counts['published_outputs']+=1

    def on_world_smoothed_velocity(self, message):
        now=self.get_clock().now().nanoseconds
        stamp=message.header.stamp.sec*1_000_000_000+message.header.stamp.nanosec
        if (not self.velocity_adapter_ready() or stamp<=0 or now-stamp< -50_000_000
                or now-stamp>250_000_000 or message.header.frame_id!=self.robot+'/base_link'
                or not self.finite_planar_velocity(message.twist)):
            self.velocity_adapter_counts['rejected_inputs']+=1
            return
        if self.navigation_motion_ns is None or now-self.navigation_motion_ns>250_000_000:
            return
        self.body_input_ns=stamp
        if self.body_stop_ns is not None and stamp<=self.body_stop_ns:
            return
        self.body_stop_ns=None
        self.velocity_adapter_counts['accepted_inputs']+=1
        self.publish_body_velocity(message.twist)

    def on_body_velocity(self,message):
        now=self.get_clock().now().nanoseconds
        stamp=message.header.stamp.sec*1_000_000_000+message.header.stamp.nanosec
        timeout_ns=round(self.smoother_config['velocity_timeout']*1_000_000_000)
        period_ns=round(1_000_000_000/self.smoother_config['smoothing_frequency'])
        valid=(self.velocity_adapter_ready() and message.header.frame_id==self.robot+'/base_link'
               and self.finite_planar_velocity(message.twist) and stamp>0
               and -period_ns<=now-stamp<=timeout_ns
               and (self.body_input_ns is None or stamp>=self.body_input_ns))
        if not valid:
            self.velocity_adapter_counts['rejected_inputs']+=1;return
        stop=self.stop_velocity(message.twist)
        if not stop and self.navigation_result_brake:
            self.velocity_adapter_counts['rejected_stop_releases']+=1
            return
        if self.body_stop_ns is not None and not stop and stamp<=self.body_stop_ns:
            # A reordered command sharing the stop's clock tick cannot release it.
            self.velocity_adapter_counts['rejected_inputs']+=1
            self.velocity_adapter_counts['rejected_stop_releases']+=1;return
        self.body_input_ns=stamp
        self.body_stop_ns=stamp if stop else None
        command=Twist() if stop else message.twist
        if stop and self.publish_body_velocity(command):
            self.velocity_adapter_counts['immediate_stops']+=1
        self.body_velocity_pub.publish(command)
        self.velocity_adapter_counts['accepted_inputs']+=1

    def publish_body_velocity(self,message):
        now=self.get_clock().now().nanoseconds
        # Only live callbacks publish. Normal motion keeps the original smoother's
        # timeout/ramp; an accepted explicit zero reaches the vehicle brake now.
        valid=(self.velocity_adapter_ready() and self.body_input_ns is not None
               and now>0 and self.finite_planar_velocity(message)
               and (self.body_output_ns is None or now>=self.body_output_ns))
        if not valid:
            self.velocity_adapter_counts['rejected_outputs']+=1;return False
        output=TwistStamped();output.header.frame_id=self.robot+'/base_link'
        output.header.stamp.sec=now//1_000_000_000
        output.header.stamp.nanosec=now%1_000_000_000
        output.twist=Twist() if self.body_stop_ns is not None else message
        self.public_velocity_pub.publish(output);self.body_output_ns=now
        self.velocity_adapter_counts['published_outputs']+=1
        return True

    def on_smoothed_body_velocity(self,message):
        if getattr(self,'idle_spin_sent',False) and self.stop_velocity(message):
            return
        if (self.publish_body_velocity(message) and self.body_stop_ns is not None
                and not self.stop_velocity(message)):
            self.velocity_adapter_counts['suppressed_smoother_outputs']+=1

    def observe_map(self,role,message):
        try:
            verify_occupancy_message(message,self.map_identity[role],self.robot+'/map')
            self.map_seen[role]=True
        except (ValueError,TypeError,AttributeError) as error:
            self.map_fault=role+': '+str(error)

    def verify_map_inputs(self):
        if self.map_identity:
            current=validate_localization_map(self.map_yaml,self.localization_map_yaml)
            if current!=self.map_identity:raise ValueError('Map inputs changed after navigation preparation')
            self.map_signatures=map_file_signatures(self.map_identity)

    def observe(self,message):
        value=json.loads(message.data)
        if value.get('robot_id')==self.robot:
            if (not value.get('connected') or not value.get('state_fresh')
                    or (self.state is not None and
                        (not self.state.get('connected') or
                         [self.state.get('session'),self.state.get('epoch')]!=
                         [value.get('session'),value.get('epoch')]))):
                self.reset_velocity_adapter()
            self.state=value;self.received=time.monotonic()

    def stop(self):
        self.reset_velocity_adapter()
        for child in reversed(list(self.children.values())):
            if child.poll() is None:child.terminate()
        for child in self.children.values():
            try:child.wait(timeout=10)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
        self.children={}
        self.map_seen={role:False for role in self.map_seen}
        self.seeded=False;self.localized=False;self.nav_started=False;self.scan=None;self.pose=None;self.good_poses=0;self.pose_stamp=0;self.amcl_active_since=0.
        for future in (self.pending_state,self.pending_update,self.pending_active):
            if future and not future.done():future.cancel()
        self.pending_state=None;self.pending_update=None;self.pending_active=None;self.nav_active=False
        self.state_query.clear(self.amcl_state);self.active_query.clear(self.active_client)
        for stream in self.streams:stream.close()
        self.streams=[]

    def start(self):
        self.verify_map_inputs() # Revalidate before every session/epoch reload, not only at construction.
        if self.drive_identity and load_drive_profile(self.drive_profile)['source_sha256']!=self.drive_identity['source_sha256']:
            raise ValueError('Vehicle drive profile changed after navigation preparation')
        reference=self.lio.get('reference_frame',self.robot+'/imu_link')
        if not reference.startswith(self.robot+'/') or not reference.endswith('_link'):raise ValueError('Invalid estimator reference frame')
        identifier(reference[len(self.robot)+1:-5])
        cfg=json.loads(self.params.read_text(encoding='utf-8'))
        for name in ('loam_interface','sensor_scan_generation'):cfg[name]['ros__parameters']['lidar_frame']=reference
        cfg['loam_interface']['ros__parameters']['align_gravity_initialized_pose']=self.lio.get('reference_parent_frame')==self.robot+'/yaw_link'
        self.params.write_text(json.dumps(cfg,indent=2)+'\n',encoding='utf-8')
        self.starts+=1
        names=INTERFACE_NODES+self.localization_nodes+['lifecycle_manager_localization']
        self.start_nodes(names)

    def start_nodes(self,names):
        for name in names:
            package,exe=PROGRAMS[name]
            path=Path(self.runtime)/(exe+'.exe') if os.name=='nt' else Path(get_package_prefix(package))/'lib'/package/exe
            if not path.is_file():raise RuntimeError('Missing native program '+str(path))
            command=[str(path),'--ros-args','-r','__node:='+name,'--params-file',str(self.params)]
            for before,after in self.remaps.get(name,[]):command+=['-r',before+':='+after]
            stream=(self.output/f'{self.starts:02d}-{name}.log').open('w',encoding='utf-8');self.streams.append(stream)
            options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
            self.children[name]=subprocess.Popen(command,stdout=stream,stderr=subprocess.STDOUT,**options)

    def initialize_pose(self):
        if self.map_identity and (self.map_fault or not all(self.map_seen.values())):return
        if not self.seeded:
            if not self.scan or not self.amcl_state.service_is_ready():return
            response=self.state_query.poll(self.amcl_state,GetState.Request(),time.monotonic())
            if response is None:return
            active=response.current_state.id==3
            if not active:return
            now_wall=time.monotonic()
            if self.amcl_active_since<=0.:
                self.amcl_active_since=now_wall
                return
            # Give Windows nav2_amcl a brief settle window after Active/createLaserObject before initialpose.
            if now_wall-self.amcl_active_since<0.35:return
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
            self.stop();self.phase='waiting';self.map_fault=''
            self.amcl_start_retries=0
        ready=self.state and self.state.get('connected') and self.state.get('state_fresh') and time.monotonic()-self.received<1 and self.lio and self.lio.get('phase')=='running'
        if ready:
            scene=self.state.get('map',{})
            if scene.get('id')!=self.map_metadata['map_id'] or scene.get('source_md5','').lower()!=self.map_metadata['source_md5'].lower():
                self.stop();self.phase='map_mismatch';ready=False
            identity=[self.state['session'],self.state['epoch']]
            if self.lio.get('identity')!=identity:ready=False
            if self.map_identity:
                if time.monotonic()>=self.next_map_check:
                    self.next_map_check=time.monotonic()+1.
                    try:
                        if map_file_signatures(self.map_identity)!=self.map_signatures:self.verify_map_inputs()
                    except ValueError as error:self.map_fault=str(error)
                if self.map_fault:
                    self.stop();self.phase='localization_map_mismatch';ready=False
        if ready:
            for name,child in self.children.items():
                code=child.poll()
                if code is None:
                    continue
                if (os.name=='nt' and name=='amcl' and (code & 0xffffffff)==0xc0000005
                        and (self.state.get('drive') or {}).get('match_autonomy') is True
                        and not self.localized and not self.nav_started and self.amcl_start_retries < 5):
                    self.amcl_start_retries+=1
                    self.get_logger().warning(
                        f'AMCL exited with 0xc0000005 before localization; restarting localization ({self.amcl_start_retries}/5).')
                    self.stop()
                    self.start()
                    break
                raise RuntimeError('Original navigation process exited: '+name+' '+str(code))
            if self.identity!=identity or not self.children:
                self.stop();self.identity=identity;self.start()
            self.initialize_pose();self.phase='running' if self.localized and (self.localization_only or self.nav_active) else 'activating' if self.nav_started else 'localizing'
        elif (self.children and self.state and self.state.get('connected') and self.lio and
              self.lio.get('phase')=='waiting_for_stream' and self.lio.get('identity')==self.identity and
              time.monotonic()-self.received<2):
            self.phase='waiting_for_stream'
        elif time.monotonic()-self.received>1 or (self.lio and self.lio.get('phase')!='running'):
            self.stop();self.phase='waiting'
        if not self.motion_released and self.motion_ready_file.is_file():
            self.motion_released = True
        self.update_operator_idle()
        report=dict(phase=self.phase,starts=self.starts,identity=self.identity,pids={k:v.pid for k,v in self.children.items()},
            amcl_start_retried=self.amcl_start_retries>0,
            amcl_start_retries=self.amcl_start_retries,
            output_directory=str(self.output),map_id=self.map_metadata['map_id'],initial_pose_sent=self.seeded,
            localization_map=self.map_identity,localization_map_verified=not self.map_identity or (all(self.map_seen.values()) and not self.map_fault),
            localization_map_topics=self.map_metadata.get('localization_map_binding'),map_input_fault=self.map_fault,
            control_profile=self.control_profile,control_source=self.source_identity,
            operator_idle_spin=getattr(self,'idle_spin_sent',False),
            operator_idle_reason=getattr(self,'idle_spin_block','unavailable'),
            operator_idle_blocked_cell=getattr(self,'idle_spin_blocked_cell',None),
            drive_profile=self.drive_identity,velocity_adapter_counts=self.velocity_adapter_counts,
            velocity_stop_policy='immediate_zero_target_latch_v1' if self.drive_identity else 'legacy',
            velocity_stop_latched=self.body_stop_ns is not None,velocity_stop_stamp_ns=self.body_stop_ns,
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
