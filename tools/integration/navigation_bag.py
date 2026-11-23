"""Record the local navigation graph in a native rosbag2, then read it back."""
from collections import Counter
import os
import json
from pathlib import Path
import sqlite3
import struct
import sys
import time
from datetime import datetime, timezone
import re

import rosbag2_py as bag
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy, HistoryPolicy
from rosidl_runtime_py.utilities import get_message
import yaml

from navigation_console_config import atomic_json


def required_topics(names):
    lidar = next((name for name in names if name.startswith('/aim_sentry/') and name.endswith('/livox')), None)
    if lidar is None:
        return set()
    return {lidar, lidar[:-5] + 'points', '/aim_sentry/mid360_imu/data_raw', '/clock', '/tf', '/tf_static',
            '/odometry', '/registered_scan', '/rmsim/aim_sentry/state'}


def check_recording(output):
    files = list((output / 'rosbag').glob('*.db3'))
    if not files:
        return False
    with sqlite3.connect(files[0].as_uri() + '?mode=ro', uri=True, timeout=.1) as db:
        topics = dict(db.execute('select name,id from topics'))
        required = required_topics(topics)
        if not required or not required <= topics.keys():
            return False
        for name in required:
            if db.execute('select 1 from messages where topic_id=? limit 1', (topics[name],)).fetchone() is None:
                return False
    return atomic_json(output/'bag-ready.json', {'topics_with_data': sorted(required)})


def review(output):
    reader = bag.SequentialReader()
    reader.open(bag.StorageOptions(uri=str(output/'rosbag'), storage_id='sqlite3'), bag.ConverterOptions('', ''))
    metadata = reader.get_metadata()
    counts = {t.topic_metadata.name: t.message_count for t in metadata.topics_with_message_count}
    topics = required_topics(counts)
    if not topics:
        raise ValueError('Raw Livox stream missing from recorded graph')
    if not topics <= counts.keys():
        raise ValueError('Required streams missing from bag: ' + str(topics - counts.keys()))
    reader.set_filter(bag.StorageFilter(topics=sorted(topics | ({'/rmsim/aim_sentry/localization'} & counts.keys()))))
    decoded = Counter()
    stamps = {}
    gaps = {}
    sessions = set()
    lio_starts = 0
    stall_count = 0
    warning_count = None
    stall_events = {}
    lidar_stamps = {}
    publisher = {}
    while reader.has_next():
        topic, raw, receipt = reader.read_next()
        decoded[topic] += 1
        endian = '<' if raw[1] == 1 else '>'
        if topic in ('/rmsim/aim_sentry/state','/rmsim/aim_sentry/localization'):
            n, = struct.unpack_from(endian+'I',raw,4)
            state = json.loads(raw[8:8+n-1])
            if state.get('session'): sessions.add((state['session'],state['epoch']))
            lio_starts = max(lio_starts,state.get('starts',0))
            if 'game_tick_stalls' in state:
                stall_count = max(stall_count,state['game_tick_stalls'])
                if state['game_tick_stalls']:
                    stall_events.setdefault((state['session'],state['game_tick_stalls']),
                        {'receipt_ns':receipt,'sim_ns':state['sim_time_ns'],'game_tick_gap_ms':state.get('last_game_tick_gap_ms')})
            if 'mujoco_warning_count' in state: warning_count = max(warning_count or 0,state['mujoco_warning_count'])
            if 'lidar' in state:
                publisher = {k:state['lidar'].get(k) for k in ('published_scans','frame_hz','incomplete_scan_drops','skipped_scan_windows','dropped_scans')}
        elif topic not in ('/tf','/tf_static'):
            sec, ns = struct.unpack_from(endian+'iI',raw,4)
            stamp = sec*10**9 + ns
            if topic.endswith(('/livox','/points')):
                lidar_stamps.setdefault(topic,set()).add(stamp)
            if topic in stamps:
                before, wall = stamps[topic]
                item = gaps.setdefault(topic,{'max_header_gap_s':0.,'max_receipt_gap_s':0.,'backward_stamps':0})
                item['max_header_gap_s'] = max(item['max_header_gap_s'],(stamp-before)*1e-9)
                item['max_receipt_gap_s'] = max(item['max_receipt_gap_s'],(receipt-wall)*1e-9)
                item['backward_stamps'] += stamp < before
            stamps[topic] = stamp, receipt
    passed = all(decoded[t] > 0 and decoded[t] == counts[t] for t in topics)
    paired = list(lidar_stamps.values())
    integrity = {}
    if len(paired)==2 and all(paired):
        common = sorted(paired[0]&paired[1])
        hz = publisher.get('frame_hz') or 10.0
        interior_missing = sum(max(0,round((b-a)*1e-9*hz)-1) for a,b in zip(common,common[1:]))
        integrity = {'common_timestamps':len(common),'unpaired_timestamps':len(paired[0]^paired[1]),
                     'missing_paired_scans_between_first_and_last':interior_missing,'publisher':publisher}
    ready_file = output/'drive-ready.json'
    hitches = []
    ready_wall = None
    if ready_file.exists():
        start = json.loads(ready_file.read_text(encoding='utf-8'))
        if 'monotonic_s' in start:
            ready_wall = time.time() - (time.monotonic() - start['monotonic_s'])
            for line in (output/'runtime.log').read_text(encoding='utf-8', errors='replace').splitlines():
                match = re.search(r'\[([\d.]+-[\d.]+:\d+)\].*SCENE_DRAW_HITCH gap_ms=([\d.]+)', line)
                if not match: continue
                at = datetime.strptime(match[1],'%Y.%m.%d-%H.%M.%S:%f').replace(tzinfo=timezone.utc).timestamp()
                if at >= ready_wall:
                    hitches.append({'since_rviz_s':round(at-ready_wall,3),'draw_gap_ms':float(match[2])})
    alerts = []
    if stall_count: alerts.append('game_tick_stall')
    if lio_starts > 1: alerts.append('localizer_restarted')
    if warning_count: alerts.append('mujoco_warning')
    if len(sessions)>1: alerts.append('sensor_session_changed')
    if hitches: alerts.append('scene_draw_hitch')
    if integrity.get('unpaired_timestamps') or integrity.get('missing_paired_scans_between_first_and_last'):
        alerts.append('raw_lidar_gap')
    summary = {'verified':passed,'storage':'sqlite3','message_count':metadata.message_count,'lidar_integrity':integrity,
               'alerts':alerts, 'render_gaps_after_rviz_ready':hitches,
               'topic_counts':counts,'readback_counts':dict(decoded),'sensor_timing':gaps,
               'bridge_sessions':sorted(sessions),'point_lio_starts':lio_starts,
               'game_tick_stalls':stall_count,'stall_events':list(stall_events.values()),'mujoco_warning_count':warning_count}
    if not atomic_json(output/'bag-review.json',summary) or not passed:
        raise RuntimeError('Incomplete rosbag2 readback')


