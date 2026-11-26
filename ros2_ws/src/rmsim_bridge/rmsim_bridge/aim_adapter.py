"""Pinned sentry vision/scan message adapter; this is not the original hardware Hub."""
import math
import time
from rclpy.qos import QoSProfile,ReliabilityPolicy
from std_msgs.msg import Int32
from .control_dlls import load_package_dlls
load_package_dlls('sp_msgs','pb_rm_interfaces')
from sp_msgs.msg import AutoAimCommandMsg
from pb_rm_interfaces.msg import GimbalCmd
from .protocol import MATCH_FIRE_OBSERVATION_NS, MATCH_TRACK_MEMORY_NS, ProtocolError, aim_packet, hold_aim, number


class AimAdapter:
    def __init__(self,node):
        self.node=node;self.identity=None;self.permission=False;self.auto=None;self.scan=None
        self.navigation=None
        if node.declare_parameter('navigation_follow',False).value:
            from .navigation_follow_adapter import NavigationFollowAdapter
            self.navigation=NavigationFollowAdapter(node)
        self.serial=0;self.sent=None;self.rejected=0;self.error='';self.source='hold';self.legacy_frame=False
        self.last_stamp={'auto':-1,'scan':-1}
        self.auto_holding=False
        qos=QoSProfile(depth=1,reliability=ReliabilityPolicy.RELIABLE)
        prefix='/'+node.robot_id
        self.subs=[node.create_subscription(AutoAimCommandMsg,prefix+'/autoaim_command',self.on_auto,qos),
                   node.create_subscription(GimbalCmd,prefix+'/gimbal_scan_cmd',self.on_scan,qos),
                   node.create_subscription(Int32,prefix+'/auto_aim_switch',self.on_switch,qos)]

    def observe(self,state):
        key=(state['session'],state['epoch'])
        if key!=self.identity:
            self.identity=key;self.permission=False;self.auto=None;self.scan=None;self.sent=None
            self.last_stamp={'auto':-1,'scan':-1}
            self.auto_holding=False
            if self.navigation:self.navigation.reset()

    def ready(self):
        node=self.node
        if not node.transport.connected or node.state is None or time.monotonic()-node.received_at>.2:
            raise ProtocolError('no fresh aim state')
        if not node.state.get('aim',{}).get('enabled'):raise ProtocolError('UE RosAimControl is not enabled')

    def reject(self,error):
        self.error=str(error);self.rejected+=1;self.auto=None;self.scan=None;self.permission=False;self.sent=None

    def checked(self,source,stamp_ns,command):
        self.ready()
        if stamp_ns<self.last_stamp[source]:raise ProtocolError('aim source stamp moved backwards')
        aim_packet(self.node.state,1,stamp_ns,command)
        limits=self.node.state['aim']
        for axis in ('yaw','pitch'):
            if abs(command[axis+'_rate'])>limits[axis+'_rate_limit']:raise ProtocolError('gimbal rate exceeds actuator limit')
        self.last_stamp[source]=stamp_ns;self.serial+=1;self.error=''
        return dict(stamp=stamp_ns,command=command,received=time.monotonic(),serial=self.serial)

    def on_switch(self,message):
        try:
            if message.data not in (0,1):raise ProtocolError('auto_aim_switch must be0 or1')
            if message.data:self.ready()
            self.permission=bool(message.data)
        except ProtocolError as error:self.reject(error)

    def on_auto(self,message):
        try:
            command=hold_aim();yaw=number(message.yaw,'auto yaw');pitch=number(message.pitch,'auto pitch')
            command.update(yaw_type=1,pitch_type=1,yaw=math.atan2(math.sin(yaw),math.cos(yaw)),pitch=pitch,
                           fire=int(message.shoot and message.control),permit=1)
            entry=self.checked('auto',message.timestamp.sec*1_000_000_000+message.timestamp.nanosec,command)
            entry['control']=bool(message.control);self.auto=entry
        except ProtocolError as error:self.reject(error)

    def on_scan(self,message):
        try:
            if message.header.frame_id not in ('','rmsim_world'):raise ProtocolError('scan requires rmsim_world or the upstream empty frame')
            if message.yaw_type not in (1,2) or message.pitch_type not in (1,2):raise ProtocolError('unsupported GimbalCmd mode')
            c=hold_aim();p=message.position;v=message.velocity
            # The pinned Hub's scan pitch is up-positive. Wire, rig and AutoAim pitch are down-positive.
            c.update(yaw_type=message.yaw_type,pitch_type=message.pitch_type,yaw=float(p.yaw),pitch=-float(p.pitch),
                     yaw_rate=float(v.yaw),pitch_rate=-float(v.pitch),yaw_min=float(v.yaw_min_range),yaw_max=float(v.yaw_max_range),
                     pitch_min=-float(v.pitch_max_range),pitch_max=-float(v.pitch_min_range))
            self.scan=self.checked('scan',message.header.stamp.sec*1_000_000_000+message.header.stamp.nanosec,c)
            self.legacy_frame=not message.header.frame_id
        except ProtocolError as error:
            self.error=str(error);self.rejected+=1;self.scan=None

    def fresh(self,entry,max_age_ns=200_000_000):
        return (entry is not None and time.monotonic()-entry['received']<=max_age_ns*1e-9
                and -50_000_000<=self.node.state['sim_time_ns']-entry['stamp']<=max_age_ns)

    def poll(self):
        try:self.ready()
        except ProtocolError:
            self.auto_holding=False
            if self.node.state is None or not self.node.transport.connected:
                self.identity=None;self.permission=False;self.auto=None;self.scan=None;self.sent=None
            return
        state=self.node.state
        match=state.get('drive', {}).get('match_autonomy') and 'match_fire' in state['aim']
        auto_ready=(self.permission and self.fresh(self.auto,MATCH_TRACK_MEMORY_NS if match else 200_000_000)
                    and self.auto['control'])
        self.auto_holding=bool(match and auto_ready and not self.fresh(self.auto,MATCH_FIRE_OBSERVATION_NS))
        scan_ready=self.fresh(self.scan)
        nav=(self.navigation.poll(auto_ready,scan_ready=scan_ready,measured_motion=match)
             if self.navigation else None)
        if auto_ready:source='auto';entry=self.auto
        elif nav:source='navigation';entry=nav
        elif scan_ready:source='scan';entry=self.scan
        else:source='hold';entry=None
        token=(source,entry['serial'] if entry else 0,self.permission,self.auto_holding)
        self.source=source
        if token==self.sent:return
        command=dict(entry['command']) if entry else hold_aim()
        stamp=entry['stamp'] if entry else self.node.state['sim_time_ns']
        if self.auto_holding:
            # Renew only the already-validated position; preserve the observation stamp in self.auto.
            stamp=state['sim_time_ns']
        if source!='auto' or self.auto_holding:command['fire']=0;command['permit']=0
        seq=self.node.sequence+1
        try:
            raw=aim_packet(self.node.state,seq,stamp,command,entry.get('frame_id','rmsim_world') if entry else 'rmsim_world')
            if self.node.transport.queue(raw):self.node.sequence=seq;self.node.forwarded+=1;self.sent=token
        except ProtocolError as error:self.reject(error)

    def diagnostic(self):
        return dict(source=self.source,permission=self.permission,auto_holding=self.auto_holding,
                    rejected=self.rejected,error=self.error,
                    scan_empty_frame_means_world=self.legacy_frame,navigation_follow=self.navigation.report if self.navigation else None)
