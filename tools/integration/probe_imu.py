"""Independent native ROS IMU observation against actual Chaos chassis motion."""
import argparse,json,math,os,shutil,statistics,subprocess,time,socket
from pathlib import Path
os.environ['ROS_DOMAIN_ID']='173';os.environ['ROS_LOCALHOST_ONLY']='1'
import rclpy
from rclpy.qos import QoSProfile,ReliabilityPolicy,DurabilityPolicy
from sensor_msgs.msg import Imu
from tf2_msgs.msg import TFMessage
from probe_ros_topics import Observer,ns
from probe_native_bridge import rotate,Peer


class ImuObserver(Observer):
    def __init__(self):
        super().__init__();self.samples={name:[] for name in ('imu','imu_offset','imu_rotated','imu_noisy')};self.mounts={}
        qos=QoSProfile(depth=200,reliability=ReliabilityPolicy.BEST_EFFORT)
        self.subs=[self.create_subscription(Imu,'/aim_sentry/'+name+'/data_raw',lambda m,n=name:self.receive(n,m),qos) for name in self.samples]
        self.static=self.create_subscription(TFMessage,'/tf_static',self.receive_static,QoSProfile(depth=1,reliability=ReliabilityPolicy.RELIABLE,durability=DurabilityPolicy.TRANSIENT_LOCAL))
    def receive(self,name,m):
        self.samples[name].append(dict(t=ns(m.header.stamp),a=[m.linear_acceleration.x,m.linear_acceleration.y,m.linear_acceleration.z],
          w=[m.angular_velocity.x,m.angular_velocity.y,m.angular_velocity.z],frame=m.header.frame_id,
          orientation_cov=list(m.orientation_covariance),ac=list(m.linear_acceleration_covariance),gc=list(m.angular_velocity_covariance),
          body_z=self.truth.pose.position.z if self.truth else None,epoch=self.diag.get('epoch') if self.diag else None))
        if self.truth:
            q=self.truth.pose.orientation;self.samples[name][-1]['body_q']=[q.x,q.y,q.z,q.w]
    def receive_static(self,m):
        for tf in m.transforms:self.mounts[tf.child_frame_id]=tf


def mean(rows,key):return [statistics.mean(r[key][i] for r in rows) for i in range(3)]
def norm(v):return math.sqrt(sum(x*x for x in v))


