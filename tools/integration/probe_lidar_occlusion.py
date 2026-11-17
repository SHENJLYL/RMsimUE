"""Actual near-field and own-vehicle geometry, independent of the ROS serialization gate."""
import argparse,json,math,struct
from pathlib import Path
from probe_native_bridge import Peer,rotate,multiply

def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,required=True);p.add_argument('--mode',choices=['self','blind_low','blind_high'],required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    peer=Peer(args.port);report=dict(status='running',mode=args.mode,checks=[])
    profile=json.loads((args.output/'lidar.json').read_text(encoding='utf-8-sig'));roll,pitch,yaw=profile['origin']['rpy']
    mount=multiply(multiply([0,0,math.sin(yaw/2),math.cos(yaw/2)],[0,math.sin(pitch/2),0,math.cos(pitch/2)]),[math.sin(roll/2),0,0,math.cos(roll/2)])
    def check(name,passed,**data):
        report['checks'].append(dict(name=name,passed=bool(passed),**data))
        if not passed:raise AssertionError(name)
    try:
        peer.connect();peer.pump(3.)
        frames=[(state,payload,scan) for state,payload in peer.images for scan in state['lidar']['scans'] if scan['point_count']>0]
        check('actual_scan_available',len(frames)>2,scans=len(frames))
        state,payload,scan=frames[-1];rows=list(struct.iter_unpack('<IfffBBBB',payload[scan['byte_offset']:scan['byte_offset']+scan['byte_length']]))
        own=near=behind=0;q=state['pose']['rotation_xyzw'];pos=state['pose']['position_m']
        origin=[a+b for a,b in zip(pos,rotate(q,profile['origin']['xyz']))]
        for _,x,y,z,*_ in rows:
            local=[a+b for a,b in zip(profile['origin']['xyz'],rotate(mount,[x,y,z]))]
            if abs(local[0])<.29 and abs(local[1])<.29 and .05<local[2]<.45:own+=1
            world=[a+b for a,b in zip(pos,rotate(q,local))];ray=[a-b for a,b in zip(world,origin)];distance=math.sqrt(sum(v*v for v in ray));direction=[v/distance for v in ray]
            if direction[0]<=0:continue
            t=(.95-origin[0])/direction[0];hit=[a+t*b for a,b in zip(origin,direction)]
            if t>0 and abs(hit[1])<.4 and .3<hit[2]<.8:
                if abs(distance-t)<.03:near+=1
                if distance>t+.1:behind+=1
        if args.mode=='self':check('own_vehicle_occludes_lidar',own>50,points_on_own_vehicle=own,returned_points=len(rows))
        elif args.mode=='blind_low':check('near_wall_visible_above_minimum_range',near>20 and behind==0,near_wall_points=near,illegal_background_points=behind)
        else:check('blind_occluder_does_not_reveal_background',near==0 and behind==0 and len(rows)>500,near_wall_points=near,illegal_background_points=behind,other_returns=len(rows))
        check('no_hit_rays_are_not_fake_returns',state['lidar']['returns']<state['lidar']['traced_rays'],returns=state['lidar']['returns'],traced=state['lidar']['traced_rays'])
        report['status']='passed'
    except Exception as e:report.update(status='failed',error=repr(e));raise
    finally:peer.close();(args.output/'verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('Actual LiDAR occlusion PASS',args.mode)

if __name__=='__main__':main()
