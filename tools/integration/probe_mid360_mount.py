"""Native moving optical mount and raw IMU test; all evaluation stays outside algorithms."""
import argparse
import json
import math
from pathlib import Path
import socket
import statistics
import sys
import time
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'ros2_ws/src/rmsim_bridge'))
from rmsim_bridge.protocol import PacketReader,validate_state,aim_packet,hold_aim
from probe_native_bridge import rotate


def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    report={'status':'running','scope':'real UE articulated geometry, raycasts, encoder TF and sampled IMU; TCP observer only','checks':[]}
    reader=PacketReader();samples=[];sequence=0;peer=None
    def save():
        (args.output/'verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    def check(name,ok,**data):
        report['checks'].append(dict(name=name,passed=bool(ok),**data));save()
        if not ok:raise AssertionError(name)
        print('PASS',name,flush=True)
    def receive(deadline):
        while time.monotonic()<deadline:
            try:raw=peer.recv(1024*1024)
            except socket.timeout:continue
            if not raw:raise RuntimeError('Native simulator disconnected')
            packets=reader.feed(raw)
            if packets:
                for state,payload in packets:validate_state(state,payload,'aim_sentry')
                return packets
        raise TimeoutError('Waiting for real simulator sensors')
    try:
        deadline=time.monotonic()+90
        while peer is None:
            try:peer=socket.create_connection(('127.0.0.1',args.port),timeout=1);peer.settimeout(.5)
            except OSError:
                if time.monotonic()>deadline:raise
                time.sleep(.2)
        start=None;rest=None;last=None;rates=[];accels=[];max_points=0;max_window=0;max_occluded=0
        phase='rest';phase_start=None
        while time.monotonic()<deadline:
            for state,payload in receive(deadline):
                t=state['sim_time_ns']*1e-9
                if start is None:start=t
                lidar=state['lidar'];mounts=state.get('sensor_mounts',[])
                max_points=max(max_points,max((s['point_count'] for s in lidar['scans']),default=0))
                max_window=max(max_window,lidar.get('optical_window_passes',0));max_occluded=max(max_occluded,lidar.get('self_occluded_rays',0))
                if not mounts:continue
                parent=mounts[-1]['pose_in_parent'];sensor=lidar['pose_in_parent']
                offset=rotate(parent['rotation_xyzw'],sensor['position_m'])
                position=[a+b for a,b in zip(parent['position_m'],offset)]
                row={'time_s':t,'phase':phase,'yaw':state['aim']['yaw_rad'],'position_in_base_m':position,
                     'imu':state['imu']['samples'],'mounts':mounts,'returns':lidar['returns']};samples.append(row)
                if phase=='rest' and t-start>2:
                    rest=row;phase='rotate';phase_start=t
                    check('measured_yaw_parent_mount',lidar['parent_frame_id']=='aim_sentry/yaw_link' and lidar.get('mount_schema')==2,
                          pose_in_parent=sensor,position_in_base_m=position)
                    check('real_scene_returns_through_optical_window',max_points>100,maximum_points_per_scan=max_points,optical_window_passes=max_window,self_occluded_rays=max_occluded)
                    profiles=state['imu']['profiles'];mid=next(v for v in profiles if v['id']=='mid360_imu')
                    check('rigidly_comounted_lidar_imu',mid['parent_frame_id']==lidar['parent_frame_id'],imu_mount=mid['pose_in_parent'])
                if phase=='rotate':
                    elapsed=t-phase_start;command=hold_aim();command.update(yaw_type=1,pitch_type=1,yaw=min(1.,elapsed*.5),pitch=0.,yaw_rate=.5 if elapsed<2 else 0.)
                    sequence+=1;peer.sendall(aim_packet(state,sequence,state['sim_time_ns'],command,'aim_sentry/base_link'))
                    if .35<elapsed<1.8:
                        for sample in state['imu']['samples']:
                            if sample['id']=='mid360_imu':
                                local=rotate(mid['pose_in_parent']['rotation_xyzw'],sample['angular_velocity_rad_s'])
                                rates.append(local[2]);accels.append(math.sqrt(sum(v*v for v in sample['specific_force_mps2'])))
                    if elapsed>2.4:
                        last=row;break
            if last is not None:break
        if last is None:raise TimeoutError('Native yaw movement did not finish')
        check('actual_gimbal_rotated',abs(last['yaw']-1.)<.03,actual_yaw_rad=last['yaw'])
        check('optical_origin_follows_real_joint',math.dist(last['position_in_base_m'],rest['position_in_base_m'])>.15,
              before_m=rest['position_in_base_m'],after_m=last['position_in_base_m'])
        check('mounted_imu_measures_joint_rotation',len(rates)>50 and abs(statistics.median(rates)-.5)<.08,
              samples=len(rates),median_yaw_rate_rad_s=statistics.median(rates) if rates else None)
        check('mounted_specific_force_has_physical_gravity',bool(accels) and 9.<statistics.median(accels)<10.7,
              median_specific_force_m_s2=statistics.median(accels) if accels else None)
        check('opaque_self_geometry_retained',max_occluded>0,self_occluded_rays=max_occluded)
        check('navigation_test_never_fires',not state['aim']['trigger_active'])
        report.update(status='passed',maximum_points_per_scan=max_points,source_profile_md5=state['lidar']['profile_md5'])
    except Exception as e:report.update(status='failed',error=repr(e));raise
    finally:
        if peer:peer.close()
        save();(args.output/'mount-trace.json').write_text(json.dumps(samples),encoding='utf-8')
    print('Native Mid-360 mount PASS',len(report['checks']),'checks',flush=True)

if __name__=='__main__':main()
