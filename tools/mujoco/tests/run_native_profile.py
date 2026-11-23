"""Record official MuJoCo timer breakdown and wall-clock step distributions."""
import argparse
import hashlib
import json
import platform
from pathlib import Path
import subprocess
import time


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("executable", "library", "model", "evidence"):
        parser.add_argument("--" + key, required=True, type=Path)
    parser.add_argument("--expected-model-sha256", required=True)
    parser.add_argument("--steps", type=int, default=3000)
    parser.add_argument("--warmup", type=int, default=500)
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--compare-threads", action="store_true")
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=False)
    paths = [args.executable, args.library, args.model, Path(__file__).resolve(),
             Path(__file__).with_name("native_profile.cpp"),
             Path(__file__).with_name("native_compare.cpp")]
    record = {"schema": "rmsim_native_profile_v1", "platform": platform.platform(),
              "inputs": {str(p.resolve()): sha(p) for p in paths},
              "started_unix": time.time(), "checks": [],
              "timing_scope": "unpaced native C API; CPU and OS scheduling affect wall time"}
    assert sha(args.model) == args.expected_model_sha256, "frozen model hash mismatch"
    if platform.system() == "Windows":
        snapshot = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
            "Get-Process -Name UnrealEditor,MSBuild,cl,link -ErrorAction SilentlyContinue | "
            "Select-Object Id,ProcessName,CPU | ConvertTo-Json -Compress"],
            capture_output=True, text=True)
        record["competing_process_snapshot"] = snapshot.stdout.strip()
    for mode in (["profile", "compare_threads"] if args.compare_threads else ["profile"]):
        command = [str(args.executable.resolve()), mode, str(args.library.resolve()),
                   str(args.model.resolve()), str(args.steps), str(args.warmup)]
        if args.workers >= 0:
            command.append(str(args.workers))
        print("START", mode, flush=True)
        start = time.perf_counter()
        result = subprocess.run(command, capture_output=True, text=True, timeout=1200)
        (args.evidence / (mode + ".stdout.json")).write_text(result.stdout, encoding="utf-8")
        (args.evidence / (mode + ".stderr.txt")).write_text(result.stderr, encoding="utf-8")
        item = {"command": command, "exit_code": result.returncode,
                "elapsed_seconds": time.perf_counter() - start}
        record["checks"].append(item)
        if result.returncode == 0:
            item["result"] = json.loads(result.stdout)
        record["passed"] = all(c["exit_code"] == 0 for c in record["checks"])
        record["model_sha256_after"] = sha(args.model)
        assert record["model_sha256_after"] == args.expected_model_sha256
        (args.evidence / "verification.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        if result.returncode:
            print(result.stderr, flush=True)
            raise SystemExit(result.returncode)
        for run in item["result"]["runs"]:
            summary = {k: v for k, v in run.items() if k not in ("fixed_collision", "dynamic")}
            if "dynamic" in run:
                summary["broad_ms"] = run["dynamic"]["timers"]["broadphase"]["mean_ms"]
                summary["mid_narrow_ms"] = run["dynamic"]["timers"]["midphase_plus_narrowphase"]["mean_ms"]
            print(json.dumps(summary), flush=True)
    print("ALL_PASSED", flush=True)


if __name__ == "__main__":
    main()
