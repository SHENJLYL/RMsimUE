"""Run importer regression and export examples; does not run a simulator."""

import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from datetime import datetime, timezone


def main():
    root = Path(__file__).resolve().parents[2]
    destination = root / "artifacts" / "vehicles"
    destination.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    env["PYTHONUTF8"] = "1"
    commands = [[sys.executable, "-m", "unittest", "discover", "-s", "tests/vehicle_tools", "-v"]]
    for name in ("sentry_omni", "training_diff"):
        package = str(root / "assets" / "vehicles" / name)
        commands += [
            [sys.executable, "-m", "rm_vehicle_tools", "validate", package],
            [sys.executable, "-m", "rm_vehicle_tools", "export", package,
             "--output", str(destination / (name + ".canonical.json"))],
        ]
    evidence = {"scope": "vehicle_importer_only", "platform": platform.platform(),
                "python": sys.version, "utc": datetime.now(timezone.utc).isoformat(), "commands": []}
    success = True
    for command in commands:
        result = subprocess.run(command, cwd=root, env=env, text=True, encoding="utf-8",
                                errors="replace", capture_output=True, timeout=120)
        evidence["commands"].append({"argv": command, "returncode": result.returncode,
                                      "stdout": result.stdout, "stderr": result.stderr})
        print(result.stdout, end="")
        if result.returncode:
            print(result.stderr, file=sys.stderr, end="")
            success = False
            break
    evidence["passed"] = success
    (destination / "verification.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Importer verification: " + ("PASS" if success else "FAIL"))
    print("This result does not verify Unreal, physics, sensors, ROS integration or multiplayer.")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
