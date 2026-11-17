"""Measure real-time simulation throughput without touching the ROS control graph."""
import argparse
import json
import statistics
import time
from pathlib import Path


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--session',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--seconds',type=float,default=20)
    args=p.parse_args()
    if args.seconds<=1 or args.seconds>120:raise ValueError('Invalid measurement window')
    if args.output.exists():raise FileExistsError(args.output)
    args.output.mkdir(parents=True)
    rows=[]
    started=time.monotonic()
    previous=None
    while time.monotonic()-started<args.seconds:
        data=json.loads((args.session/'status.json').read_text(encoding='utf-8-sig'))
        bridge=data.get('bridge') or {}
        moment=time.monotonic()
        record=(moment,bridge.get('sim_time_ns'),data.get('phase'),bridge.get('session'),bridge.get('epoch'),bridge.get('simulation_rate'))
        if record[1] and record!=previous:rows.append(record)
        previous=record
        time.sleep(.25)
    valid=[r for r in rows if r[1] is not None]
    same_session=len(set((r[3],r[4]) for r in valid))==1
    order=all(a[1]<b[1] for a,b in zip(valid,valid[1:]))
    delta_sim=(valid[-1][1]-valid[0][1])*1e-9 if len(valid)>1 else 0.
    delta_wall=valid[-1][0]-valid[0][0] if len(valid)>1 else 0.
    rates=[r[5] for r in valid if isinstance(r[5],(float,int))]
    report={'status':'measured' if len(valid)>10 and same_session and order else 'incomplete',
            'scope':'sim time vs monotonic wall time, read-only file observation; no controls or truth injection',
            'samples':len(valid),'sim_elapsed_s':delta_sim,'wall_elapsed_s':delta_wall,
            'measured_realtime_factor':delta_sim/delta_wall if delta_wall else None,
            'median_reported_rate':statistics.median(rates) if rates else None,
            'min_reported_rate':min(rates) if rates else None,'max_reported_rate':max(rates) if rates else None,
            'session_identity':list((valid[-1][3],valid[-1][4])) if valid else None}
    (args.output/'verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
