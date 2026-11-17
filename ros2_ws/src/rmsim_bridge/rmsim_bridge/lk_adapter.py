"""Original LK ROS messages on isolated simulator topics; never connect to EtherCAT."""
import math
import time
from rclpy.qos import QoSProfile, ReliabilityPolicy
from .control_dlls import load_control_dlls
load_control_dlls()
from chassis_controllers.msg import ChassisControl
from custom_msgs.msg import ReadLkMotorMulti, WriteLkMotorBroadcastCurrentControl
from .protocol import ProtocolError, integer, motor_packet


class LkMotorAdapter:
    def __init__(self,node):
        self.node=node;self.identity=None;self.intent_at=0.;self.intent_stop=True
        self.currents={'steer':[0]*4,'wheel':[0]*4};self.current_at={'steer':0.,'wheel':0.}
        self.reason='waiting';self.feedback_count=0
        prefix='/rmsim/'+node.robot_id
        latest=QoSProfile(depth=1,reliability=ReliabilityPolicy.BEST_EFFORT)
        self.feedback={name:node.create_publisher(ReadLkMotorMulti,prefix+'/'+name+'/feedback',latest) for name in self.currents}
        self.subs=[node.create_subscription(ChassisControl,prefix+'/chassis_command',self.intent,latest)]
        for name in self.currents:
            self.subs.append(node.create_subscription(WriteLkMotorBroadcastCurrentControl,prefix+'/'+name+'/current_command',
                lambda message,group=name:self.current(group,message),latest))

    def intent(self,message):
        self.intent_stop=bool(message.emergency_stop)
        valid=all(math.isfinite(v) and abs(v)<=20 for v in (message.x_speed,message.y_speed,message.spin_speed))
        if not valid or self.identity is None or not self.node.transport.connected:
            self.intent_at=0.;return
        self.intent_at=time.monotonic()

    def current(self,group,message):
        values=[getattr(message,f'motor{i+1}_cmd') for i in range(4)]
        if any(abs(v)>2000 for v in values):
            self.current_at[group]=0.;return
        self.currents[group]=values;self.current_at[group]=time.monotonic()

    def observe(self,state):
        identity=(state['session'],state['epoch'])
        if identity!=self.identity:
            self.identity=identity;self.intent_at=0.;self.intent_stop=True
            self.current_at={'steer':0.,'wheel':0.}
        motors=state.get('motors',{})
        if motors.get('enabled') is not True or motors.get('order')!='front_left,front_right,back_left,back_right':
            raise ProtocolError('LK adapter requires UE RosMotorControl feedback in FL/FR/BL/BR order')
        for group,publisher in self.feedback.items():
            rows=motors.get(group)
            if not isinstance(rows,list) or len(rows)!=4:raise ProtocolError('invalid LK feedback rows')
            msg=ReadLkMotorMulti();msg.header.frame_id=self.node.robot_id+'/base_link'
            msg.header.stamp.sec=state['sim_time_ns']//1_000_000_000;msg.header.stamp.nanosec=state['sim_time_ns']%1_000_000_000
            for i,row in enumerate(rows):
                for key,source,low,high in [('encoder','encoder',0,32767),('speed','speed_deg_s',-32768,32767),('current','current',-2000,2000)]:
                    value=row.get(source)
                    # UE JSON stores integral fields as JSON numbers, normally decoded as int.
                    if isinstance(value,float) and value.is_integer():value=int(value)
                    setattr(msg,f'motor{i+1}_{key}',integer(value,source,low,high))
                setattr(msg,f'motor{i+1}_online',1)
                # No thermal model;0 is an unsupported temperature reading, not measured ambient.
            publisher.publish(msg)
        self.feedback_count+=1

    def poll(self):
        node=self.node;now=time.monotonic()
        if node.state is None or not node.transport.connected or now-node.received_at>.1:
            self.identity=None;self.intent_at=0.;self.reason='no_fresh_feedback';return
        if self.intent_stop:self.reason='emergency_stop'
        elif now-self.intent_at>.25:self.reason='chassis_command_timeout'
        elif any(now-self.current_at[k]>.1 for k in self.current_at):self.reason='motor_controller_timeout'
        else:self.reason='current_control'
        brake=self.reason!='current_control';sequence=node.sequence+1
        raw=motor_packet(node.state,sequence,self.currents['steer'],self.currents['wheel'],brake,node.robot_id)
        if node.transport.queue(raw):node.sequence=sequence;node.forwarded+=1

    def diagnostic(self):
        return dict(status=self.reason,feedback_samples=self.feedback_count,
                    current_message_age_s={k:time.monotonic()-v for k,v in self.current_at.items()})
