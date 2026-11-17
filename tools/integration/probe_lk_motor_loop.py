"""Native original LK ROS controller, original custom_msgs, actual UE current-driven joints."""
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
from rmsim_bridge.control_dlls import load_control_dlls
load_control_dlls()
from chassis_controllers.msg import ChassisControl
from probe_ros_topics import Observer
from probe_native_bridge import Peer
from rmsim_bridge.protocol import command_packet,motor_packet


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=19457);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--wait-reset',action='store_true')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    root=Path(__file__).resolve().parents[2];ros2=shutil.which('ros2');assert ros2 and os.name=='nt'
    report={'status':'running','scope':'native original LK PID/power loop and actual current-driven Chaos joints','checks':[]}
    rclpy.init();node=Observer();publisher=node.create_publisher(ChassisControl,'/rmsim/aim_sentry/chassis_command',1)
    children=[];logs=[];trace=[]
    def start(name,arguments):
        f=(args.output/(name+'.log')).open('w',encoding='utf-8');logs.append(f)
        p=subprocess.Popen([ros2,'run']+arguments,stdout=f,stderr=subprocess.STDOUT,env=os.environ.copy());children.append(p);return p
    def stop(p):
        if p.poll() is None:
            subprocess.run(['taskkill','/PID',str(p.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True);p.wait(timeout=5)
    def save():
        (args.output/'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        (args.output/'trace.json').write_text(json.dumps(trace),encoding='utf-8')
    def check(name,passed,**details):
        report['checks'].append(dict(name=name,passed=bool(passed),**details));save()
        if not passed:raise AssertionError(name)
    def pump(seconds,command=None,power=100):
        end=node.clock+int(seconds*1e9);wall_deadline=time.monotonic()+90;next_command=0.;next_sample=0.
        while node.clock<end:
            rclpy.spin_once(node,timeout_sec=.005);now=time.monotonic()
            if now>wall_deadline:raise TimeoutError('physical simulation clock did not advance')
            if command is not None and now>=next_command:
                m=ChassisControl();m.x_speed,m.y_speed,m.spin_speed=map(float,command)
                m.power_limit=power;m.emergency_stop=0;publisher.publish(m);next_command=now+.04
            if now>=next_sample and node.diag and node.truth:
                q=node.truth.pose.orientation
                trace.append(dict(wall=now,stamp=node.clock,position=node.position(),yaw=math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z)),
                    command=node.diag.get('command'),adapter=node.diag.get('lk_adapter'),motors=node.diag.get('motors'),simulation_rate=node.diag.get('simulation_rate')));next_sample=now+.03
    def yaw():
        q=node.truth.pose.orientation;return math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z))
    def delta(a,b):return math.atan2(math.sin(a-b),math.cos(a-b))
    try:
        bridge=start('bridge',['rmsim_bridge','bridge','--ros-args','-p',f'port:={args.port}','-p','control_backend:=lk'])
        controller=start('controller',['chassis_controllers','lk_full_wheel_chassis_controller','--ros-args','--params-file',str(root/'assets/vehicles/aim_sentry/adapters/sentry_lk.yaml')])
        node.wait(lambda:node.diag and node.diag.get('motors') and node.truth is not None and node.diag['connected'],90)
        pump(2,(0,0,0));check('native_original_node_and_feedback',controller.poll() is None and node.diag['lk_adapter']['feedback_samples']>20,
            node_executable='ros2_ws/install_control/chassis_controllers/lib/chassis_controllers/lk_full_wheel_chassis_controller.exe',motors=node.diag['motors'])
        motors=node.diag['motors']
        check('fallback_brake_within_motor_torque',0<motors['fallback_wheel_brake_nm']<=2000*motors['amps_per_count']*motors['wheel_nm_per_amp']+1e-9,
            maximum_nm=motors['fallback_wheel_brake_nm'])
        # Below fixed loss budget: original power limiter must suppress drive current.
        index=len(trace);start_pose=node.position();pump(.8,(.4,0,0),21)
        low=max(abs(row['current']) for sample in trace[index:] if sample.get('motors') for row in sample['motors']['wheel'])
        check('original_power_limiter_active',low<50 and math.dist(start_pose,node.position())<.05,max_current_count=low,drift_m=math.dist(start_pose,node.position()))
        index=len(trace);start_pose=node.position();pump(3,(.4,0,0));end=node.position()
        high=max(abs(row['current']) for sample in trace[index:] if sample.get('motors') for row in sample['motors']['wheel'])
        check('current_driven_forward',end[0]-start_pose[0]>.6 and abs(end[1]-start_pose[1])<.05 and high>50,
            displacement=[end[i]-start_pose[i] for i in range(3)],max_current_count=high)
        steady=[s for s in trace[index:] if s['stamp']>node.clock-500_000_000]
        angle_error=max(abs(math.asin(math.sin(w['encoder']*2*math.pi/32768))) for s in steady for w in s['motors']['steer'])
        steering_speed=max(abs(w['speed_deg_s']) for s in steady for w in s['motors']['steer'])
        check('forward_steering_settled',angle_error<math.radians(5) and steering_speed<200,
            max_angle_error_deg=math.degrees(angle_error),max_steady_speed_deg_s=steering_speed,simulation_rate=node.diag.get('simulation_rate'))
        pump(2,(0,0,0));p=node.position();pump(.5,(0,0,0))
        check('original_zero_velocity_brake',math.dist(p,node.position())<.025,drift_m=math.dist(p,node.position()))
        start_pose=node.position();pump(3,(0,.3,0));end=node.position()
        check('current_driven_left',end[1]-start_pose[1]>.4 and abs(end[0]-start_pose[0])<.05,displacement=[end[i]-start_pose[i] for i in range(3)])
        pump(2,(0,0,0));angle=yaw();p=node.position();pump(2,(0,0,.8))
        check('current_driven_positive_yaw',delta(yaw(),angle)>.7 and math.dist(p,node.position())<.3,yaw_change_rad=delta(yaw(),angle),translation_m=math.dist(p,node.position()))
        pump(1);p=node.position();pump(.5)
        check('chassis_publisher_loss_brakes',math.dist(p,node.position())<.02 and not node.diag['motors']['current_active'],
            drift_m=math.dist(p,node.position()),adapter=node.diag['lk_adapter'])
        # Explicit original emergency_stop is translated into the simulator's operator brake.
        pump(.8,(.3,0,0));m=ChassisControl();m.emergency_stop=1;publisher.publish(m);pump(.8)
        check('original_estop_brakes',not node.diag['motors']['current_active'],adapter=node.diag['lk_adapter'])
        pump(.8,(.3,0,0));stop(bridge);peer=Peer(args.port)
        try:
            peer.connect(10);peer.pump(3);p=peer.state['pose']['position_m'];peer.pump(.5)
            check('bridge_loss_brakes',not peer.state['motors']['current_active'] and math.dist(p,peer.state['pose']['position_m'])<.02,
                drift_m=math.dist(p,peer.state['pose']['position_m']))
            seq=1
            for name,changes in [('overcurrent',{'wheel0':2001}),('fractional_current',{'steer0':.5}),('invalid_brake',{'brake':2}),
                                 ('stale',{'stamp_ns':max(0,peer.state['sim_time_ns']-1_000_000_000)}),('wrong_session',{'session':'old-session'})]:
                packet=json.loads(motor_packet(peer.state,seq,[0]*4,[0]*4,True,'aim_sentry'));packet.update(changes)
                before=peer.state['command']['rejected_count'];peer.socket.sendall(json.dumps(packet).encode()+b'\n')
                peer.until(lambda:peer.state['command']['rejected_count']>before,3)
                check('motor_reject_'+name,not peer.state['motors']['current_active'] and peer.state['command']['accepted_seq']<seq,
                      reason=peer.state['command']['last_rejection']);seq+=1
            before=peer.state['command']['rejected_count']
            peer.socket.sendall(command_packet(peer.state,seq,peer.state['sim_time_ns'],'aim_sentry/base_link',[.2,0,0,0,0,0],'aim_sentry'))
            peer.until(lambda:peer.state['command']['rejected_count']>before,3)
            check('velocity_cannot_compete_with_current_mode',peer.state['command']['last_rejection']=='control_mode');seq+=1
            peer.socket.sendall(motor_packet(peer.state,seq,[0]*4,[0]*4,True,'aim_sentry'))
            peer.until(lambda:peer.state['command']['accepted_seq']==seq,3)
            check('explicit_motor_brake_acknowledged',not peer.state['motors']['current_active'])
        finally:peer.close()
        old_session=node.diag['session'];bridge=start('bridge-restarted',['rmsim_bridge','bridge','--ros-args','-p',f'port:={args.port}','-p','control_backend:=lk'])
        node.wait(lambda:node.diag and node.diag.get('session') and node.diag.get('session')!=old_session and
                  node.diag.get('motors') and node.diag.get('state_fresh') and node.diag['connected'],15);pump(.5)
        check('current_reconnect_does_not_replay',not node.diag['motors']['current_active'],adapter=node.diag['lk_adapter'])
        if args.wait_reset:
            epoch=node.diag['epoch'];stamp=node.clock
            (args.output/'ready-for-reset.json').write_text(json.dumps(dict(epoch=epoch,sim_time_ns=stamp)),encoding='utf-8')
            node.wait(lambda:node.diag and node.diag.get('epoch',epoch)>epoch and node.diag['connected'],300)
            pump(.2)
            check('real_R_resets_current_epoch',node.clock<stamp and not node.diag['motors']['current_active'],before_epoch=epoch,after_epoch=node.diag['epoch'])
        before_demo=node.position();demo_yaw=yaw()
        demo=start('drive-demo',['rmsim_bridge','drive_demo','--ros-args','-p','control_backend:=lk','-p','vx:=0.2','-p','duration_s:=3.0'])
        deadline=time.monotonic()+20
        while demo.poll() is None and time.monotonic()<deadline:rclpy.spin_once(node,timeout_sec=.01)
        movement=[node.position()[i]-before_demo[i] for i in range(3)]
        forward=movement[0]*math.cos(demo_yaw)+movement[1]*math.sin(demo_yaw)
        check('installed_original_message_demo',demo.poll()==0 and forward>.1,forward_m=forward,displacement=movement)
        stop(demo);pump(.5)
        pump(.8,(.3,0,0));stop(controller);pump(1,(.3,0,0));p=node.position();pump(.5,(.3,0,0))
        check('controller_loss_brakes',math.dist(p,node.position())<.02 and node.diag['lk_adapter']['status']=='motor_controller_timeout',
            drift_m=math.dist(p,node.position()),adapter=node.diag['lk_adapter'])
        stop(bridge);report['status']='passed'
    except Exception as error:
        report.update(status='failed',error=repr(error));raise
    finally:
        for p in children:stop(p)
        node.destroy_node();rclpy.shutdown()
        for f in logs:f.close()
        save()
    print('Native LK motor loop PASS',len(report['checks']),'checks')


if __name__=='__main__':main()
