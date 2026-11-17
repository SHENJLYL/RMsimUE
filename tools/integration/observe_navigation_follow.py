"""Read-only actual ROS monitor for navigation-follow gimbal and articulated LiDAR."""
import argparse,json,math,os,statistics,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--duration',type=float,default=480.);args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    from rmsim_bridge.native_transport import configure_navigation_transport
    os.environ['ROS_DOMAIN_ID']='71';configure_navigation_transport()
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import qos_profile_sensor_data
    from std_msgs.msg import String
    from sensor_msgs.msg import PointCloud2,Imu
    from tf2_msgs.msg import TFMessage
    rclpy.init();node=Node('rmsim_navigation_follow_observer')
    rows=[];clouds=[];imu=[];transforms={};last=[0];first=[None];last_lidar=[None]
    def observe(m):
        d=json.loads(m.data);a=d.get('aim',{});f=d.get('aim_adapter',{}).get('navigation_follow') or {}
        stamp=d.get('sim_time_ns',0)
        if stamp<=last[0] or not a:return
        last[0]=stamp
        if first[0] is None:first[0]=stamp
        rows.append({'stamp_ns':stamp,'source':d.get('aim_adapter',{}).get('source'),'state_fresh':d.get('state_fresh'),
                     'actual_yaw':a.get('yaw_rad'),'actual_pitch':a.get('pitch_rad'),'trigger':a.get('trigger_active'),
                     'follow':f,'frame':a.get('command_frame'),'aim_status':a.get('status')})
        if d.get('lidar'):last_lidar[0]=d['lidar']
        if len(rows)>25000:del rows[:1000]
    def cloud(m):
        clouds.append({'stamp_ns':m.header.stamp.sec*10**9+m.header.stamp.nanosec,'points':m.width*m.height,'frame':m.header.frame_id})
        if len(clouds)>6000:del clouds[:100]
    def inertial(m):
        imu.append([m.header.stamp.sec*10**9+m.header.stamp.nanosec,m.angular_velocity.x,m.angular_velocity.y,m.angular_velocity.z])
        if len(imu)>100000:del imu[:1000]
    def tf(m):
        for t in m.transforms:
            if t.child_frame_id=='aim_sentry/yaw_link':transforms[t.child_frame_id]=t.header.stamp.sec*10**9+t.header.stamp.nanosec
    node.create_subscription(String,'/rmsim/aim_sentry/state',observe,1)
    node.create_subscription(PointCloud2,'/aim_sentry/lidar/points',cloud,qos_profile_sensor_data)
    node.create_subscription(Imu,'/aim_sentry/mid360_imu/data_raw',inertial,qos_profile_sensor_data)
    node.create_subscription(TFMessage,'/tf',tf,100)
    report={'status':'running','scope':'Read-only original navigation-follow target, actual joint feedback, real mounted sensors; sends no commands and consumes no truth','checks':[]}
    started=time.monotonic();last_save=0
    try:
        print('FOLLOW_OBSERVER_READY',flush=True)
        while time.monotonic()-started<args.duration and not (args.output/'stop.request').exists():
            rclpy.spin_once(node,timeout_sec=.02)
            if time.monotonic()-last_save>1:
                last_save=time.monotonic();follow=[r for r in rows if r['source']=='navigation']
                live={'samples':len(rows),'following_samples':len(follow),'pointclouds':len(clouds),'imu_samples':len(imu),'last':rows[-1] if rows else None}
                (args.output/'live.json').write_text(json.dumps(live,indent=2),encoding='utf-8')
        following=[r for r in rows if r['source']=='navigation' and r['follow'].get('status') in ('following','holding')]
        errors=[abs(math.atan2(math.sin(r['actual_yaw']-r['follow']['yaw']),math.cos(r['actual_yaw']-r['follow']['yaw']))) for r in following]
        yaw_travel=sum(abs(math.atan2(math.sin(b['actual_yaw']-a['actual_yaw']),math.cos(b['actual_yaw']-a['actual_yaw']))) for a,b in zip(rows,rows[1:]))
        def check(name,ok,**info):report['checks'].append(dict(name=name,passed=bool(ok),**info))
        check('real_navigation_follow_active',len(following)>50,samples=len(following))
        check('actual_gimbal_tracks_shaped_target',bool(errors) and statistics.median(errors)<.08,median_error_rad=statistics.median(errors) if errors else None)
        check('actual_gimbal_changes_direction',yaw_travel>.3,accumulated_yaw_travel_rad=yaw_travel)
        check('original_yaw_rate_limit_retained',bool(following) and max(abs(r['follow']['yaw_rate']) for r in following)<=1.000001)
        check('navigation_fire_stays_disabled',bool(rows) and all(not r['trigger'] for r in rows))
        check('moving_mount_has_live_pointclouds',len(clouds)>10 and max(c['points'] for c in clouds)>100,clouds=len(clouds),maximum_points=max((c['points'] for c in clouds),default=0))
        check('moving_mount_imu_and_tf_received',len(imu)>200 and bool(transforms),imu_samples=len(imu),joint_tf=transforms)
        check('lidar_attached_to_actual_yaw_parent',bool(last_lidar[0]) and last_lidar[0].get('parent_frame_id')=='aim_sentry/yaw_link',profile=last_lidar[0])
        report['status']='passed' if all(c['passed'] for c in report['checks']) else 'failed'
    finally:
        (args.output/'verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        (args.output/'follow-trace.json').write_text(json.dumps({'states':rows,'clouds':clouds,'imu':imu}),encoding='utf-8')
        node.destroy_node();rclpy.shutdown()
    print('FOLLOW_OBSERVER',report['status'],len(report['checks']),flush=True)
    return 0 if report['status']=='passed' else 2

if __name__=='__main__':raise SystemExit(main())
