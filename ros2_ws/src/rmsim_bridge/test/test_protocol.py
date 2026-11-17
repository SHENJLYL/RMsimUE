"""Wire and actual loopback checks; these are intentionally not ROS integration tests."""
import copy
import json
import math
import socket
import struct
import time
import unittest

from rmsim_bridge.protocol import PacketReader, ProtocolError, command_packet, motor_packet, aim_packet, hold_aim, intrinsics, validate_state, MAX_IMAGE, MAX_JSON
from rmsim_bridge.transport import LoopbackClient


def state():
    return dict(v=1, kind='state', session='run-one', epoch=1, tick=20, sim_time_ns=1_000_000_000,
                robot_id='aim_sentry', pose=dict(position_m=[1., 2., 3.], rotation_xyzw=[0., 0., 0., 1.]),
                camera=dict(width=2, height=2, hfov_rad=math.pi/2, frame_id='aim_sentry/aim_camera_optical_frame',
                            stamp_ns=1_000_000_000, pose_in_base=dict(position_m=[.2, 0., .3], rotation_xyzw=[-.5, .5, -.5, .5])),
                command=dict(accepted_seq=0, status='waiting', age_s=-1.))


def packet(value=None, pixels=b''):
    raw=json.dumps(value if value is not None else state(), separators=(',', ':')).encode('utf-8')
    return struct.pack('<II', len(raw), len(pixels)) + raw + pixels


