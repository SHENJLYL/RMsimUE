"""Live TCP gate for the shared vehicle shaper, with less than 1 m excursion.

World poses and velocity diagnostics use ROS ENU metres/radians. Positive yaw
turns left. Truth is consumed only by this independent evaluator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import socket
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "ros2_ws/src/rmsim_bridge"))
from rmsim_bridge.protocol import PacketReader, command_packet, validate_state


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def yaw(pose):
    x, y, z, w = pose["rotation_xyzw"]
    return math.atan2(2 * (w*z + x*y), 1 - 2 * (y*y + z*z))


def angle_delta(after, before):
    return math.atan2(math.sin(after-before), math.cos(after-before))


def speed(row):
    return math.hypot(*row["drive"]["filtered_world_linear_m_s"][:2])


def rates(rows, value):
    return [(value(b)-value(a))/((b["stamp_ns"]-a["stamp_ns"])*1e-9)
            for a, b in zip(rows, rows[1:]) if b["stamp_ns"] > a["stamp_ns"]]


class DrivePeer:
    def __init__(self, port, trace):
        self.port, self.trace = port, trace
        self.socket = None
        self.reader = PacketReader()
        self.state = None
        self.seq = 0
        self.phase = "connect"
        self.rows, self.commands = [], []
        self.origin = None
        self.max_excursion_m = 0.

    def close(self):
        if self.socket:
            self.socket.close()
        self.socket = None

    def connect(self, timeout=120.):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                self.socket = socket.create_connection(("127.0.0.1", self.port), timeout=.5)
                self.socket.settimeout(.05)
                break
            except OSError:
                time.sleep(.1)
        if not self.socket:
            raise TimeoutError("Native shared-drive bridge unavailable")
        while self.state is None:
            if time.monotonic() > deadline:
                raise TimeoutError("Native shared-drive handshake timed out")
            self.poll()

    def poll(self):
        try:
            raw = self.socket.recv(256*1024)
        except socket.timeout:
            return
        if not raw:
            raise ConnectionError("Native shared-drive bridge disconnected")
        for state, payload in self.reader.feed(raw):
            validate_state(state, payload, "aim_sentry")
            if self.state:
                if (state["session"], state["epoch"]) != (self.state["session"], self.state["epoch"]):
                    raise AssertionError("Unexpected reset or connection epoch during drive gate")
                if state["sim_time_ns"] <= self.state["sim_time_ns"]:
                    raise AssertionError("Native clock did not advance")
            self.state = state
            drive = dict(state["drive"])
            drive.pop("control_parameters", None)  # Full source values are bound once in the report.
            row = {"kind": "state", "phase": self.phase, "stamp_ns": state["sim_time_ns"],
                   "wall_s": time.monotonic(), "tick": state["tick"], "command": state["command"],
                   "drive": drive, "pose_enu_m": state["pose"]}
            self.rows.append(row)
            self.trace.write(json.dumps(row, separators=(",", ":")) + "\n")
            p = state["pose"]["position_m"]
            if self.origin is None:
                self.origin = p[:2]
            excursion = math.dist(p[:2], self.origin)
            self.max_excursion_m = max(self.max_excursion_m, excursion)
            if excursion >= 1.:
                self.send((0., 0., 0.))
                raise AssertionError("Actual chassis excursion reached the 1 m gate boundary")

    def send(self, request):
        self.seq += 1
        vx, vy, wz = request
        self.socket.sendall(command_packet(self.state, self.seq, self.state["sim_time_ns"],
            "aim_sentry/base_link", [vx, vy, 0., 0., 0., wz], "aim_sentry"))
        row = {"kind": "input", "phase": self.phase, "stamp_ns": self.state["sim_time_ns"],
               "wall_s": time.monotonic(), "seq": self.seq, "base_link_vx_vy_wz": list(request)}
        self.commands.append(row)
        self.trace.write(json.dumps(row, separators=(",", ":")) + "\n")

    def pump(self, seconds, phase, request=None):
        self.phase = phase
        first = len(self.rows)
        start = self.state["sim_time_ns"]
        next_send = start
        deadline = time.monotonic() + max(10., 8*seconds)
        while self.state["sim_time_ns"] < start + round(seconds*1e9):
            if time.monotonic() > deadline:
                raise TimeoutError("Native clock stalled during " + phase)
            if request is not None and self.state["sim_time_ns"] >= next_send:
                self.send(request)
                next_send = start + ((self.state["sim_time_ns"]-start)//50_000_000+1)*50_000_000
            self.poll()
        self.trace.flush()
        return self.rows[first:]

    def acknowledged(self, phase, applied=False):
        seqs = {r["seq"] for r in self.commands if r["phase"] == phase}
        rows = [r for r in self.rows if r["phase"] == phase and r["command"]["accepted_seq"] in seqs]
        # Acceptance can occur after the vehicle tick. Applied targets then become
        # visible on the next 5 ms vehicle tick; retain this evidence in the trace.
        if applied and rows:
            cutoff = rows[0]["stamp_ns"] + round(self.state["fixed_step_s"]*1e9)
            rows = [r for r in rows if r["stamp_ns"] >= cutoff]
        return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    profile_hash = sha(args.profile)
    control = json.loads(args.profile.read_text(encoding="utf-8-sig"))["control"]
    report = {"schema": "rmsim_shared_drive_profile_tcp_v1", "status": "running", "checks": [],
              "scope": "Live MuJoCo vehicle profile/continuous shaper and watchdog; no navigation, sensor or realtime acceptance",
              "coordinates": "ROS ENU world; base_link x forward, y left, positive yaw left; SI",
              "command": sys.argv, "profile_path": str(args.profile.resolve()), "profile_sha256": profile_hash,
              "probe_sha256": sha(__file__), "protocol_sha256": sha(ROOT / "ros2_ws/src/rmsim_bridge/rmsim_bridge/protocol.py"),
              "expected_control_parameters": control}
    trace = (args.output / "trace.jsonl").open("w", encoding="utf-8")
    peer = DrivePeer(args.port, trace)

    def save():
        (args.output / "verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    def check(name, passed, **detail):
        report["checks"].append(dict(name=name, passed=bool(passed), **detail))
        save()
        print(("PASS " if passed else "FAIL ") + name, flush=True)
        if not passed:
            raise AssertionError(name)

    def settled(name, duration=.35):
        rows = peer.pump(duration, name, (0., 0., 0.))
        if speed(rows[-1]) > 1e-7 or abs(rows[-1]["drive"]["filtered_yaw_rad_s"]) > 1e-7:
            raise AssertionError("Shared shaper has not stopped: " + name)
        return rows

    try:
        peer.connect()
        settled("settle", 2.)
        state = peer.state
        report["actual_control_parameters"] = state["drive"].get("control_parameters")
        check("native_full_map_same_profile", state.get("physics_backend") == "mujoco"
              and state["map"]["id"] == "rmuc2027" and abs(state["dynamics_step_s"]-.001) < 1e-12
              and abs(state["fixed_step_s"]-.005) < 1e-12
              and state["drive"].get("output_velocity_shaper") == "shared_drive_profile_v1"
              and state["drive"].get("control_parameters") == control,
              observed_control_count=len(state["drive"].get("control_parameters", {})), map=state["map"])
        cases = [("reverse_cap", (-4., 0., 0.), (-control["MaxReverseSpeedMps"], 0.)),
                 ("left_cap", (0., 5., 0.), (0., control["MaxLateralSpeedMps"])),
                 ("right_cap", (0., -5., 0.), (0., -control["MaxLateralSpeedMps"]))]
        for name, request, expected in cases:
            peer.pump(.10, name, request)
            rows = peer.acknowledged(name)
            check(name, len(rows) >= 2 and all(
                math.dist(r["drive"]["requested_linear_m_s"][:2], expected) < 1e-9
                and not r["drive"]["requested_in_world_frame"] for r in rows),
                sent_base_link=request, expected_base_link=expected, observed_samples=len(rows))
            settled(name + "_stop")

        phase = "continuous_forward"
        peer.pump(.60, phase, (control["MaxForwardSpeedMps"], 0., 0.))
        rows = peer.acknowledged(phase)
        acc = [r["drive"]["speed_acceleration_m_s2"] for r in rows]
        seq_count = len({r["command"]["accepted_seq"] for r in rows})
        jerk = rates(rows, lambda r: r["drive"]["speed_acceleration_m_s2"])
        actual_acc = rates(rows, speed)
        first_send = next(r["stamp_ns"] for r in peer.commands if r["phase"] == phase)
        amax, jmax = control["LinearAccelerationMps2"], control["LinearJerkMps3"]
        check("continuous_acceleration_across_20hz_commands", len(rows) >= 15 and seq_count >= 8
              and all(math.dist(r["drive"]["requested_linear_m_s"][:2], [control["MaxForwardSpeedMps"], 0.]) < 1e-9 for r in rows)
              and min(acc) >= -1e-9 and max(acc) <= amax+1e-9 and acc[-1] >= amax-.03
              and all(-1e-7 <= j <= jmax+1e-6 for j in jerk)
              and all(-1e-7 <= a <= amax+1e-6 for a in actual_acc)
              and all(r["drive"]["speed_acceleration_m_s2"] <= jmax*((r["stamp_ns"]-first_send)*1e-9+.005)+1e-6 for r in rows),
              accepted_sequences=seq_count, acceleration_min=min(acc), acceleration_max=max(acc),
              acceleration_final=acc[-1], measured_speed_acceleration_max=max(actual_acc), observed_jerk_min=min(jerk), observed_jerk_max=max(jerk))

        braking = settled("explicit_brake", .80)
        rows = peer.acknowledged("explicit_brake")
        active = [r for r in rows if speed(r) > .02]
        brake_jerk = rates(active, lambda r: r["drive"]["speed_acceleration_m_s2"])
        check("explicit_zero_brakes_with_shared_profile", len(active) >= 4
              and all(r["drive"]["physical_brake"] for r in rows)
              and min(r["drive"]["speed_acceleration_m_s2"] for r in rows) >= -control["BrakeDecelerationMps2"]-1e-9
              and all(abs(j) <= control["DecelerationJerkMps3"]+1e-6 for j in brake_jerk)
              and speed(braking[-1]) < 1e-7,
              active_brake_samples=len(active), observed_jerk=brake_jerk,
              final_filtered_speed_m_s=speed(braking[-1]))

        for sign, name in ((1., "left_yaw"), (-1., "right_yaw")):
            before_yaw = yaw(peer.state["pose"])
            phase = name + "_cap"
            peer.pump(.38, phase, (0., 0., sign*2.))
            rows = peer.acknowledged(phase, applied=True)
            requested = [r["drive"]["applied_requested_yaw_rad_s"] for r in rows]
            angular_acc = rates(rows, lambda r: sign*r["drive"]["filtered_yaw_rad_s"])
            signed_angle = sign*angle_delta(yaw(peer.state["pose"]), before_yaw)
            first_send = next(r["stamp_ns"] for r in peer.commands if r["phase"] == phase)
            check(name + "_signed_cap_and_acceleration", len(rows) >= 8
                  and all(abs(v-sign*control["ManualYawRateRadS"]) < 1e-9 for v in requested)
                  and all(-1e-7 <= a <= control["AngularAccelerationRadS2"]+1e-6 for a in angular_acc)
                  and all(sign*r["drive"]["filtered_yaw_rad_s"] <= control["AngularAccelerationRadS2"]*((r["stamp_ns"]-first_send)*1e-9+.005)+1e-6 for r in rows)
                  and 0 < sign*rows[-1]["drive"]["filtered_yaw_rad_s"] <= control["ManualYawRateRadS"]+1e-9
                  and signed_angle > .01,
                  requested_yaw_rad_s=requested[-1], final_filtered_yaw_rad_s=rows[-1]["drive"]["filtered_yaw_rad_s"],
                  actual_signed_yaw_change_rad=signed_angle, maximum_angular_acceleration_rad_s2=max(angular_acc))
            stopped = settled(name + "_brake", .45)
            angular_decel = rates(stopped, lambda r: sign*r["drive"]["filtered_yaw_rad_s"])
            check(name + "_angular_brake", all(-control["AngularDecelerationRadS2"]-1e-6 <= a <= 1e-7 for a in angular_decel),
                  minimum_angular_acceleration_rad_s2=min(angular_decel))
            if sign > 0:
                peer.pump(.12, "body_frame_after_turn", (.25, 0., 0.))
                body_rows = peer.acknowledged("body_frame_after_turn", applied=True)
                errors = [angle_delta(math.atan2(r["drive"]["applied_requested_world_linear_m_s"][1],
                                                r["drive"]["applied_requested_world_linear_m_s"][0]),
                                      yaw(r["pose_enu_m"])) for r in body_rows]
                check("base_link_target_follows_actual_chassis_heading", len(errors) >= 3
                      and abs(yaw(peer.state["pose"])-before_yaw) > .02 and max(abs(e) for e in errors) < .02,
                      world_target_minus_actual_ros_yaw_rad=errors)
                settled("body_frame_stop")

        peer.pump(.20, "lease_seed", (.4, 0., 0.))
        sent = peer.seq
        peer.pump(1., "lease_expiry")
        state = peer.state
        check("stop_sending_expires_without_command_replay", state["command"]["accepted_seq"] == sent
              and state["command"]["status"] == "expired" and state["command"]["age_s"] > .25
              and state["drive"]["physical_brake"] and speed(peer.rows[-1]) < 1e-7
              and math.hypot(*state["drive"]["requested_linear_m_s"][:2]) < 1e-9,
              final_command=state["command"], final_filtered_speed_m_s=speed(peer.rows[-1]))
        settled("final_stop", .3)
        check("actual_motion_remains_local_and_inputs_unchanged", peer.max_excursion_m < 1.
              and peer.state["command"]["rejected_count"] == 0 and sha(args.profile) == profile_hash,
              maximum_actual_planar_excursion_m=peer.max_excursion_m, state_samples=len(peer.rows), commands=len(peer.commands))
        report["status"] = "passed"
    except Exception as error:
        report["status"] = "failed"
        report["reason"] = str(error)
        print("FAIL " + str(error), file=sys.stderr, flush=True)
    finally:
        if peer.socket and peer.state:
            try:
                peer.send((0., 0., 0.))
            except (OSError, ValueError):
                pass
        if peer.state:
            report["final_state"] = peer.state
        report["maximum_actual_planar_excursion_m"] = peer.max_excursion_m
        peer.close()
        trace.close()
        report["trace_sha256"] = sha(args.output / "trace.jsonl")
        save()
    return 0 if report["status"] == "passed" else 3


if __name__ == "__main__":
    raise SystemExit(main())