def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=19471);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--fixture',choices=['motion','freefall','incline'],default='motion');p.add_argument('--wait-reset',action='store_true');args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=False);report=dict(status='running',fixture=args.fixture,scope='native ROS and actual physical chassis, raw orientation unavailable',checks=[])
    rclpy.init();node=ImuObserver();children=[];logs=[]
    def start(name):
        stream=(args.output/(name+'.log')).open('w',encoding='utf-8');logs.append(stream)
        child=subprocess.Popen([shutil.which('ros2'),'run','rmsim_bridge','bridge','--ros-args','-p',f'port:={args.port}'],stdout=stream,stderr=subprocess.STDOUT,env=os.environ.copy());children.append(child);return child
    def stop(child):
        if child.poll() is None:
            subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True);child.wait(timeout=5)
    def save():
        (args.output/'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        (args.output/'samples.json').write_text(json.dumps(node.samples),encoding='utf-8')
    def check(name,passed,**details):
        report['checks'].append(dict(name=name,passed=bool(passed),**details));save()
        if not passed:raise AssertionError(name)
    def pump(seconds,velocity=None):
        end=node.clock+int(seconds*1e9);deadline=time.monotonic()+120;next_input=0.
        while node.clock<end:
            rclpy.spin_once(node,timeout_sec=.005)
            if time.monotonic()>deadline:raise TimeoutError('physics clock stopped')
            if velocity is not None and time.monotonic()>=next_input:node.command(*velocity);next_input=time.monotonic()+.03
    def recent(seconds=1.):
        return [s for s in node.samples['imu'] if s['t']>=node.clock-int(seconds*1e9)]
    try:
        bridge=start('bridge')
        node.wait(lambda:node.diag and node.diag.get('imu') and all(node.samples.values()) and len(node.mounts)==4,90)
        if args.fixture=='freefall':
            pump(2.)
            rows=node.samples['imu'];jumps=[s for s in rows if s['body_z'] is not None and s['body_z']>4.5]
            check('actual_freefall_fixture_seen',bool(jumps))
            t0=jumps[0]['t'];fall=[s for s in rows if t0+400_000_000<s['t']<t0+700_000_000 and s['body_z']>1.5]
            check('freefall_specific_force',len(fall)>20 and norm(mean(fall,'a'))<.8,mean_specific_force=mean(fall,'a'),samples=len(fall))
            check('teleport_resets_derivative',all(s['history_resets']>=1 for s in node.diag['imu']['stats']),stats=node.diag['imu']['stats'])
        elif args.fixture=='incline':
            pump(4.);rows=recent();g=-node.diag['imu']['gravity_z_mps2']
            projected=[rotate([-r['body_q'][0],-r['body_q'][1],-r['body_q'][2],r['body_q'][3]],[0,0,g]) for r in rows]
            expected=[statistics.mean(v[i] for v in projected) for i in range(3)]
            check('incline_gravity_projection',math.dist(mean(rows,'a'),expected)<.6 and expected[0]>2 and statistics.mean(r['body_z'] for r in rows)>1,
                  mean_specific_force=mean(rows,'a'),expected_from_actual_body_attitude=expected,ramp_deg=20,
                  body_tilt_deg=math.degrees(math.acos(expected[2]/g)))
        else:
            pump(2.);rows=recent();g=-node.diag['imu']['gravity_z_mps2']
            projected=[rotate([-r['body_q'][0],-r['body_q'][1],-r['body_q'][2],r['body_q'][3]],[0,0,g]) for r in rows]
            expected=[statistics.mean(v[i] for v in projected) for i in range(3)]
            check('stationary_specific_force',math.dist(mean(rows,'a'),expected)<.2 and norm(mean(rows,'w'))<.03,
                  mean_accel=mean(rows,'a'),expected_sensor_gravity=expected,mean_gyro=mean(rows,'w'),world_gravity=g)
            check('orientation_explicitly_unavailable',all(r['orientation_cov'][0]==-1 for r in rows) and rows[-1]['frame']=='aim_sentry/imu_link')
            stamps=[r['t'] for r in rows];intervals=[(b-a)/1e9 for a,b in zip(stamps,stamps[1:])]
            data=node.diag['imu'];rate=data['profiles'][0]['rate_hz'];fixed=node.diag.get('fixed_step_s',0)
            expected_rate=min(rate,1/fixed) if fixed else rate
            scheduled=math.floor(node.diag['sim_time_ns']/1e9*rate+1e-6)
            budget=data['stats'][0]['measured_samples']+data['stats'][0]['missed_periods']
            check('real_sampling_and_batch_transport',all(d>0 for d in intervals) and abs(budget-scheduled)<=1 and len(rows)>.8*expected_rate,
                  samples_last_sim_second=len(rows),mean_interval_s=statistics.mean(intervals),scheduled_periods=scheduled,accounted_periods=budget,stats=data['stats'])
            if fixed and fixed>1/rate:
                check('no_fabricated_subframe_samples',min(intervals)>=fixed-1e-6 and data['stats'][0]['missed_periods']>0,
                      physics_step=fixed,actual_rate=len(rows),nominal_rate=rate)
            index={name:{r['t']:r for r in values if r['t']>=node.clock-1_000_000_000} for name,values in node.samples.items()}
            keys=set(index['imu'])&set(index['imu_rotated'])&set(index['imu_noisy'])
            errors=[math.dist(index['imu_rotated'][t]['a'],[-index['imu'][t]['a'][2],index['imu'][t]['a'][1],index['imu'][t]['a'][0]]) for t in keys]
            check('rotated_mount_axes',len(keys)>.7*expected_rate and max(errors)<1e-5,max_error=max(errors),paired_samples=len(keys))
            noise=[[index['imu_noisy'][t]['a'][i]-index['imu'][t]['a'][i]-[.02,-.01,.03][i] for t in keys] for i in range(3)]
            means=[statistics.mean(x) for x in noise];stds=[statistics.pstdev(x) for x in noise]
            check('configured_noise_and_covariance',max(abs(x) for x in means)<.004 and all(.007<x<.013 for x in stds) and
                  abs(index['imu_noisy'][max(keys)]['ac'][0]-.0001)<1e-12,noise_means=means,noise_std=stds)
            before=node.yaw() if hasattr(node,'yaw') else None
            pump(4.,(0,0,1.));spin=recent();w=mean(spin,'w')
            check('positive_yaw_gyro_sign',.7<w[2]<1.2,mean_gyro=w)
            ref={r['t']:r for r in spin};off={r['t']:r for r in node.samples['imu_offset'] if r['t']>=node.clock-1_000_000_000}
            shared=set(ref)&set(off);dx=statistics.mean(off[t]['a'][0]-ref[t]['a'][0] for t in shared)
            expected=-.2*statistics.mean(ref[t]['w'][2]**2 for t in shared)
            check('lever_arm_centripetal_acceleration',abs(dx-expected)<.04 and dx<-.08,measured_delta_x=dx,expected_delta_x=expected)
            pump(1.,(0,0,0));index0=len(node.samples['imu']);pump(1.,(.6,0,0));accelerating=node.samples['imu'][index0:]
            check('drive_acceleration_is_measured',mean(accelerating,'a')[0]>.15,mean_accel=mean(accelerating,'a'))
            pump(1.,(0,0,0))
            check('static_tf_mount_only',set(node.mounts)=={'aim_sentry/'+n+'_link' for n in node.samples} and all(t.header.frame_id=='aim_sentry/base_link' for t in node.mounts.values()))
            old=node.diag['session'];stop(bridge);node.pump(.4)
            slow=socket.socket();slow.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,4096);slow.connect(('127.0.0.1',args.port))
            time.sleep(5);slow.close();peer=Peer(args.port)
            try:
                peer.connect(10);peer.pump(.1)
                stats=peer.state['imu']['stats']
                check('slow_reader_drops_bounded_imu_history',any(x['dropped_samples']>0 for x in stats) and len(peer.state['imu']['samples'])<=128,
                      stats=stats,last_disconnect=peer.state['last_disconnect'])
            finally:peer.close()
            bridge=start('bridge-restart')
            node.wait(lambda:node.diag and node.diag.get('session') and node.diag['session']!=old and node.diag.get('imu') and node.diag['connected'],15)
            pump(.3)
            check('reconnect_keeps_raw_sensor_contract',node.samples['imu'][-1]['orientation_cov'][0]==-1 and node.diag['imu']['calibrated'] is False)
            if args.wait_reset:
                epoch=node.diag['epoch'];stamp=node.clock
                (args.output/'ready-for-reset.json').write_text(json.dumps(dict(epoch=epoch)),encoding='utf-8')
                node.wait(lambda:node.diag and node.diag.get('epoch',epoch)>epoch and node.diag.get('imu') and node.clock<stamp,300)
                node.samples={name:[] for name in node.samples};pump(.5)
                check('reset_has_no_old_imu_backlog',bool(node.samples['imu']) and all(0<r['t']<1_000_000_000 for r in node.samples['imu']),samples=len(node.samples['imu']))
        report.update(status='passed',received_counts={name:len(v) for name,v in node.samples.items()},imu_diagnostic=node.diag['imu'])
    except Exception as error:report.update(status='failed',error=repr(error));raise
    finally:
        for child in children:stop(child)
        node.destroy_node();rclpy.shutdown()
        for stream in logs:stream.close()
        save()
    print('Native IMU PASS',len(report['checks']),'checks')


if __name__=='__main__':main()
