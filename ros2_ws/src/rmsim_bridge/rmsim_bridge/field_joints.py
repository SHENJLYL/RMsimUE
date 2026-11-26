"""Read simulator field joint state without changing the vehicle TF tree."""
from dataclasses import dataclass
from math import cos, isfinite, sin
from pathlib import Path
import xml.etree.ElementTree as ET


BASE_JOINT_NAMES = tuple(
    f'rmuc2027_{side}_base_panel_{panel}_{kind}'
    for side in ('red', 'blue')
    for panel in range(1, 4)
    for kind in ('slide', 'hinge')
)
FIELD_FRAME = 'rmuc2027_field'


def base_joint_positions(referee):
    field = referee.get('field_devices')
    if not isinstance(field, dict) or not field.get('base_armor_meshes_bound'):
        return None
    raw = field.get('base_armor_joint_positions')
    if not isinstance(raw, list) or len(raw) != len(BASE_JOINT_NAMES):
        return None
    if not all(type(value) in (float, int) and isfinite(value) for value in raw):
        return None
    return [float(value) for value in raw]


@dataclass(frozen=True)
class FieldJoint:
    name: str
    kind: str
    parent: str
    child: str
    origin: tuple
    axis: tuple

    def pose(self, position=0.):
        if self.kind == 'prismatic':
            xyz = tuple(origin + position * axis for origin, axis in zip(self.origin, self.axis))
            return xyz, (0., 0., 0., 1.)
        if self.kind == 'revolute':
            return self.origin, (*[axis * sin(position / 2) for axis in self.axis], cos(position / 2))
        return self.origin, (0., 0., 0., 1.)


def description_joints(description):
    root = ET.fromstring(description)
    motions, fixed = {}, []
    for joint in root.findall('joint'):
        origin = joint.find('origin')
        axis_node = joint.find('axis')
        xyz = tuple(map(float, origin.get('xyz').split()))
        if tuple(map(float, origin.get('rpy').split())) != (0., 0., 0.):
            raise ValueError('RMUC field joint origins must use the arena axes')
        axis = tuple(map(float, axis_node.get('xyz').split())) if axis_node is not None else (0., 0., 0.)
        item = FieldJoint(joint.get('name'), joint.get('type'), joint.find('parent').get('link'),
                          joint.find('child').get('link'), xyz, axis)
        if item.kind == 'fixed':
            fixed.append(item)
        elif item.kind in ('prismatic', 'revolute'):
            motions[item.name] = item
        else:
            raise ValueError(f'Unexpected RMUC field joint type {item.kind}')
    if tuple(motions) != BASE_JOINT_NAMES:
        raise ValueError('URDF and referee joint order differ')
    return fixed, motions


class FieldDescriptionPublisher:
    """Publish the field description and its own TF tree using the existing ROS runtime."""

    def __init__(self, node, qos):
        from ament_index_python.packages import get_package_share_directory
        from rclpy.qos import DurabilityPolicy, QoSProfile
        from sensor_msgs.msg import JointState
        from std_msgs.msg import String
        from tf2_msgs.msg import TFMessage

        self.node = node
        share = Path(get_package_share_directory('rmsim_bridge'))
        self.urdf = (share / 'description' / 'rmuc2027_field.urdf').read_text(encoding='utf-8')
        self.fixed, self.motions = description_joints(self.urdf)
        durable = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.description_pub = node.create_publisher(String, '/rmsim/field/robot_description', durable)
        self.static_pub = node.create_publisher(TFMessage, '/tf_static', durable)
        self.joints_pub = node.create_publisher(JointState, '/rmsim/field/joint_states', qos)
        self.last_ns = None

    @staticmethod
    def transform(joint, step, nanoseconds):
        from geometry_msgs.msg import TransformStamped

        msg = TransformStamped()
        msg.header.stamp.sec, msg.header.stamp.nanosec = divmod(nanoseconds, 1_000_000_000)
        msg.header.frame_id = joint.parent
        msg.child_frame_id = joint.child
        xyz, quaternion = joint.pose(step)
        msg.transform.translation.x, msg.transform.translation.y, msg.transform.translation.z = xyz
        msg.transform.rotation.x, msg.transform.rotation.y, msg.transform.rotation.z, msg.transform.rotation.w = quaternion
        return msg

    def observe(self, nanoseconds, positions):
        from sensor_msgs.msg import JointState
        from std_msgs.msg import String
        from tf2_msgs.msg import TFMessage

        if self.last_ns is None:
            self.description_pub.publish(String(data=self.urdf))
            self.static_pub.publish(TFMessage(transforms=[self.transform(joint, 0., 0) for joint in self.fixed]))
        elif 0 <= nanoseconds - self.last_ns < 50_000_000:
            return
        self.last_ns = nanoseconds
        state = JointState()
        state.header.stamp.sec, state.header.stamp.nanosec = divmod(nanoseconds, 1_000_000_000)
        state.header.frame_id = FIELD_FRAME
        state.name = list(BASE_JOINT_NAMES)
        state.position = positions
        self.joints_pub.publish(state)
        self.node.tf_pub.publish(TFMessage(transforms=[
            self.transform(self.motions[name], step, nanoseconds) for name, step in zip(state.name, positions)
        ]))
