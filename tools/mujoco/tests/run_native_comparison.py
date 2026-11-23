"""Native MuJoCo reference/candidate checks; writes each attempt to a new evidence directory."""
import argparse
import hashlib
import json
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--official-library", required=True, type=Path)
    parser.add_argument("--custom-library", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--phase", choices=["fixtures", "full", "all"], default="fixtures")
    parser.add_argument("--full-model", type=Path)
    parser.add_argument("--expected-model-sha256")
    parser.add_argument("--full-steps", type=int, default=500)
    parser.add_argument("--benchmark-steps", type=int, default=25)
    parser.add_argument("--benchmark-warmup", type=int, default=100)
    args = parser.parse_args()
    for path in [args.executable, args.official_library, args.custom_library]:
        if not path.is_file():
            parser.error(f"missing required file: {path}")
    if args.evidence.exists() and any(args.evidence.iterdir()):
        parser.error("evidence directory must be new or empty")
    args.evidence.mkdir(parents=True, exist_ok=True)
    report = {"schema": "rmsim_mujoco_native_reference_v1", "platform": platform.platform(),
              "machine": platform.machine(), "python": sys.version, "checks": [],
              "inputs": {str(p.resolve()): sha(p) for p in
                         [args.executable, args.official_library, args.custom_library,
                          HERE / "native_compare.cpp", Path(__file__).resolve()]},
              "comparison_absolute_tolerance": 1e-12,
              "physics_acceptance_scope": "native C API equivalence; UE integration and vehicle calibration are separate"}

    def save():
        report["passed"] = bool(report["checks"]) and all(c["passed"] for c in report["checks"])
        (args.evidence / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    def run(name, mode, model, steps, library=None, warmup=None):
        out = args.evidence / name
        out.mkdir()
        raw = library or args.official_library
        candidate = library or args.custom_library
        command = [str(args.executable.resolve()), mode, str(raw.resolve()),
                   str(candidate.resolve()), str(model.resolve()), str(steps)]
        if warmup is not None:
            command.append(str(warmup))
        before = sha(model)
        record = {"name": name, "command": command, "model_sha256_before": before,
                  "started_unix": time.time(), "passed": False}
        print(f"START {name}", flush=True)
        try:
            result = subprocess.run(command, cwd=out, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=1200)
            (out / "stdout.log").write_text(result.stdout, encoding="utf-8")
            (out / "stderr.log").write_text(result.stderr, encoding="utf-8")
            record["exit_code"] = result.returncode
            lines = [s for s in result.stdout.splitlines() if s.startswith("{")]
            if lines:
                record["result"] = json.loads(lines[-1])
            record["model_sha256_after"] = sha(model)
            record["passed"] = (result.returncode == 0 and bool(lines) and
                                before == record["model_sha256_after"] and
                                record["result"].get("passed", mode == "bench"))
        except Exception as error:
            record["error"] = repr(error)
        record["elapsed_seconds"] = time.time() - record["started_unix"]
        report["checks"].append(record)
        (out / "execution.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        save()
        print(f"END {name} passed={record['passed']} elapsed={record['elapsed_seconds']:.3f}s", flush=True)

    if args.phase in ["fixtures", "all"]:
        cases = args.evidence / "cases"
        cases.mkdir()
        names = ["channel_convex_mesh", "wing_convex_mesh", "channel_sphere", "wing_sphere",
                 "open_hole", "moving_rigid_flex", "initial_midphase_disabled"]
        for name in names:
            shutil.copy2(HERE / "fixtures" / f"{name}.xml", cases / f"{name}.xml")
        subprocess.run([sys.executable, str(HERE / "generate_grid.py"), "64", str(cases / "grid_64.xml")], check=True)
        extra = ('<body name="second" pos=".35 .6 .35"><freejoint/><geom type="sphere" size=".04" mass="1"/></body>'
                 '<body name="third" pos="-.55 .15 .65"><freejoint/><geom type="sphere" size=".06" mass="1"/></body>')
        (cases / "rotating_sap.xml").write_text((cases / "grid_64.xml").read_text().replace(
            "</worldbody>", extra + "</worldbody>"), encoding="utf-8")
        for name in names + ["grid_64", "rotating_sap"]:
            run(name, "compare", cases / f"{name}.xml", 2000)

    if args.phase in ["full", "all"]:
        if not args.full_model or not args.full_model.is_file():
            parser.error("full phase requires --full-model")
        model_hash = sha(args.full_model)
        if args.expected_model_sha256 and model_hash != args.expected_model_sha256:
            parser.error("full model hash differs from the expected frozen model")
        report["full_model_initialization"] = "unmodified model qpos0, zero ctrl with the model's existing affine biases"
        report["full_model_geometry_comparison"] = "all geometry arrays at first and final state; dynamics and contacts every step"
        run("rmuc_equivalence", "compare_model", args.full_model, args.full_steps)
        for name, library in [("rmuc_official_benchmark", args.official_library),
                              ("rmuc_custom_benchmark", args.custom_library)]:
            run(name, "bench", args.full_model, args.benchmark_steps, library, args.benchmark_warmup)
    save()
    print(json.dumps({"passed": report["passed"], "checks": len(report["checks"]),
                      "evidence": str(args.evidence.resolve())}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
