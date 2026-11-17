"""Native original message gate. Known calibration targets are NOT an auto-aim vision algorithm."""
import argparse
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time
os.environ['ROS_DOMAIN_ID']='173';os.environ['ROS_LOCALHOST_ONLY']='1'
import rclpy
from rclpy.qos import qos_profile_sensor_data
from std_msgs.msg import Int32,String
from sensor_msgs.msg import JointState
from rmsim_bridge.control_dlls import load_package_dlls
load_package_dlls('sp_msgs','pb_rm_interfaces','chassis_controllers','custom_msgs')
from sp_msgs.msg import AutoAimCommandMsg
from pb_rm_interfaces.msg import GimbalCmd
from chassis_controllers.msg import ChassisControl
from rmsim_bridge.protocol import hold_aim,aim_packet
from probe_ros_topics import Observer,ns
from probe_native_bridge import Peer,rotate


def angle(a,b):return math.atan2(math.sin(a-b),math.cos(a-b))


class Aimer(Observer):
    def __init__(self,motor):
        super().__init__();self.motor=motor;self.shooter=None;self.evaluation=None;self.joints={}
        self.auto_pub=self.create_publisher(AutoAimCommandMsg,'/aim_sentry/autoaim_command',1)
        self.scan_pub=self.create_publisher(GimbalCmd,'/aim_sentry/gimbal_scan_cmd',1)
        self.switch_pub=self.create_publisher(Int32,'/aim_sentry/auto_aim_switch',1)
        self.motor_pub=self.create_publisher(ChassisControl,'/rmsim/aim_sentry/chassis_command',1)
        self.create_subscription(String,'/aim_sentry/training_state',lambda m:setattr(self,'shooter',json.loads(m.data)),1)
        self.create_subscription(String,'/rmsim/evaluation/aim_sentry/training',lambda m:setattr(self,'evaluation',json.loads(m.data)),1)
        self.create_subscription(JointState,'/aim_sentry/gimbal/joint_states',lambda m:self.store(self.joints,ns(m.header.stamp),m),qos_profile_sensor_data)
    def auto(self,yaw,pitch,shoot=False,control=True,offset=0):
        m=AutoAimCommandMsg();stamp=max(0,self.clock+offset);m.timestamp.sec=stamp//1_000_000_000;m.timestamp.nanosec=stamp%1_000_000_000
        m.control=bool(control);m.shoot=bool(shoot);m.yaw=float(yaw);m.pitch=float(pitch);self.auto_pub.publish(m)
    def permit(self,on):self.switch_pub.publish(Int32(data=int(on)))
    def scan(self):
        m=GimbalCmd();m.header.stamp.sec=self.clock//1_000_000_000;m.header.stamp.nanosec=self.clock%1_000_000_000
        m.yaw_type=2;m.pitch_type=2;m.velocity.yaw=.8;m.velocity.pitch=.3
        m.velocity.yaw_min_range=-.25;m.velocity.yaw_max_range=.25;m.velocity.pitch_min_range=-.1;m.velocity.pitch_max_range=.15
        self.scan_pub.publish(m)
    def drive(self,wz=0.):
        if self.motor:
            m=ChassisControl();m.spin_speed=float(wz);m.power_limit=100;self.motor_pub.publish(m)
        else:self.command(wz=wz)
    def aim_state(self):return self.diag['aim']
    def yaw(self):
        q=self.truth.pose.orientation;return math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z))
    def fixture_angles(self):
        key=max(set(self.transforms)&set(self.truths));tf=self.transforms[key].transform;p=self.truths[key].pose
        q=[p.orientation.x,p.orientation.y,p.orientation.z,p.orientation.w]
        local=[tf.translation.x,tf.translation.y,tf.translation.z];offset=rotate(q,local)
        origin=[p.position.x+offset[0],p.position.y+offset[1],p.position.z+offset[2]]
        dx,dy,dz=3.4-origin[0],-origin[1],.64-origin[2] # Known middle calibration armor in import_lab.
        distance=math.hypot(dx,dy);speed=self.shooter['launch_cm_s']/100
        gravity=self.evaluation['demo_parameters']['gravity_cm_s2']/100
        disc=speed**4-gravity*(gravity*distance**2+2*dz*speed**2)
        return math.atan2(dy,dx),-math.atan2(speed**2-math.sqrt(disc),gravity*distance)


