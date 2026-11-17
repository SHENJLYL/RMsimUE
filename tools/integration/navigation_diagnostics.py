"""Bounded, read-only telemetry for the real navigation pipeline and reconnects."""
import json
import os
import time
from pathlib import Path
from collections import deque
from nav_msgs.msg import Odometry
from sensor_msgs.msg import PointCloud2, LaserScan
from tf2_msgs.msg import TFMessage
from rclpy.clock import Clock, ClockType
from rclpy.qos import qos_profile_sensor_data


def stamp_ns(stamp):
    return stamp.sec * 1_000_000_000 + stamp.nanosec


class NavigationDiagnostics:
    def __init__(self, node, directory):
        self.node = node
        self.output = Path(directory) / 'pipeline-diagnostics.json'
        self.topics = {}
        self.edges = {}
        self.samples = deque(maxlen=600)
        self.subscriptions = []
        for topic, kind in [('/aft_mapped_to_init', Odometry), ('/lidar_odometry', Odometry),
                            ('/odometry', Odometry), ('/cloud_registered', PointCloud2),
                            ('/registered_scan', PointCloud2), ('/sensor_scan', PointCloud2),
                            ('/terrain_map_ext', PointCloud2), ('/obstacle_scan', LaserScan)]:
            self.subscriptions.append(node.create_subscription(
                kind, topic, lambda message, key=topic: self.observe(key, message), qos_profile_sensor_data))
        self.subscriptions.append(node.create_subscription(TFMessage, '/tf', self.transforms, 100))
        self.timer = node.create_timer(1.0, self.sample, clock=Clock(clock_type=ClockType.STEADY_TIME))

    def observe(self, topic, message):
        stamp = stamp_ns(message.header.stamp)
        previous = self.topics.get(topic, {})
        self.topics[topic] = dict(count=previous.get('count', 0)+1, stamp_ns=stamp,
                                 frame=message.header.frame_id, wall=time.monotonic(),
                                 regressions=previous.get('regressions', 0)+int(stamp < previous.get('stamp_ns', 0)))

    def transforms(self, message):
        for transform in message.transforms:
            if transform.header.frame_id.startswith('aim_sentry/') and 'camera' not in transform.child_frame_id:
                key=transform.header.frame_id+'->'+transform.child_frame_id
                self.edges[key]={'stamp_ns':stamp_ns(transform.header.stamp), 'wall':time.monotonic()}

    def sample(self):
        now=time.monotonic()
        def rows(values):
            return {key:{**{k:v for k,v in value.items() if k!='wall'},
                         'age_wall_s':round(now-value['wall'],3)} for key,value in values.items()}
        self.samples.append(dict(wall=now, clock_ns=self.node.clock,
                                 navigation=self.node.nav, topics=rows(self.topics), tf=rows(self.edges)))
        report={'scope':'read-only observations; no control or localization input',
                'ros_domain_id':os.environ.get('ROS_DOMAIN_ID'), 'samples':list(self.samples)}
        self.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')

    def save(self):
        self.sample()
