"""Native TCP pressure against real LiDAR queues and chassis watchdog."""
import argparse,json,math,socket,time
from pathlib import Path
from probe_native_bridge import Peer

def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=19485);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=False);peer=Peer(args.port);report=dict(status='running',checks=[],scope='actual UE LiDAR30Hz pressure fixture and native chassis command watchdog')
    def save():(args.output/'verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    def check(name,passed,**data):
        report['checks'].append(dict(name=name,passed=bool(passed),**data));save()
        if not passed:raise AssertionError(name)
    try:
        peer.connect();peer.pump(2,(.3,0,0));before=peer.position();old=peer.state['session']
        peer.socket.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,4096)
        time.sleep(7);peer.close();peer.connect(15)
        first=peer.state['sim_time_ns'];data=peer.state['lidar']
        check('slow_reader_keeps_bounded_lidar_queue',peer.state['session']!=old and peer.state['slow_reader_disconnects']>0 and data['dropped_scans']>0 and len(data['scans'])<=2,dropped_scans=data['dropped_scans'],disconnect_reason=peer.state['last_disconnect'])
        check('backpressure_does_not_keep_driving',math.dist(before,peer.position())<.16,drift_m=math.dist(before,peer.position()),command=peer.state['command'])
        peer.pump(.5);session=peer.state['session']
        scans=[scan for state in peer.states if state['session']==session for scan in state['lidar']['scans']]
        check('new_connection_does_not_replay_scans',bool(scans) and all(s['start_ns']>=first for s in scans),session_start_ns=first,scans=scans)
        report['status']='passed'
    except Exception as e:report.update(status='failed',error=repr(e));raise
    finally:peer.close();save()
    print('Native LiDAR pressure PASS',len(report['checks']),'checks')

if __name__=='__main__':main()
