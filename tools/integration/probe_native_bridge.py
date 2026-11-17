"""Actual UE loopback gates. No ROS mocks; ROS topic gates are a separate client."""
import argparse
import copy
import json
import math
from pathlib import Path
import socket
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
if __name__=='__main__':sys.path.insert(0,str(ROOT/'ros2_ws/src/rmsim_bridge'))
from rmsim_bridge.protocol import PacketReader,command_packet,validate_state


def rotate(q,v):
    x,y,z,w=q
    cross=lambda a,b:[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]
    a=cross([x,y,z],v);b=cross([x,y,z],a)
    return [v[i]+2*w*a[i]+2*b[i] for i in range(3)]

def multiply(a,b):
    x,y,z,w=a;X,Y,Z,W=b
    return [w*X+x*W+y*Z-z*Y,w*Y-x*Z+y*W+z*X,w*Z+x*Y-y*X+z*W,w*W-x*X-y*Y-z*Z]


class Peer:
    def __init__(self,port):
        self.port=port;self.socket=None;self.state=None;self.reader=PacketReader();self.seq=0;self.images=[];self.states=[]

    def close(self):
        if self.socket:self.socket.close()
        self.socket=None

    def connect(self,timeout=90):
        self.close();deadline=time.monotonic()+timeout
        while True:
            try:
                self.socket=socket.create_connection(('127.0.0.1',self.port),timeout=.5)
                self.socket.settimeout(.05);self.reader=PacketReader();self.state=None;self.seq=0
                # A new TCP connect can race retirement of the previous peer.
                # Exclusive servers may reject it until EOF is observed; require a real state handshake.
                self.until(lambda:self.state is not None,min(5,max(.01,deadline-time.monotonic())))
                return
            except (OSError,ConnectionError):
                self.close()
                if time.monotonic()>deadline:raise
                time.sleep(.1)

    def poll(self):
        try:raw=self.socket.recv(262144)
        except socket.timeout:return
        if not raw:raise ConnectionError('native server disconnected')
        for state,pixels in self.reader.feed(raw):
            validate_state(state,pixels,'aim_sentry')
            if self.state and (state['session'],state['epoch'])==(self.state['session'],self.state['epoch']):
                assert state['sim_time_ns']>=self.state['sim_time_ns'],'simulation clock regressed'
            self.state=state;self.states.append(state)
            if pixels:self.images.append((state,pixels))

    def until(self,predicate,timeout):
        deadline=time.monotonic()+timeout
        while not predicate():
            if time.monotonic()>deadline:raise TimeoutError('native state condition timed out')
            self.poll()

    def send(self,vx=0,vy=0,wz=0):
        self.seq+=1
        raw=command_packet(self.state,self.seq,self.state['sim_time_ns'],'aim_sentry/base_link',[vx,vy,0,0,0,wz],'aim_sentry')
        self.socket.sendall(raw);return raw

    def pump(self,seconds,velocity=None):
        deadline=time.monotonic()+seconds;next_send=0
        while time.monotonic()<deadline:
            self.poll()
            if velocity is not None and time.monotonic()>=next_send:
                self.send(*velocity);next_send=time.monotonic()+.04

    def position(self):return self.state['pose']['position_m'][:]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=19451);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--observe-only',action='store_true');parser.add_argument('--stall-probe',action='store_true')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    report=dict(scope='actual native UE TCP/physics/rendering; ROS topic proof is separate',status='running',checks=[])
    peer=Peer(args.port)
    def record(name,passed,**detail):
        report['checks'].append(dict(name=name,passed=bool(passed),**detail));save()
        if not passed:raise AssertionError(name)
    def save():
        (args.output/'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    try:
        peer.connect()
        if args.stall_probe:
            old_session=peer.state['session'];closed=False
            try:peer.pump(2,(.5,0,0))
            except ConnectionError:closed=True
            peer.connect(5);peer.pump(.7);before=peer.position();peer.pump(.5)
            record('game_thread_stall_invalidates_buffered_commands',closed and peer.state['session']!=old_session and
                   peer.state['last_disconnect']=='game_tick_stall' and peer.state['development_stall_injected'] and
                   peer.state['command']['accepted_seq']==-1 and math.dist(before,peer.position())<.02,
                   last_disconnect=peer.state['last_disconnect'],drift_m=math.dist(before,peer.position()))
            report.update(status='passed');return
        peer.pump(2,None if args.observe_only else (0,0,0));peer.until(lambda:len(peer.images)>3,5)
        image_state,pixels=peer.images[-1];camera=image_state['camera']
        record('native_clock_and_image',image_state['clock_source']=='synchronous_Chaos_solver' and max(pixels)-min(pixels)>80,
               frames=len(peer.images),width=camera['width'],height=camera['height'],clock_ns=image_state['sim_time_ns'])
        (args.output/'camera.rgb').write_bytes(pixels);(args.output/'camera-state.json').write_text(json.dumps(image_state,indent=2),encoding='utf-8')
        from PIL import Image
        Image.frombytes('RGB',(camera['width'],camera['height']),pixels).save(args.output/'camera.png')
        base=image_state['pose'];local=camera['pose_in_base'];cam_offset=rotate(base['rotation_xyzw'],local['position_m'])
        cam_position=[base['position_m'][i]+cam_offset[i] for i in range(3)];q=multiply(base['rotation_xyzw'],local['rotation_xyzw'])
        target=[4.,-1.7,.64]  # Off-axis real armor: catches an accidental observer-FOV render.
        point=rotate([-q[0],-q[1],-q[2],q[3]],[target[i]-cam_position[i] for i in range(3)])
        f=camera['width']/(2*math.tan(camera['hfov_rad']/2));u=f*point[0]/point[2]+camera['width']/2;v=f*point[1]/point[2]+camera['height']/2
        red=0
        for yy in range(round(v)-3,round(v)+4):
            for xx in range(round(u)-3,round(u)+4):
                if 0<=xx<camera['width'] and 0<=yy<camera['height']:
                    i=(yy*camera['width']+xx)*3;r,g,b=pixels[i:i+3]
                    red+=r>100 and r>1.4*g and r>1.4*b
        record('optical_projection_matches_real_armor',point[2]>0 and red>=25,projected_pixel=[u,v],red_pixels=red,world_target=target)
        record('sensor_optics_independent_of_observer',abs(camera['hfov_rad']-math.pi/3)<1.e-5 and abs(camera['observer_hfov_rad']-math.pi/6)<1.e-5,
               sensor_hfov_rad=camera['hfov_rad'],observer_hfov_rad=camera['observer_hfov_rad'])
        if args.observe_only:
            start=peer.position();peer.pump(.6,(.5,0,0))
            record('manual_mode_rejects_remote_drive',peer.state['command']['last_rejection']=='manual_mode' and math.dist(start,peer.position())<.02,
                   drift_m=math.dist(start,peer.position()))
            report.update(status='passed',states=len(peer.states),images=len(peer.images));return
        start=peer.position();peer.pump(3,(.5,0,0));end=peer.position()
        record('forward_si_motion',end[0]-start[0]>.7 and abs(end[1]-start[1])<.2,displacement=[end[i]-start[i] for i in range(3)])
        peer.pump(.9);stopped=peer.position();peer.pump(.5);after=peer.position()
        record('publisher_silence_brakes',peer.state['command']['status']=='expired' and math.dist(stopped,after)<.02,settled_drift_m=math.dist(stopped,after))
        start=peer.position();peer.pump(2,(0,.4,0));end=peer.position()
        record('left_si_motion',end[1]-start[1]>.35 and abs(end[0]-start[0])<.25,displacement=[end[i]-start[i] for i in range(3)])
        peer.pump(1,(0,0,0))
        for reason,change in [('timestamp',{'stamp_ns':max(0,peer.state['sim_time_ns']-1_000_000_000)}),
                              ('timestamp',{'stamp_ns':peer.state['sim_time_ns']+1_000_000_000}),
                              ('frame',{'frame_id':'map'}),('session_epoch',{'session':'old-run'}),
                              ('sequence',{'seq':peer.state['command']['accepted_seq']}),('limits',{'vx':4})]:
            peer.seq+=1
            payload=json.loads(command_packet(peer.state,peer.seq,peer.state['sim_time_ns'],'aim_sentry/base_link',[0,0,0,0,0,0],'aim_sentry'))
            payload.update(change);old=peer.state['command']['rejected_count'];peer.socket.sendall(json.dumps(payload).encode()+b'\n')
            peer.until(lambda:peer.state['command']['rejected_count']>old,2)
            record('reject_'+reason,peer.state['command']['last_rejection']==reason,command=peer.state['command'])
        previous_session=peer.state['session'];peer.pump(.5,(.3,0,0));peer.close();time.sleep(.8);peer.connect(5)
        before=peer.position();peer.pump(.5)
        record('disconnect_and_new_session',peer.state['session']!=previous_session and math.dist(before,peer.position())<.02,
               new_session=peer.state['session'],drift_m=math.dist(before,peer.position()))
        template=command_packet(peer.state,peer.seq+1,peer.state['sim_time_ns'],'aim_sentry/base_link',[0,0,0,0,0,0],'aim_sentry')
        for name,raw in [('duplicate_key',template.replace(b'"v":1',b'"v":1,"v":1',1)),
                         ('oversized',b'{' + b' '*5000+b'}\n')]:
            old_session=peer.state['session'];peer.socket.sendall(raw);closed=False
            try:peer.pump(.5)
            except (ConnectionError,OSError):closed=True
            peer.connect(5);record('close_'+name,closed and peer.state['session']!=old_session)
        peer.seq+=1
        raw=command_packet(peer.state,peer.seq,peer.state['sim_time_ns'],'aim_sentry/base_link',[0,0,0,0,0,0],'aim_sentry').replace(b'"vx":0.0',b'"vx":NaN',1)
        old_rejected=peer.state['command']['rejected_count'];peer.socket.sendall(raw)
        try:peer.until(lambda:peer.state['command']['rejected_count']>old_rejected,2)
        except ConnectionError:peer.connect(5)
        record('nonfinite_rejected',peer.state['command']['rejected_count']>old_rejected and peer.state['command']['accepted_seq']<peer.seq)
        # Exercise TCP fragmentation without inventing a partial command acknowledgement.
        peer.seq+=1;raw=command_packet(peer.state,peer.seq,peer.state['sim_time_ns'],'aim_sentry/base_link',[0,0,0,0,0,0],'aim_sentry')
        old=peer.state['command']['accepted_seq'];peer.socket.sendall(raw[:len(raw)//2]);peer.pump(.05)
        record('partial_command_not_applied',peer.state['command']['accepted_seq']==old)
        peer.socket.sendall(raw[len(raw)//2:]);peer.until(lambda:peer.state['command']['accepted_seq']==peer.seq,2)
        record('fragment_completed_once',peer.state['command']['accepted_seq']==peer.seq)
        old_slow=peer.state['slow_reader_disconnects'];peer.close();time.sleep(.2)
        slow=socket.socket();slow.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,4096);slow.connect(('127.0.0.1',args.port))
        time.sleep(7);peer.connect(5)
        record('slow_reader_disconnected_bounded',peer.state['slow_reader_disconnects']>old_slow,last_disconnect=peer.state['last_disconnect'])
        slow.close()
        report.update(status='passed',states=len(peer.states),images=len(peer.images))
    except Exception as error:
        report.update(status='failed',error=repr(error));raise
    finally:
        peer.close();save()
    print('Native bridge PASS',len(report['checks']),'checks',len(peer.images),'real images')


if __name__=='__main__':main()
