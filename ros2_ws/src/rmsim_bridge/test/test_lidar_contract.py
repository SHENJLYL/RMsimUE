import copy,struct,unittest
from test_protocol import state,packet
from rmsim_bridge.protocol import validate_state,ProtocolError,PacketReader

def frame():
    s=state();s.update(v=2,camera_bytes=12)
    s['lidar']=dict(id='lidar',calibrated=False,model='test rays',profile_md5='test',frame_id='aim_sentry/lidar_link',parent_frame_id='aim_sentry/base_link',
        pose_in_base=dict(position_m=[0,0,.46],rotation_xyzw=[0,0,0,1]),rays_per_second=200000,frame_hz=10,
        min_range_m=.1,max_range_m=40,elevation_min_rad=-.12,elevation_max_rad=.9,
        traced_rays=20,returns=2,missed_rays=0,dropped_scans=0,history_resets=0,max_physics_step_s=.001,
        scans=[dict(seq=1,start_ns=900000000,end_ns=999000000,point_count=2,byte_offset=12,byte_length=40)])
    payload=bytes(range(12))+struct.pack('<IfffBBBB',1000000,1.,0.,.1,100,0,0,0)+struct.pack('<IfffBBBB',99000000,2.,0.,.2,100,0,1,0)
    return s,payload

class LidarWireTests(unittest.TestCase):
    def test_rgb_and_lidar_fragmented_packet(self):
        s,payload=frame();decoder=PacketReader();received=[]
        for byte in packet(s,payload):received.extend(decoder.feed(bytes([byte])))
        self.assertEqual(len(received),1);validate_state(*received[0],'aim_sentry')
        self.assertEqual(received[0][1][:12],bytes(range(12)))
    def test_layout_and_time_bounds(self):
        s,payload=frame()
        for change in ({'byte_offset':0},{'byte_length':20},{'point_count':65537},{'end_ns':901000000},{'seq':0}):
            bad=copy.deepcopy(s);bad['lidar']['scans'][0].update(change)
            with self.assertRaises(ProtocolError):validate_state(bad,payload,'aim_sentry')
        with self.assertRaises(ProtocolError):validate_state(s,payload+b'\0','aim_sentry')
    def test_nonfinite_and_unordered_returns(self):
        s,payload=frame()
        for values in [(1000000,float('nan'),0.,0.,100,0,1,0),(0,1.,0.,0.,100,0,1,0),(1000000,1.,0.,0.,100,0,4,0)]:
            bad=payload[:32]+struct.pack('<IfffBBBB',*values)
            with self.assertRaises(ProtocolError):validate_state(s,bad,'aim_sentry')
