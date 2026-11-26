"""End-to-end offline verification of sp_vision_25 on real UE5 1280x720 aim_camera frames."""
from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(r"D:\simulator")
sys.path.insert(0, str(ROOT / "ros2_ws/src/sp_vision_25"))
sys.path.insert(0, str(ROOT / "ros2_ws/install_control/sp_msgs/Lib/site-packages"))
sys.path.insert(0, str(ROOT / "ros2_ws/install_control/pb_rm_interfaces/Lib/site-packages"))
_DLLS = [
    os.add_dll_directory(str(ROOT / "ros2_ws/install_control/sp_msgs/bin")),
    os.add_dll_directory(str(ROOT / "ros2_ws/install_control/pb_rm_interfaces/bin")),
]
from sp_msgs.msg import AutoAimCommandMsg  # noqa: E402

from sp_vision_25.detector import SpVisionYoloV8Detector  # noqa: E402
from sp_vision_25.solver_tracker import BallisticAimer, Kalman3DTracker, solve_armor_pnp  # noqa: E402


def main() -> int:
    frames_dir = ROOT / "artifacts/unreal/armor-camera-20261125-neutral"
    det_ref = json.loads((frames_dir / "detection/detections.json").read_text(encoding="utf-8"))
    assets_dir = ROOT / "ros2_ws/src/sp_vision_25/assets"

    detector = SpVisionYoloV8Detector(assets_dir, min_classify_conf=0.72)
    aimer = BallisticAimer(muzzle_speed_mps=24.5)
    K = np.array([[1108.51, 0.0, 640.0], [0.0, 1108.51, 360.0], [0.0, 0.0, 1.0]], dtype=np.float64)

    total = 0
    passed = 0
    sentry_total = 0
    sentry_passed = 0
    t0 = time.perf_counter()

    for fname in sorted(det_ref["frames"].keys()):
        img_path = frames_dir / fname
        if not img_path.is_file():
            continue
        bgr = cv2.imread(str(img_path))
        if bgr is None:
            continue
        total += 1
        is_sentry = fname.startswith("sentry-")
        if is_sentry:
            sentry_total += 1
        exp_color = "blue" if ("-blue-" in fname or "by-blue" in fname) else "red"
        exp_name = "sentry" if is_sentry else "outpost"

        dets = detector.detect(bgr)
        matched = [d for d in dets if d.color == exp_color and d.name == exp_name]
        if matched:
            best = max(matched, key=lambda d: d.confidence)
            p_cam = solve_armor_pnp(best.keypoints, best.armor_type, K)
            tracker = Kalman3DTracker()
            tracker.update(p_cam, 0.0)
            tr = tracker.update(p_cam, 0.033)
            assert p_cam is not None and tr is not None
            sol = aimer.solve(tr[0], tr[1], 0.0, 0.0, tr[2])
            cmd = AutoAimCommandMsg()
            cmd.control = bool(sol.locked)
            cmd.shoot = bool(sol.shoot)
            cmd.yaw = float(sol.target_yaw)
            cmd.pitch = float(sol.target_pitch)
            assert math.isfinite(cmd.yaw) and math.isfinite(cmd.pitch)
            passed += 1
            if is_sentry:
                sentry_passed += 1
            print(
                f"OK   {fname:<34} | {best.color}/{best.name} conf={best.confidence:.2f} "
                f"| dist={sol.distance_m:.2f}m tof={sol.tof_s*1000:.0f}ms "
                f"| yaw={math.degrees(cmd.yaw):+.2f}deg pitch={math.degrees(cmd.pitch):+.2f}deg shoot={cmd.shoot}"
            )
        else:
            print(f"MISS {fname:<34} | got={[(d.color, d.name, round(d.confidence,2)) for d in dets]}")

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    print(f"\nSUMMARY: total={passed}/{total} ({passed/max(1,total)*100:.1f}%), sentry={sentry_passed}/{sentry_total} ({sentry_passed/max(1,sentry_total)*100:.1f}%), avg={elapsed_ms/max(1,total):.1f} ms/frame, sp_msgs=OK")
    return 0 if sentry_passed == sentry_total and passed >= 15 else 1


if __name__ == "__main__":
    raise SystemExit(main())
