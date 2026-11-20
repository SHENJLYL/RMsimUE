"""Actual full-field MuJoCo clock, raw sensors and camera; TCP evaluation only."""
from __future__ import annotations
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import socket
import statistics
import struct
import sys
import time
import zlib
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/"ros2_ws/src/rmsim_bridge"))
sys.path.insert(0,str(ROOT/"tools/integration"))
from rmsim_bridge.protocol import PacketReader,validate_state,command_packet,aim_packet,hold_aim
from probe_native_bridge import rotate,multiply

def conjugate(q):return [-q[0],-q[1],-q[2],q[3]]
def norm(v):return math.sqrt(sum(x*x for x in v))
def average(rows,key):return [statistics.mean(r[key][i] for r in rows) for i in range(3)]
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def png(path,width,height,rgb):
    def chunk(tag,data):return struct.pack(">I",len(data))+tag+data+struct.pack(">I",zlib.crc32(tag+data)&0xffffffff)
    rows=b"".join(b"\0"+rgb[y*width*3:(y+1)*width*3] for y in range(height))
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack(">IIBBBBB",width,height,8,2,0,0,0))
        +chunk(b"IDAT",zlib.compress(rows))+chunk(b"IEND",b""))

class SensorPeer:
    def __init__(self,port):
        self.port=port;self.socket=None;self.reader=PacketReader();self.state=None
        self.seq=0;self.aim_seq=0;self.phase="connect"
        self.packets=[];self.samples=[];self.mounts=[];self.scans=[];self.images=[];self.frames={}
    def close(self):
        if self.socket:self.socket.close()
        self.socket=None
    def connect(self,timeout=120):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            try:
                self.close();self.socket=socket.create_connection(("127.0.0.1",self.port),timeout=.5)
                self.socket.settimeout(.1);self.reader=PacketReader();self.state=None;self.seq=0;self.aim_seq=0
                while self.state is None:
                    if time.monotonic()>deadline:raise TimeoutError("No native sensor handshake")
                    self.poll()
                return
            except (OSError,ConnectionError):
                self.close();time.sleep(.1)
        raise TimeoutError("Native sensor server unavailable")
    def poll(self):
        try:raw=self.socket.recv(1024*1024)
        except socket.timeout:return
        if not raw:raise ConnectionError("Native sensor server disconnected")
        for state,payload in self.reader.feed(raw):
            validate_state(state,payload,"aim_sentry")
            if self.state and (state["session"],state["epoch"])==(self.state["session"],self.state["epoch"]):
                if state["sim_time_ns"]<self.state["sim_time_ns"]:raise AssertionError("MuJoCo clock regressed")
            self.state=state;t=state["sim_time_ns"]
            entry={"stamp_ns":t,"wall_s":time.monotonic(),"phase":self.phase,"tick":state["tick"],
                "session":state["session"],"epoch":state["epoch"],"pose":state["pose"],"aim":state.get("aim")}
            self.packets.append(entry)
            parents={s["stamp_ns"]:s["pose_in_parent"] for s in state.get("sensor_mounts",[])}
            for s in state.get("sensor_mounts",[]):self.mounts.append(dict(s,phase=self.phase))
            profiles={p["id"]:p for p in state.get("imu",{}).get("profiles",[])}
            for s in state.get("imu",{}).get("samples",[]):
                p=profiles[s["id"]];local=p.get("pose_in_base",p.get("pose_in_parent"))
                parent_q=[0,0,0,1]
                if p["parent_frame_id"].endswith("/yaw_link"):
                    parent=parents.get(s["stamp_ns"])
                    if parent is None and parents:parent=parents[max(parents)]
                    if parent:parent_q=parent["rotation_xyzw"]
                world_q=multiply(state["pose"]["rotation_xyzw"],multiply(parent_q,local["rotation_xyzw"]))
                expected=rotate(conjugate(world_q),[0,0,-state["imu"]["gravity_z_mps2"]])
                self.samples.append(dict(s,phase=self.phase,gravity_expected=expected,
                    force_without_gravity=[a-b for a,b in zip(s["specific_force_mps2"],expected)]))
            for scan in state.get("lidar",{}).get("scans",[]):
                rows=list(struct.iter_unpack("<IfffBBBB",payload[scan["byte_offset"]:scan["byte_offset"]+scan["byte_length"]]))
                offsets=sorted({r[0] for r in rows})
                ranges=[math.sqrt(r[1]**2+r[2]**2+r[3]**2) for r in rows]
                self.scans.append(dict(scan,phase=self.phase,packet_stamp_ns=t,unique_offsets=offsets,
                    minimum_range_m=min(ranges) if ranges else None,maximum_range_m=max(ranges) if ranges else None,
                    sample_points=rows[::max(1,len(rows)//32)]))
            count=state.get("camera_bytes",len(payload))
            if count:
                rgb=payload[:count];camera=state["camera"]
                image={"phase":self.phase,"stamp_ns":camera["stamp_ns"],"packet_stamp_ns":t,
                    "width":camera["width"],"height":camera["height"],"hfov_rad":camera["hfov_rad"],
                    "bytes":count,"min":min(rgb),"max":max(rgb),"sha256":hashlib.sha256(rgb).hexdigest()}
                self.images.append(image);self.frames[self.phase]=(camera,rgb)
    def drive(self,vx=0.,vy=0.,wz=0.):
        self.seq+=1
        self.socket.sendall(command_packet(self.state,self.seq,self.state["sim_time_ns"],
            "aim_sentry/base_link",[vx,vy,0,0,0,wz],"aim_sentry"))
    def aim(self,yaw,rate=0.):
        command=hold_aim();command.update(yaw_type=1,pitch_type=1,yaw=float(yaw),pitch=0.,yaw_rate=float(rate))
        self.aim_seq+=1;self.socket.sendall(aim_packet(self.state,self.aim_seq,self.state["sim_time_ns"],command,"aim_sentry/base_link"))
    def pump(self,seconds,phase,action=None,timeout=180):
        self.phase=phase;start=self.state["sim_time_ns"];deadline=time.monotonic()+timeout;next_send=0
        while self.state["sim_time_ns"]<start+round(seconds*1e9):
            if time.monotonic()>deadline:raise TimeoutError("Native physical clock stopped during "+phase)
            self.poll()
            if action and time.monotonic()>=next_send:
                action((self.state["sim_time_ns"]-start)/1e9);next_send=time.monotonic()+.03

def main():
    p=argparse.ArgumentParser();p.add_argument("--port",type=int,required=True);p.add_argument("--output",type=Path,required=True)
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    report={"schema":"rmsim_mujoco_sensor_tcp_v1","status":"running","checks":[],
        "scope":"Real UE full RMUC scene, MuJoCo dynamics and genuine sensor samples; TCP observer; truth only for independent measurement",
        "command":sys.argv,"probe_sha256":sha(__file__),"protocol_sha256":sha(ROOT/"ros2_ws/src/rmsim_bridge/rmsim_bridge/protocol.py")}
    peer=SensorPeer(args.port)
    def save():
        (args.output/"verification.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    def check(name,ok,**detail):
        report["checks"].append(dict(name=name,passed=bool(ok),**detail));save()
        print(("PASS " if ok else "FAIL ")+name,flush=True)
        if not ok:raise AssertionError(name)
    try:
        peer.connect();initial=dict(peer.state);peer.pump(2.5,"rest",lambda _:peer.drive())
        state=peer.state
        check("native_mujoco_clock_and_full_map",state.get("physics_backend")=="mujoco" and
            state.get("clock_source")=="synchronous_MuJoCo_solver" and abs(state["dynamics_step_s"]-.001)<1e-12
            and abs(state["fixed_step_s"]-.005)<1e-12 and state["map"]["id"]=="rmuc2027",
            physics_backend=state.get("physics_backend"),clock_source=state.get("clock_source"),
            dynamics_step_s=state.get("dynamics_step_s"),sensor_step_s=state.get("fixed_step_s"),map=state["map"])
        cutoff=state["sim_time_ns"]-1_000_000_000
        for name in ("imu","mid360_imu"):
            rows=[r for r in peer.samples if r["id"]==name and r["phase"]=="rest" and r["stamp_ns"]>=cutoff]
            intervals=[(b["stamp_ns"]-a["stamp_ns"])/1e9 for a,b in zip(rows,rows[1:])]
            check(name+"_actual_200hz",195<=len(rows)<=202 and intervals and
                max(abs(v-.005) for v in intervals)<1e-9 and all(abs(r["integration_dt_s"]-.005)<1e-8 for r in rows),
                samples_last_sim_second=len(rows),interval_min_s=min(intervals) if intervals else None,
                interval_max_s=max(intervals) if intervals else None)
            force=average(rows,"specific_force_mps2");expected=average(rows,"gravity_expected");gyro=average(rows,"angular_velocity_rad_s")
            check(name+"_stationary_specific_force",math.dist(force,expected)<.2 and norm(gyro)<.03,
                measured_m_s2=force,expected_from_actual_sensor_attitude_m_s2=expected,mean_gyro_rad_s=gyro)
        mounts=[m for m in peer.mounts if m["phase"]=="rest"];rest_mount=mounts[-1]["pose_in_parent"]
        lidar=state["lidar"];sensor=lidar["pose_in_parent"]
        rest_origin=[a+b for a,b in zip(rest_mount["position_m"],rotate(rest_mount["rotation_xyzw"],sensor["position_m"]))]
        check("source_articulated_sensor_profiles",lidar["parent_frame_id"]=="aim_sentry/yaw_link" and
            lidar["rays_per_second"]==200000 and lidar["frame_hz"]==10 and
            {p["id"]:p["parent_frame_id"] for p in state["imu"]["profiles"]}==
            {"imu":"aim_sentry/base_link","mid360_imu":"aim_sentry/yaw_link"},
            lidar_profile_md5=lidar["profile_md5"],imu_profile_md5=state["imu"]["profile_md5"],lidar_mount=sensor)
        peer.pump(2.5,"rotate",lambda t:(peer.drive(),peer.aim(min(1.,.5*t),.5 if t<2. else 0.)))
        state=peer.state;rotating=[r for r in peer.samples if r["phase"]=="rotate" and r["id"]=="mid360_imu"]
        rotate_start=min(r["stamp_ns"] for r in rotating)
        middle=[r for r in rotating if rotate_start+400_000_000<r["stamp_ns"]<rotate_start+1_750_000_000]
        mid=next(p for p in state["imu"]["profiles"] if p["id"]=="mid360_imu")
        rates=[rotate(mid["pose_in_parent"]["rotation_xyzw"],r["angular_velocity_rad_s"])[2] for r in middle]
        mount=next(m for m in reversed(peer.mounts) if m["phase"]=="rotate")["pose_in_parent"]
        origin=[a+b for a,b in zip(mount["position_m"],rotate(mount["rotation_xyzw"],sensor["position_m"]))]
        check("physical_gimbal_and_optical_origin",abs(state["aim"]["yaw_rad"]-1.)<.03 and math.dist(origin,rest_origin)>.15,
            actual_yaw_rad=state["aim"]["yaw_rad"],before_m=rest_origin,after_m=origin)
        check("mounted_imu_tracks_gimbal_rate",len(rates)>200 and abs(statistics.median(rates)-.5)<.08,
            samples=len(rates),median_parent_z_rate_rad_s=statistics.median(rates) if rates else None)
        force_norms=[norm(r["specific_force_mps2"]) for r in middle]
        check("moving_mount_specific_force",9.<statistics.median(force_norms)<10.7,
            median_specific_force_m_s2=statistics.median(force_norms))
        before=state["pose"]["position_m"][:];peer.pump(1.5,"drive",lambda _:(peer.drive(.25),peer.aim(1.)))
        peer.pump(.8,"brake",lambda _:(peer.drive(),peer.aim(1.)))
        after=peer.state["pose"]["position_m"];delta=[b-a for a,b in zip(before,after)]
        check("sensor_stream_during_real_drive",.18<delta[0]<.6 and abs(delta[1])<.08,displacement_m=delta)
        moving=[r for r in peer.samples if r["id"]=="imu" and r["phase"]=="drive"]
        first=min(r["stamp_ns"] for r in moving);early=[r for r in moving if r["stamp_ns"]<first+600_000_000]
        acceleration=average(early,"force_without_gravity")
        check("imu_measures_actual_drive_acceleration",acceleration[0]>.1,mean_acceleration_m_s2=acceleration)
        state=peer.state;lidar=state["lidar"];scans=[s for s in peer.scans if s["point_count"]]
        check("real_lidar_packets_and_range",len(scans)>30 and max(s["point_count"] for s in scans)>1000 and
            all(s["start_ns"]<=s["end_ns"]<=s["packet_stamp_ns"] for s in scans) and
            min(s["minimum_range_m"] for s in scans)>=lidar["min_range_m"]-1e-5 and
            max(s["maximum_range_m"] for s in scans)<=lidar["max_range_m"]+1e-4,
            scans=len(scans),maximum_points=max(s["point_count"] for s in scans))
        check("lidar_real_5ms_acquisition_times",all(19<=len(s["unique_offsets"])<=20 and
            s["unique_offsets"][-1]-s["unique_offsets"][0]>=90_000_000 and
            all(min(v%5_000_000,5_000_000-v%5_000_000)<=2 for v in s["unique_offsets"]) for s in scans),
            minimum_distinct_times=min(len(s["unique_offsets"]) for s in scans),
            maximum_distinct_times=max(len(s["unique_offsets"]) for s in scans),max_physics_step_s=lidar["max_physics_step_s"])
        check("lidar_budget_and_opaque_occlusion",lidar["missed_rays"]==0 and lidar["dropped_scans"]==0 and
            lidar["max_physics_step_s"]<.00501 and lidar["self_occluded_rays"]>0 and
            0<lidar["returns"]<lidar["traced_rays"],metadata=lidar)
        images=[i for i in peer.images if i["stamp_ns"]>=peer.packets[0]["stamp_ns"]+1_000_000_000]
        check("actual_rendered_camera",len(images)>=40 and all(i["bytes"]==640*360*3 and i["max"]-i["min"]>80 and
            i["stamp_ns"]<=i["packet_stamp_ns"] and abs(i["hfov_rad"]-math.pi/3)<1e-5 for i in images),
            frames=len(images),distinct_images=len({i["sha256"] for i in images}),dimensions=[640,360])
        check("camera_changes_with_real_gimbal",peer.frames["rest"][1]!=peer.frames["rotate"][1])
        packet_times=[p["stamp_ns"] for p in peer.packets]
        check("clock_and_sensor_stamps_share_epoch",all(t%5_000_000==0 for t in packet_times) and
            all(s["stamp_ns"]%5_000_000==0 for s in peer.samples) and len({p["epoch"] for p in peer.packets})==1)
        check("sensor_sampling_no_drop_or_invalid",all(s["invalid_samples"]==0 and s["dropped_samples"]==0
            for s in state["imu"]["stats"]),statistics=state["imu"]["stats"])
        check("no_fire_during_sensor_test",not state["aim"]["trigger_active"])
        original_session=state["session"];last_stamp=state["sim_time_ns"]
        peer.close();peer.phase="reconnect";peer.connect(20);peer.pump(.3,"reconnect",lambda _:peer.drive())
        fresh=[s for s in peer.samples if s["phase"]=="reconnect"]
        check("reconnect_discards_old_sensor_backlog",peer.state["session"]!=original_session and
            fresh and min(s["stamp_ns"] for s in fresh)>last_stamp and
            all(s["start_ns"]>=last_stamp for s in peer.scans if s["phase"]=="reconnect"),
            previous_session=original_session,new_session=peer.state["session"],first_fresh_imu_ns=min(s["stamp_ns"] for s in fresh))
        elapsed_sim=(peer.packets[-1]["stamp_ns"]-peer.packets[0]["stamp_ns"])/1e9
        elapsed_wall=peer.packets[-1]["wall_s"]-peer.packets[0]["wall_s"]
        report.update(status="passed",simulated_seconds=elapsed_sim,observed_wall_seconds=elapsed_wall,
            observed_simulation_rate=elapsed_sim/elapsed_wall,real_time_gate=False,final_state=peer.state)
    except Exception as error:
        report.update(status="failed",error=repr(error));raise
    finally:
        if peer.socket and peer.state:
            try:peer.drive()
            except OSError:pass
        peer.close()
        for phase,(camera,rgb) in peer.frames.items():
            png(args.output/(phase+"-camera.png"),camera["width"],camera["height"],rgb)
        for name,value in (("packet-trace",peer.packets),("imu-samples",peer.samples),("joint-mounts",peer.mounts),
                ("lidar-scans",peer.scans),("camera-frames",peer.images)):
            (args.output/(name+".json")).write_text(json.dumps(value,separators=(",",":"))+"\n",encoding="utf-8")
        save()
    print("MuJoCo sensor TCP PASS",len(report["checks"]),"checks",flush=True)
if __name__=="__main__":main()
