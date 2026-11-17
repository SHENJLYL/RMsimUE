"""Compare the bulk-publish CDR bytes to the original generated Livox serializer."""
from pathlib import Path
import os,struct,json,time
root=Path(__file__).resolve().parents[2]
_dll=os.add_dll_directory(str(root/'ros2_ws/install_lio/livox_ros_driver2/bin'))
from rclpy.serialization import serialize_message,deserialize_message
from livox_ros_driver2.msg import CustomMsg,CustomPoint
from rmsim_bridge.lidar import livox_cdr
checks=[]
for count in [0,1,2,31,20000]:
    message=CustomMsg();message.header.frame_id='aim_sentry/lidar_link';message.header.stamp.sec=12;message.header.stamp.nanosec=345
    message.timebase=12_000_000_345;message.point_num=count
    raw=bytearray();points=[]
    for i in range(count):
        p=CustomPoint();p.offset_time=i*1000;p.x=float(i*.01);p.y=-.25;p.z=1.5;p.reflectivity=100;p.tag=0;p.line=i%4;points.append(p)
        raw.extend(struct.pack('<IfffBBBB',p.offset_time,p.x,p.y,p.z,p.reflectivity,p.tag,p.line,0))
    started=time.perf_counter();bulk=livox_cdr(message,bytes(raw));elapsed=time.perf_counter()-started
    message.points=points;expected=serialize_message(message)
    # CDR padding bytes are unspecified; compare both through the original native decoder.
    decoded=deserialize_message(bulk,CustomMsg);reference=deserialize_message(expected,CustomMsg)
    assert decoded==reference,(count,len(bulk),len(expected))
    assert len(decoded.points)==count and decoded.point_num==count
    checks.append(dict(points=count,bytes=len(bulk),bulk_seconds=elapsed,semantic_equality=True,padding_bytes_not_compared=True,passed=True))
out=root/'artifacts/sensors/lidar/livox-serialization.json';out.write_text(json.dumps(dict(passed=True,checks=checks),indent=2)+'\n',encoding='utf-8')
print('Original Livox CDR decoded equality passed',len(checks),'sizes; 20k points',checks[-1]['bulk_seconds'],'seconds')
