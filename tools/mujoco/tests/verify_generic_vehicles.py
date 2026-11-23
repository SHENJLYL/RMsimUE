"""End-to-end native UE5 + MuJoCo + TCP bridge verification for generic URDF vehicles.

Verifies that heterogeneous vehicles (differential_drive and holonomic) run in
UE5 + MuJoCo without any vehicle-specific C++ changes, responding to real
velocity commands over the standard loopback bridge protocol.
"""
from __future__ import annotations

import json
import math
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "ros2_ws/src/rmsim_bridge"))
from rmsim_bridge.protocol import PacketReader, command_packet  # noqa: E402

UE_EDITOR = Path(r"E:\simulator-tools\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe")
UPROJECT = REPO / "unreal/RMSim/RMSim.uproject"
MAP_FILE = REPO / "assets/maps/import_lab/map.json"


def quat_to_yaw(qxyzw) -> float:
    x, y, z, w = qxyzw
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def read_state_packet(sock: socket.socket, reader: PacketReader, queue: list, timeout: float = 5.0) -> dict:
    sock.settimeout(timeout)
    deadline = time.monotonic() + timeout
    while True:
        if queue:
            state, _binary = queue.pop(0)
            return state
        if time.monotonic() > deadline:
            raise TimeoutError("Timed out waiting for simulator state packet")
        chunk = sock.recv(65536)
        if not chunk:
            raise EOFError("Bridge closed")
        queue.extend(reader.feed(chunk))


def send_cmd(sock: socket.socket, state: dict, seq: int, vx: float, vy: float, wz: float):
    robot_id = state["robot_id"]
    raw = command_packet(
        state,
        seq,
        state["sim_time_ns"],
        f"{robot_id}/base_link",
        [vx, vy, 0.0, 0.0, 0.0, wz],
        robot_id,
    )
    sock.sendall(raw)


def run_vehicle_test(vehicle_id: str, port: int, test_lateral: bool) -> dict:
    vehicle_dir = REPO / "assets/vehicles" / vehicle_id
    canonical = vehicle_dir / "vehicle.canonical.json"
    composed_dir = REPO / "artifacts/mujoco/composed" / f"{vehicle_id}__import_lab"
    manifest = composed_dir / "manifest.json"
    if not manifest.is_file():
        subprocess.run(
            [
                sys.executable,
                str(REPO / "tools/mujoco/compose_scene.py"),
                "--repo",
                str(REPO),
                "--vehicle",
                str(canonical),
                "--map",
                str(MAP_FILE),
                "--output",
                str(composed_dir),
                "--overwrite",
            ],
            check=True,
        )

    ue_args = [
        str(UE_EDITOR),
        str(UPROJECT),
        "/Engine/Maps/Templates/Template_Default",
        "-game",
        "-RenderOffScreen",
        "-ResX=320",
        "-ResY=240",
        "-nosplash",
        "-NoSound",
        f"-VehicleFile={canonical}",
        f"-MapFile={MAP_FILE}",
        "-SpawnId=default",
        "-ContactPhysics",
        "-PhysicsBackend=mujoco",
        f"-MujocoModel={manifest}",
        "-SimStepSeconds=0.005",
        "-SimRealTimePace",
        "-RosBridge",
        "-RosControl",
        f"-RosPort={port}",
        "-RosCameraHz=0",
    ]
    proc = subprocess.Popen(ue_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    sock = None
    try:
        # Wait for UE bridge TCP listener
        deadline = time.monotonic() + 90.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read().decode("utf-8", errors="replace")[-1000:]
                err = proc.stderr.read().decode("utf-8", errors="replace")[-1000:]
                raise RuntimeError(f"UE exited early ({proc.returncode}): {out}\n{err}")
            try:
                sock = socket.create_connection(("127.0.0.1", port), timeout=1.0)
                break
            except OSError:
                time.sleep(0.3)
        if sock is None:
            raise TimeoutError(f"UE bridge did not listen on port {port} within 90s")

        reader = PacketReader()
        queue: list = []
        st0 = read_state_packet(sock, reader, queue, timeout=15.0)
        assert st0["robot_id"] == vehicle_id, f"Expected robot_id={vehicle_id}, got {st0['robot_id']}"
        p0 = st0["pose"]["position_m"]
        seq = 1
        cur = st0

        # Phase 1: Forward drive for ~1.2s sim time
        t_start = cur["sim_time_ns"]
        while (cur["sim_time_ns"] - t_start) < 1_200_000_000:
            send_cmd(sock, cur, seq, 0.9, 0.0, 0.0)
            seq += 1
            cur = read_state_packet(sock, reader, queue, timeout=5.0)
        p_fwd = cur["pose"]["position_m"]
        dx_fwd = p_fwd[0] - p0[0]

        # Phase 2: Lateral drive (for holonomic)
        dy_lat = 0.0
        if test_lateral:
            t_start = cur["sim_time_ns"]
            p_lat0 = cur["pose"]["position_m"]
            while (cur["sim_time_ns"] - t_start) < 1_200_000_000:
                send_cmd(sock, cur, seq, 0.0, 0.9, 0.0)
                seq += 1
                cur = read_state_packet(sock, reader, queue, timeout=5.0)
            dy_lat = cur["pose"]["position_m"][1] - p_lat0[1]

        # Phase 3: Yaw rotation for ~1.0s sim time
        t_start = cur["sim_time_ns"]
        yaw_before = quat_to_yaw(cur["pose"]["rotation_xyzw"])
        while (cur["sim_time_ns"] - t_start) < 1_000_000_000:
            send_cmd(sock, cur, seq, 0.0, 0.0, 1.2)
            seq += 1
            cur = read_state_packet(sock, reader, queue, timeout=5.0)
        yaw_after = quat_to_yaw(cur["pose"]["rotation_xyzw"])
        dyaw = math.atan2(math.sin(yaw_after - yaw_before), math.cos(yaw_after - yaw_before))

        passed = (dx_fwd > 0.25) and (dyaw > 0.25) and (not test_lateral or dy_lat > 0.25)
        result = {
            "vehicle_id": vehicle_id,
            "robot_id": cur["robot_id"],
            "sim_time_s": cur["sim_time_ns"] / 1e9,
            "dx_forward_m": round(dx_fwd, 4),
            "dy_lateral_m": round(dy_lat, 4),
            "dyaw_rad": round(dyaw, 4),
            "passed": passed,
        }
        return result
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def main() -> int:
    results = []
    results.append(run_vehicle_test("training_diff", port=9461, test_lateral=False))
    results.append(run_vehicle_test("sentry_omni", port=9462, test_lateral=True))
    print(json.dumps({"results": results, "all_passed": all(r["passed"] for r in results)}, indent=2))
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