def main(output):
    # ROS dynamically loads the Livox Fast DDS type support on subscription.
    # The native Windows loader and Python extensions use separate search paths.
    if os.name == 'nt':
        from ament_index_python.packages import get_package_prefix
        livox_bin = Path(get_package_prefix('livox_ros_driver2')) / 'bin'
        os.environ['PATH'] = str(livox_bin) + os.pathsep + os.environ['PATH']
        livox_dll_directory = os.add_dll_directory(str(livox_bin))
    rclpy.init()
    node = Node('rmsim_navigation_recorder',enable_rosout=False)
    writer = bag.SequentialWriter()
    storage = bag.StorageOptions(uri=str(output/'rosbag'),storage_id='sqlite3')
    storage.max_cache_size = 16 * 1024 * 1024
    writer.open(storage, bag.ConverterOptions('cdr','cdr'))
    subscriptions = {}
    next_discovery = next_check = 0.
    ready = False

    def subscribe(name, typename, reliable, latched):
        cls = get_message(typename)
        qos = QoSProfile(depth=128 if name.endswith('/data_raw') else 32,
                         history=HistoryPolicy.KEEP_LAST,
                         reliability=ReliabilityPolicy.RELIABLE if reliable else ReliabilityPolicy.BEST_EFFORT,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL if latched else DurabilityPolicy.VOLATILE)
        infinite = {'sec':9223372036,'nsec':854775807}
        offer = yaml.safe_dump([{'history':1,'depth':qos.depth,'reliability':int(qos.reliability),
            'durability':int(qos.durability),'deadline':infinite,'lifespan':infinite,'liveliness':1,
            'liveliness_lease_duration':infinite,'avoid_ros_namespace_conventions':False}],sort_keys=False)
        writer.create_topic(bag.TopicMetadata(name=name,type=typename,serialization_format='cdr',offered_qos_profiles=offer))
        subscriptions[name] = node.create_subscription(cls,name,
            lambda raw, name=name: writer.write(name,raw,time.time_ns()),qos,raw=True)

    try:
        # Raw CDR subscriptions avoid per-point Python objects and retain original sensor stamps.
        for name,typename,latched in [('/tf_static','tf2_msgs/msg/TFMessage',True),('/tf','tf2_msgs/msg/TFMessage',False),
            ('/clock','rosgraph_msgs/msg/Clock',False),('/aim_sentry/lidar/livox','livox_ros_driver2/msg/CustomMsg',False),
            ('/aim_sentry/lidar/points','sensor_msgs/msg/PointCloud2',False),('/aim_sentry/mid360_imu/data_raw','sensor_msgs/msg/Imu',False),
            ('/registered_scan','sensor_msgs/msg/PointCloud2',False),('/odometry','nav_msgs/msg/Odometry',False)]:
            subscribe(name,typename,latched or name.startswith('/aim_sentry/'),latched)
        while rclpy.ok() and not (output/'bag-stop.request').exists():
            now = time.monotonic()
            if now >= next_discovery:
                for name,types in node.get_topic_names_and_types():
                    if name in subscriptions or len(types)!=1: continue
                    pubs = node.get_publishers_info_by_topic(name)
                    if not pubs: continue
                    subscribe(name,types[0],all(p.qos_profile.reliability==ReliabilityPolicy.RELIABLE for p in pubs),
                              all(p.qos_profile.durability==DurabilityPolicy.TRANSIENT_LOCAL for p in pubs))
                next_discovery = now + .5
            if not ready and now >= next_check:
                try: ready = check_recording(output)
                except sqlite3.Error: pass
                next_check = now + .5
            rclpy.spin_once(node,timeout_sec=.015)
    finally:
        writer.close()
        node.destroy_node()
        rclpy.shutdown()
    review(output)


if __name__ == '__main__':
    main(Path(sys.argv[1]).resolve())
