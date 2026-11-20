"""ROS publishers/subscriber for real local simulator packets; never synthesize observations."""
import array
import json
import time

import rclpy
from rclpy.clock import Clock, ClockType
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy, HistoryPolicy
from geometry_msgs.msg import PoseStamped, TransformStamped, TwistStamped
from rosgraph_msgs.msg import Clock as ClockMessage
from sensor_msgs.msg import Image, CameraInfo, JointState
from std_msgs.msg import String
from tf2_msgs.msg import TFMessage

from .protocol import ProtocolError, command_packet, identifier, intrinsics, validate_state
from .transport import LoopbackClient


def stamp(message, nanoseconds):
    message.sec = nanoseconds // 1_000_000_000
    message.nanosec = nanoseconds % 1_000_000_000


def set_pose(message, source):
    message.position.x, message.position.y, message.position.z = map(float, source['position_m'])
    message.orientation.x, message.orientation.y, message.orientation.z, message.orientation.w = map(float, source['rotation_xyzw'])


class BridgeNode(Node):
    def __init__(self):
        super().__init__('rmsim_bridge')
        self.robot_id = identifier(self.declare_parameter('robot_id', 'aim_sentry').value)
        self.transport = LoopbackClient(self.declare_parameter('port', 9451).value)
        sensor = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=2,
                            reliability=ReliabilityPolicy.BEST_EFFORT, durability=DurabilityPolicy.VOLATILE)
        control = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=1,
                             reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.VOLATILE)
        prefix = '/' + self.robot_id
        self.clock_pub = self.create_publisher(ClockMessage, '/clock', control)
        self.image_pub = self.create_publisher(Image, prefix + '/camera/image_raw', sensor)
        self.info_pub = self.create_publisher(CameraInfo, prefix + '/camera/camera_info', sensor)
        self.tf_pub = self.create_publisher(TFMessage, '/tf', QoSProfile(depth=100))
        # Truth is isolated from the robot TF tree and named explicitly for evaluation.
        self.truth_pub = self.create_publisher(PoseStamped, '/rmsim/evaluation' + prefix + '/ground_truth', sensor)
        self.state_pub = self.create_publisher(String, '/rmsim' + prefix + '/state', control)
        self.joints_pub=self.create_publisher(JointState,prefix+'/gimbal/joint_states',sensor)
        self.shooter_pub=self.create_publisher(String,prefix+'/training_state',control)
        self.training_eval_pub=self.create_publisher(String,'/rmsim/evaluation'+prefix+'/training',control)
        self.command_sub = self.create_subscription(TwistStamped, prefix + '/cmd_vel', self.on_command, control)
        self.state = None
        self.received_at = 0.
        self.sequence = 0
        self.forwarded = self.rejected = self.images = self.states = 0
        self.command_error = ''
        self.last_transport_error = ''
        self.last_diagnostic = 0.
        self.generation = 0
        from .imu import ImuPublisher
        self.imu=ImuPublisher(self)
        from .lidar import LidarPublisher
        self.lidar=LidarPublisher(self)
        backend=self.declare_parameter('control_backend','velocity').value
        if backend not in ('velocity','lk'):raise ValueError('control_backend must be velocity or lk')
        self.lk=None
        if backend=='lk':
            from .lk_adapter import LkMotorAdapter
            self.lk=LkMotorAdapter(self)
        self.aim=None
        if self.declare_parameter('aim_control',False).value:
            from .aim_adapter import AimAdapter
            self.aim=AimAdapter(self)
        # use_sim_time may be true for algorithm nodes. This I/O timer must keep running
        # before the first /clock message and through pauses or simulator restarts.
        self.wall_clock = Clock(clock_type=ClockType.STEADY_TIME)
        self.timer = self.create_timer(.001 if self.lk is not None else .01, self.poll, clock=self.wall_clock)
        self.get_logger().info('RMSim bridge listening to local simulator state; waiting for TCP 127.0.0.1:%d' % self.transport.port)

    def on_command(self, message):
        try:
            if self.lk is not None:raise ProtocolError('LK mode accepts original ChassisControl, not cmd_vel')
            if not self.transport.connected or self.state is None or time.monotonic()-self.received_at > .25:
                raise ProtocolError('no fresh simulator state')
            stamp_ns = int(message.header.stamp.sec)*1_000_000_000 + int(message.header.stamp.nanosec)
            t = message.twist
            next_sequence = self.sequence + 1
            raw = command_packet(self.state, next_sequence, stamp_ns, message.header.frame_id,
                                 [t.linear.x, t.linear.y, t.linear.z, t.angular.x, t.angular.y, t.angular.z], self.robot_id)
            if not self.transport.queue(raw):
                raise ProtocolError('simulator disconnected or command queue stalled')
            self.sequence = next_sequence
            self.forwarded += 1
            self.command_error = ''
            if self.aim is not None and self.aim.navigation is not None:self.aim.navigation.observe_velocity(message)
        except ProtocolError as error:
            self.rejected += 1
            self.command_error = str(error)

    def poll(self):
        packets = self.transport.poll()
        if self.generation != self.transport.connection_number:
            self.state = None
            self.generation = self.transport.connection_number
        if not self.transport.connected:
            self.state = None
        for state, pixels in packets:
            try:
                validate_state(state, pixels, self.robot_id)
                previous = self.state
                same_epoch = previous and (previous['session'], previous['epoch']) == (state['session'], state['epoch'])
                if same_epoch and (state['tick'] < previous['tick'] or state['sim_time_ns'] < previous['sim_time_ns']):
                    raise ProtocolError('state time/tick moved backward within an epoch')
                self.sequence = max(self.sequence if same_epoch else 0, state['command']['accepted_seq'], 0)
                self.state = state
                self.received_at = time.monotonic()
                if self.lk is not None:self.lk.observe(state)
                if self.aim is not None:self.aim.observe(state)
                self.publish_state(state, pixels)
            except (ProtocolError, TypeError, ValueError) as error:
                self.transport.close(str(error))
                self.state = None
                break
        if self.transport.last_error != self.last_transport_error:
            self.last_transport_error = self.transport.last_error
            if self.last_transport_error:
                self.get_logger().warning(self.last_transport_error)
        if self.lk is not None:self.lk.poll()
        if self.aim is not None:self.aim.poll()
        if time.monotonic()-self.last_diagnostic >= 1:
            self.publish_diagnostic()

    def publish_state(self, state, pixels):
        clock = ClockMessage()
        stamp(clock.clock, state['sim_time_ns'])
        self.clock_pub.publish(clock)
        if state.get('sensor_mounts'):
            transforms=[]
            for sample in state['sensor_mounts']:
                tf=TransformStamped();stamp(tf.header.stamp,sample['stamp_ns'])
                tf.header.frame_id=sample['parent_frame_id'];tf.child_frame_id=sample['frame_id'];local=sample['pose_in_parent']
                tf.transform.translation.x,tf.transform.translation.y,tf.transform.translation.z=map(float,local['position_m'])
                tf.transform.rotation.x,tf.transform.rotation.y,tf.transform.rotation.z,tf.transform.rotation.w=map(float,local['rotation_xyzw']);transforms.append(tf)
            self.tf_pub.publish(TFMessage(transforms=transforms))
        self.imu.observe(state)
        self.lidar.observe(state,pixels)
        if state['v']==2:pixels=pixels[:state['camera_bytes']]
        self.states += 1
        truth = PoseStamped()
        stamp(truth.header.stamp, state['sim_time_ns'])
        truth.header.frame_id = 'rmsim_world'
        set_pose(truth.pose, state['pose'])
        self.truth_pub.publish(truth)
        camera = state['camera']
        transform = TransformStamped()
        stamp(transform.header.stamp, camera['stamp_ns'])
        transform.header.frame_id = self.robot_id + '/base_link'
        transform.child_frame_id = camera['frame_id']
        local = camera['pose_in_base']
        transform.transform.translation.x, transform.transform.translation.y, transform.transform.translation.z = map(float, local['position_m'])
        q = transform.transform.rotation
        q.x, q.y, q.z, q.w = map(float, local['rotation_xyzw'])
        self.tf_pub.publish(TFMessage(transforms=[transform]))
        info = CameraInfo()
        stamp(info.header.stamp, camera['stamp_ns'])
        info.header.frame_id = camera['frame_id']
        info.width, info.height = camera['width'], camera['height']
        info.distortion_model = 'plumb_bob'
        info.d = [0.] * 5
        info.k = intrinsics(info.width, info.height, camera['hfov_rad'])
        info.r = [1., 0., 0., 0., 1., 0., 0., 0., 1.]
        k = info.k
        info.p = [k[0], 0., k[2], 0., 0., k[4], k[5], 0., 0., 0., 1., 0.]
        self.info_pub.publish(info)
        if pixels:
            image = Image()
            stamp(image.header.stamp, camera['stamp_ns'])
            image.header.frame_id = camera['frame_id']
            image.width, image.height = info.width, info.height
            image.encoding, image.is_bigendian, image.step = 'rgb8', 0, image.width*3
            # The generated ROS setter validates Python sequences per element;
            # byte storage is already range-checked by its type and the wire length gate.
            image.data = array.array('B', pixels)
            self.image_pub.publish(image)
            self.images += 1
        aiming=state.get('aim')
        if aiming:
            if aiming['yaw_joint'] and aiming['pitch_joint']:
                joints=JointState();stamp(joints.header.stamp,aiming['stamp_ns']);joints.header.frame_id=self.robot_id+'/base_link'
                joints.name=[aiming['yaw_joint'],aiming['pitch_joint']]
                joints.position=[float(aiming['yaw_rad']),float(aiming['pitch_rad'])]
                joints.velocity=[float(aiming['yaw_velocity_rad_s']),float(aiming['pitch_velocity_rad_s'])]
                self.joints_pub.publish(joints)
            self.shooter_pub.publish(String(data=json.dumps(aiming['shooter'],allow_nan=False,separators=(',',':'))))
        if state.get('training_evaluation'):
            self.training_eval_pub.publish(String(data=json.dumps(state['training_evaluation'],allow_nan=False,separators=(',',':'))))
        self.publish_diagnostic()

    def publish_diagnostic(self):
        diagnostic = dict(connected=self.transport.connected, robot_id=self.robot_id,
                          state_fresh=self.state is not None and time.monotonic()-self.received_at <= .25,
                          received_states=self.states, published_images=self.images,
                          forwarded_commands=self.forwarded, rejected_commands=self.rejected,
                          command_error=self.command_error, transport_error=self.transport.last_error,
                          evaluation_truth_is_not_localization=True)
        if self.state is not None:
            diagnostic.update(session=self.state['session'], epoch=self.state['epoch'], tick=self.state['tick'],
                              sim_time_ns=self.state['sim_time_ns'], command=self.state['command'])
            diagnostic.update(simulation_rate=self.state.get('simulation_rate'),fixed_step_s=self.state.get('fixed_step_s'))
            for key in ('clock_source', 'physics_backend', 'dynamics_step_s', 'rendering', 'drive'):
                if key in self.state:
                    diagnostic[key] = self.state[key]
            if 'map' in self.state:diagnostic['map']=self.state['map']
            camera=self.state['camera']
            diagnostic['camera_config']={key:camera.get(key) for key in ('width','height','hfov_rad','nominal_hz','observer_hfov_rad','capture_enabled')}
            if self.lk is not None:diagnostic['motors']=self.state.get('motors')
            if 'aim' in self.state:diagnostic['aim']=self.state['aim']
            if 'imu' in self.state:
                data=self.state['imu'];diagnostic['imu']={key:data[key] for key in ('model','profile_md5','calibrated','gravity_z_mps2','profiles','stats')}
                diagnostic['imu']['published_samples']=self.imu.count
            if 'lidar' in self.state:
                diagnostic['lidar']={key:value for key,value in self.state['lidar'].items() if key!='scans'}
                diagnostic['lidar'].update(published_scans=self.lidar.count,published_points=self.lidar.points)
        if self.lk is not None:diagnostic['lk_adapter']=self.lk.diagnostic()
        if self.aim is not None:diagnostic['aim_adapter']=self.aim.diagnostic()
        self.state_pub.publish(String(data=json.dumps(diagnostic, separators=(',', ':'))))
        self.last_diagnostic = time.monotonic()

    def destroy_node(self):
        self.transport.close()
        return super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = BridgeNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