def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=19461);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--wait-reset',action='store_true');p.add_argument('--observe-only',action='store_true');p.add_argument('--motor-mode',action='store_true')
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=False);root=Path(__file__).resolve().parents[2];ros2=shutil.which('ros2')
    assert os.name=='nt' and ros2
    report=dict(status='running',scope='original ROS messages and actual UE scene/projectiles; calibration fixture, not vision recognition',motor_mode=args.motor_mode,checks=[])
    rclpy.init();node=Aimer(args.motor_mode);children=[];streams=[];trace=[]
    def start(name,argv):
        f=(args.output/(name+'.log')).open('w',encoding='utf-8');streams.append(f)
        child=subprocess.Popen([ros2,'run']+argv,stdout=f,stderr=subprocess.STDOUT,env=os.environ.copy());children.append(child);return child
    def stop(child):
        if child.poll() is None:
            subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True);child.wait(timeout=5)
    def bridge(name):return start(name,['rmsim_bridge','bridge','--ros-args','-p',f'port:={args.port}','-p','aim_control:=true','-p','control_backend:='+('lk' if args.motor_mode else 'velocity')])
    def save():
        (args.output/'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        (args.output/'trace.json').write_text(json.dumps(trace),encoding='utf-8')
    def check(name,passed,**detail):
        report['checks'].append(dict(name=name,passed=bool(passed),**detail));save()
        if not passed:raise AssertionError(name)
    def pump(seconds,action=None):
        end=node.clock+int(seconds*1e9);deadline=time.monotonic()+90;next_input=0.;next_sample=0.
        while node.clock<end:
            rclpy.spin_once(node,timeout_sec=.005);now=time.monotonic()
            if now>deadline:raise TimeoutError('simulator clock stopped')
            if action and now>=next_input:action();next_input=now+.03
            if now>=next_sample and node.diag and node.diag.get('aim'):
                trace.append(dict(stamp=node.clock,aim=node.aim_state(),command=node.diag.get('command'),yaw=node.yaw(),evaluation=node.evaluation))
                next_sample=now+.02
    try:
        server=bridge('bridge')
        if args.motor_mode:start('controller',['chassis_controllers','lk_full_wheel_chassis_controller','--ros-args','--params-file',str(root/'assets/vehicles/aim_sentry/adapters/sentry_lk.yaml')])
        node.wait(lambda:node.diag and node.diag.get('aim') and node.shooter and node.evaluation and node.truth is not None and node.diag['connected'],90)
        pump(.5)
        if args.observe_only:
            before=dict(node.aim_state());shots=node.shooter['shots'];pump(.6,lambda:(node.permit(1),node.auto(.5,.2,True)))
            check('observe_only_rejects_original_aim',node.diag['aim_adapter']['rejected']>0 and abs(node.aim_state()['yaw_rad']-before['yaw_rad'])<.01 and node.shooter['shots']==shots,
                  error=node.diag['aim_adapter']['error'])
            if args.wait_reset:
                original=node.aim_state()['yaw_rad'];epoch=node.diag['epoch']
                (args.output/'ready-for-manual-motion.json').write_text(json.dumps(dict(yaw=original)),encoding='utf-8')
                node.wait(lambda:abs(node.aim_state()['yaw_rad']-original)>.03 and abs(node.aim_state()['yaw_velocity_rad_s'])>.1,180)
                check('manual_motion_has_observed_joint_velocity',True,yaw=node.aim_state()['yaw_rad'],velocity=node.aim_state()['yaw_velocity_rad_s'])
                (args.output/'ready-for-reset.json').write_text(json.dumps(dict(epoch=epoch)),encoding='utf-8')
                node.wait(lambda:node.diag and node.diag.get('epoch',epoch)>epoch and node.diag.get('aim') and node.diag['connected'],180)
                check('observe_reset_reconnects',not node.aim_state()['enabled'])
            stop(server);peer=Peer(args.port)
            try:
                peer.connect(10);command=hold_aim();command.update(yaw_type=1,pitch_type=1,yaw=.4,permit=1,fire=1)
                old=peer.state['aim']['rejected_count'];peer.socket.sendall(aim_packet(peer.state,1,peer.state['sim_time_ns'],command))
                peer.until(lambda:peer.state['aim']['rejected_count']>old,3)
                check('server_observe_mode_rejects_aim',peer.state['aim']['last_rejection']=='aim_control_disabled' and not peer.state['aim']['trigger_active'])
            finally:peer.close()
            report['status']='passed';return
        check('original_messages_and_own_state',node.aim_state()['enabled'] and not {'target_health','hits','score','last_hit_actor'}&set(node.shooter),shooter_keys=sorted(node.shooter))
        check('training_time_matches_physics',abs(node.aim_state()['shooter']['time']-node.diag['sim_time_ns']/1e9)<1.e-4,
              training_time=node.aim_state()['shooter']['time'],physics_time=node.diag['sim_time_ns']/1e9)
        index=len(trace);pump(2.4,node.scan)
        y=[s['aim']['world_yaw_rad'] for s in trace[index:]];pitch=[s['aim']['world_pitch_rad'] for s in trace[index:]]
        check('scan_bounds_and_pitch_sign',min(y)<-.21 and max(y)>.21 and min(y)>-.28 and max(y)<.28 and min(pitch)<-.13 and max(pitch)>.08,
              yaw_range=[min(y),max(y)],pitch_down_range=[min(pitch),max(pitch)],empty_upstream_frame=node.diag['aim_adapter']['scan_empty_frame_means_world'])
        pump(.4);node.permit(1);pump(.6,lambda:node.auto(.5,.15))
        a=node.aim_state();check('absolute_world_aim',abs(angle(a['world_yaw_rad'],.5))<.01 and abs(a['world_pitch_rad']-.15)<.01,world_angles=[a['world_yaw_rad'],a['world_pitch_rad']])
        node.wait(lambda:bool(set(node.joints)&set(node.transforms)),5);key=max(set(node.joints)&set(node.transforms))
        j=node.joints[key];q=node.transforms[key].transform.rotation;f=rotate([q.x,q.y,q.z,q.w],[0,0,1]);jy,jp=j.position
        expected=[math.cos(jp)*math.cos(jy),math.cos(jp)*math.sin(jy),-math.sin(jp)]
        check('joint_feedback_matches_optical_tf',j.name==['yaw','pitch'] and math.dist(f,expected)<1e-4,vector_error=math.dist(f,expected),stamp_ns=key)
        start_yaw=node.yaw();index=len(trace);pump(2,lambda:(node.auto(0,0),node.drive(.8)))
        steady=trace[index:][-10:]
        check('world_aim_stabilizes_spinning_chassis',abs(angle(node.yaw(),start_yaw))>.6 and max(abs(s['aim']['world_yaw_rad']) for s in steady)<.02,
              chassis_turn_rad=angle(node.yaw(),start_yaw),world_error_rad=max(abs(s['aim']['world_yaw_rad']) for s in steady))
        pump(.8,lambda:node.auto(0,0));stopped=node.yaw();pump(.4,lambda:node.auto(0,0))
        chassis_safe=(not node.diag['motors']['current_active']) if args.motor_mode else node.diag['command']['status']=='expired'
        check('aim_stream_cannot_renew_chassis',chassis_safe and abs(angle(node.yaw(),stopped))<.015,chassis_change_rad=angle(node.yaw(),stopped),chassis_status=node.diag['command']['status'])
        pump(.4,lambda:node.auto(0,1.2));a=node.aim_state()
        check('actual_pitch_mechanical_limit',a['mechanically_limited'] and abs(a['pitch_rad']-a['pitch_max_rad'])<.005,pitch=a['pitch_rad'],limit=a['pitch_max_rad'])
        node.permit(0);shots=node.shooter['shots'];pump(.4,lambda:node.auto(0,0,True))
        check('permission_zero_inhibits_fire',node.shooter['shots']==shots and not node.aim_state()['trigger_active'])
        pump(.3,lambda:node.auto(0,0,False,False));node.permit(1);pump(.5,lambda:node.auto(*node.fixture_angles()))
        shots=node.shooter['shots'];ammo=node.shooter['ammo'];hits=node.evaluation['hits'];health=node.evaluation['target_health'][1]
        node.auto(*node.fixture_angles(),shoot=True);node.wait(lambda:node.shooter['shots']>shots,3);node.auto(*node.fixture_angles(),shoot=False);pump(.5)
        check('real_projectile_hits_calibration_armor',node.evaluation['hits']>hits and node.evaluation['target_health'][1]<health and node.shooter['ammo']<ammo,
              shots=node.shooter['shots']-shots,hits=node.evaluation['hits']-hits,health_before=health,health_after=node.evaluation['target_health'][1])
        before=node.shooter.copy();index=len(trace);pump(2,lambda:node.auto(1.57,0,True));node.auto(1.57,0,False);pump(.05)
        delta=node.shooter['shots']-before['shots'];parameters=node.shooter
        heat_budget=(parameters['max_heat']-before['heat']+2.1*parameters['cooling_per_second'])/parameters['heat_per_shot']
        check('heat_and_ammo_are_authoritative',delta>0 and delta<=math.ceil(heat_budget) and delta<2/parameters['shot_interval'] and before['ammo']-node.shooter['ammo']==delta and
              max(s['aim']['shooter']['heat'] for s in trace[index:])<=parameters['max_heat']+1e-5,shots=delta,heat_budget_shots=heat_budget)
        pump(.4,lambda:node.auto(1.57,0,True));pump(.6,lambda:node.drive(0));shots=node.shooter['shots'];pump(.4,lambda:node.drive(0))
        check('chassis_stream_cannot_renew_fire',node.shooter['shots']==shots and not node.aim_state()['trigger_active'],shots_after_expiry=shots)
        rejected=node.diag['aim_adapter']['rejected'];node.auto(0,0,offset=-1_000_000_000)
        node.wait(lambda:node.diag['aim_adapter']['rejected']>rejected,3)
        check('old_source_timestamp_rejected','stale' in node.diag['aim_adapter']['error'],error=node.diag['aim_adapter']['error'])
        stop(server);peer=Peer(args.port)
        try:
            peer.connect(10);peer.pump(.5);seq=1
            for label,change in [('bad_frame',{'frame_id':'map'}),('wrong_epoch',{'epoch':peer.state['epoch']+1}),
                                 ('bad_mode',{'yaw_type':3}),('bad_range',{'yaw_type':2,'yaw_min':.2,'yaw_max':-.2}),('nonfinite',{'pitch':float('nan')})]:
                value=json.loads(aim_packet(peer.state,seq,peer.state['sim_time_ns'],hold_aim()));value.update(change)
                old=peer.state['aim']['rejected_count'];peer.socket.sendall(json.dumps(value).encode()+b'\n')
                try:peer.until(lambda:peer.state['aim']['rejected_count']>old,3)
                except ConnectionError:peer.connect(5)
                check('wire_'+label,not peer.state['aim']['trigger_active'] and (peer.state['aim']['rejected_count']>old or peer.state['aim']['accepted_seq']==-1))
                seq+=1
        finally:peer.close()
        old_session=node.diag['session'];server=bridge('bridge-restart')
        node.wait(lambda:node.diag and node.diag.get('session') and node.diag['session']!=old_session and node.diag.get('aim') and node.diag['connected'],15)
        pump(.3,lambda:node.auto(0,0,True))
        check('reconnect_needs_new_permission',not node.diag['aim_adapter']['permission'] and not node.aim_state()['trigger_active'])
        if args.wait_reset:
            epoch=node.diag['epoch'];training_epoch=node.shooter['epoch']
            (args.output/'ready-for-reset.json').write_text(json.dumps(dict(epoch=epoch)),encoding='utf-8')
            node.wait(lambda:node.diag and node.diag.get('epoch',epoch)>epoch and node.diag.get('aim') and node.diag['connected'],300);pump(.3)
            check('real_R_resets_once',node.shooter['epoch']==training_epoch+1 and node.shooter['shots']==0 and not node.aim_state()['trigger_active'] and not node.diag['aim_adapter']['permission'],
                  training_epoch_before=training_epoch,training_epoch_after=node.shooter['epoch'])
        demo=start('aim-demo',['rmsim_bridge','aim_demo','--ros-args','-p','mode:=aim','-p','yaw_rad:=0.3','-p','pitch_rad:=-0.1','-p','duration_s:=1.0'])
        deadline=time.monotonic()+20
        while demo.poll() is None and time.monotonic()<deadline:rclpy.spin_once(node,timeout_sec=.01)
        pump(.2);a=node.aim_state()
        check('installed_original_aim_demo',demo.poll()==0 and abs(angle(a['world_yaw_rad'],.3))<.02 and abs(a['world_pitch_rad']+.1)<.02 and not a['trigger_active'],
              world_yaw=a['world_yaw_rad'],world_pitch=a['world_pitch_rad'])
        stop(demo)
        report['status']='passed'
    except Exception as error:
        report.update(status='failed',error=repr(error));raise
    finally:
        for child in children:stop(child)
        node.destroy_node();rclpy.shutdown()
        for stream in streams:stream.close()
        save()
    print('Native ROS aiming PASS',len(report['checks']),'checks')


if __name__=='__main__':main()