class ProtocolTests(unittest.TestCase):
    def test_optional_map_identity_is_strict(self):
        value=state();value['map']={'id':'import_lab','kind':'rm_flat_map','source_md5':'a'*32}
        validate_state(value,b'','aim_sentry')
        for change in ({'source_md5':'bad'},{'kind':'unknown'},{'id':''}):
            bad=copy.deepcopy(value);bad['map'].update(change)
            with self.assertRaises(ProtocolError):validate_state(bad,b'','aim_sentry')
    def test_aim_envelope_and_bounds(self):
        cmd=hold_aim();cmd.update(yaw_type=1,pitch_type=1,yaw=.5,pitch=-.2,fire=1,permit=1)
        value=json.loads(aim_packet(state(),7,1_000_000_000,cmd))
        self.assertEqual(len(value),19);self.assertEqual(value['frame_id'],'rmsim_world');self.assertEqual(value['pitch'],-.2)
        for update in ({'yaw_type':3},{'permit':0},{'yaw':float('nan')},{'yaw_type':2,'yaw_min':1.,'yaw_max':-1.},{'fire':True}):
            with self.assertRaises(ProtocolError):aim_packet(state(),8,1_000_000_000,dict(cmd,**update))
        with self.assertRaises(ProtocolError):aim_packet(state(),8,700_000_000,cmd)

    def test_original_motor_current_contract(self):
        packet=json.loads(motor_packet(state(),1,[-850,0,100,850],[-2000,0,1000,2000],False,'aim_sentry'))
        self.assertEqual(packet['steer0'],-850)
        self.assertEqual(packet['wheel3'],2000)
        self.assertEqual(packet['kind'],'motor_current')
        self.assertEqual(len(packet),16)
        for value in (True,2001,-2001,float('nan'),1.5):
            with self.assertRaises(ProtocolError):motor_packet(state(),1,[value,0,0,0],[0]*4,False,'aim_sentry')

    def test_every_byte_fragment_retained(self):
        reader=PacketReader();pixels=bytes(range(12));wire=packet(pixels=pixels);received=[]
        for byte in wire:received.extend(reader.feed(bytes([byte])))
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][1], pixels)
        self.assertEqual(reader.buffer, b'')
        validate_state(*received[0], 'aim_sentry')

    def test_coalesced_packets_and_partial_next_header(self):
        reader=PacketReader();a=state();b=state();b['tick']=21
        wire=packet(b)
        self.assertEqual(len(reader.feed(packet(a)+wire[:7])), 1)
        self.assertEqual(reader.feed(wire[7:])[0][0]['tick'], 21)

    def test_untrusted_lengths_fail_before_payload(self):
        for header in (struct.pack('<II', MAX_JSON+1, 0), struct.pack('<II', 2, MAX_IMAGE+1), struct.pack('<II', 0, 0)):
            with self.assertRaises(ProtocolError):PacketReader().feed(header)

    def test_json_duplicate_key_and_invalid_utf8(self):
        for raw in (b'{"v":1,"v":2}', b'{"x":"\xff"}'):
            with self.assertRaises(ProtocolError):PacketReader().feed(struct.pack('<II', len(raw), 0)+raw)

    def test_camera_payload_and_frames_checked(self):
        good=state()
        validate_state(good, bytes(12), 'aim_sentry')
        with self.assertRaises(ProtocolError):validate_state(good, bytes(11), 'aim_sentry')
        bad=copy.deepcopy(good);bad['camera']['pose_in_base']['rotation_xyzw']=[0., 0., 0., 0.]
        with self.assertRaises(ProtocolError):validate_state(bad, b'', 'aim_sentry')
        bad=copy.deepcopy(good);bad['camera']['frame_id']='map'
        with self.assertRaises(ProtocolError):validate_state(bad, b'', 'aim_sentry')

    def test_nonfinite_state_rejected(self):
        for key in ('sim_time_ns', 'tick'):
            bad=state();bad[key]=True
            with self.assertRaises(ProtocolError):validate_state(bad, b'', 'aim_sentry')
        bad=state();bad['pose']['position_m'][0]=float('nan')
        with self.assertRaises(ProtocolError):validate_state(bad, b'', 'aim_sentry')

    def test_planar_command_preserves_sign_units_and_identity(self):
        raw=command_packet(state(), 9, 990_000_000, 'aim_sentry/base_link', [1.25, -.5, 0., 0., 0., -.7], 'aim_sentry')
        decoded=json.loads(raw)
        self.assertTrue(raw.endswith(b'\n'))
        self.assertEqual((decoded['vx'], decoded['vy'], decoded['wz']), (1.25, -.5, -.7))
        self.assertEqual((decoded['session'], decoded['epoch'], decoded['seq']), ('run-one', 1, 9))

    def test_bad_command_never_silently_changes_frame_or_axes(self):
        invalid=[('map', 1_000_000_000, [1, 0, 0, 0, 0, 0]),
                 ('aim_sentry/base_link', 749_999_999, [1, 0, 0, 0, 0, 0]),
                 ('aim_sentry/base_link', 1_050_000_001, [1, 0, 0, 0, 0, 0]),
                 ('aim_sentry/base_link', 1_000_000_000, [1, 0, .1, 0, 0, 0]),
                 ('aim_sentry/base_link', 1_000_000_000, [float('inf'), 0, 0, 0, 0, 0])]
        for frame,stamp_ns,twist in invalid:
            with self.assertRaises(ProtocolError):command_packet(state(), 1, stamp_ns, frame, twist, 'aim_sentry')

    def test_pinhole_intrinsics(self):
        k=intrinsics(1280, 720, math.pi/2)
        self.assertAlmostEqual(k[0], 640.)
        self.assertAlmostEqual(k[4], 640.)
        self.assertEqual((k[2], k[5]), (640., 360.))


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.server=socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.bind(('127.0.0.1', 0));self.server.listen(1);self.server.settimeout(2)
        self.client=LoopbackClient(self.server.getsockname()[1]);self.peer=None

    def tearDown(self):
        self.client.close()
        if self.peer:self.peer.close()
        self.server.close()

    def connect(self):
        deadline=time.monotonic()+2
        while not self.client.connected and time.monotonic()<deadline:
            self.client.poll();time.sleep(.001)
        self.assertTrue(self.client.connected)
        self.peer,_=self.server.accept();self.peer.settimeout(2)

    def receive_until(self, count):
        found=[];deadline=time.monotonic()+2
        while len(found)<count and time.monotonic()<deadline:
            found.extend(self.client.poll());time.sleep(.001)
        return found

    def test_actual_loopback_fragments_and_commands(self):
        self.connect();wire=packet(pixels=bytes(range(12)))
        self.peer.sendall(wire[:6]);self.assertEqual(self.client.poll(), [])
        self.peer.sendall(wire[6:-4]);self.assertEqual(self.client.poll(), [])
        self.peer.sendall(wire[-4:]);received=self.receive_until(1)
        self.assertEqual(received[0][1], bytes(range(12)))
        raw=command_packet(received[0][0], 1, 1_000_000_000, 'aim_sentry/base_link', [1, 0, 0, 0, 0, 0], 'aim_sentry')
        self.assertTrue(self.client.queue(raw));self.client.poll()
        self.assertEqual(self.peer.recv(4096), raw)

    def test_disconnect_discards_partial_state_and_unsent_command(self):
        self.connect();wire=packet();self.peer.sendall(wire[:17]);self.client.poll()
        self.assertTrue(self.client.reader.buffer)
        self.client.queue(b'{"unsent":1}\n')
        self.peer.close();self.peer=None
        deadline=time.monotonic()+2
        while self.client.connected and time.monotonic()<deadline:self.client.poll();time.sleep(.001)
        self.assertFalse(self.client.connected)
        self.assertFalse(self.client.reader.buffer)
        self.assertFalse(self.client.outbound)
        self.client.retry_at=0;self.connect()
        changed=state();changed['session']='run-two';self.peer.sendall(packet(changed))
        self.assertEqual(self.receive_until(1)[0][0]['session'], 'run-two')

    def test_malformed_frame_closes_connection(self):
        self.connect();self.peer.sendall(struct.pack('<II', MAX_JSON+1, 0))
        deadline=time.monotonic()+2
        while self.client.connected and time.monotonic()<deadline:self.client.poll();time.sleep(.001)
        self.assertFalse(self.client.connected)
        self.assertIn('length', self.client.last_error)


if __name__ == '__main__':unittest.main()
