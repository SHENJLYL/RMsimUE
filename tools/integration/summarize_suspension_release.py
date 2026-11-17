"""Build an evidence summary only after frozen-profile native regression has passed."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def summarize(base: Path, candidate: str, regression: str) -> dict:
    root = Path(__file__).resolve().parents[2]
    production = root / "assets/vehicles/aim_sentry/drive-profile.json"
    current = read(production)
    before = read(base / "before/drive-profile.json")
    changed = [f"{group}.{key}" for group in ("control", "physics") for key in before[group]
               if before[group][key] != current[group][key]]
    require(sorted(changed) == ["physics.SpringDampingNsPerM", "physics.SpringStiffnessNPerM"], "Unexpected production parameter changes")
    module_hash = sha(root / "unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll")
    profile_hash = sha(production)
    rig_hash = sha(root / "assets/vehicles/aim_sentry/ue_mesh_rig.json")
    require(rig_hash == sha(base / "before/ue_mesh_rig.json"), "Vehicle binding changed")
    require(profile_hash == sha(base / "profiles" / candidate / "drive-profile.json"), "Selected profile is not the production profile")
    run_root = base / regression
    gate = read(run_root / "verification.json")
    require(gate["status"] == "passed", "Sequential regression did not pass")
    require(gate["drive_profile_sha256"].lower() == profile_hash and gate["module_sha256"].lower() == module_hash, "Regression is not the current runtime")
    paths = [("traction", 10), ("controls/default", 19), ("waves/wave_red", 5),
             ("waves/wave_blue", 5), ("integration", 10), ("fast", 5), ("spin", 6),
             ("ros/ros", 11), ("lk/lk", 21)]
    verified = []
    captures = 0
    for relative, expected in paths:
        path = run_root / relative / "verification.json"
        result = read(path)
        require(result.get("status") == "passed" or result.get("passed") is True, f"Failed run {relative}")
        checks = result["checks"]
        count = len(checks) if isinstance(checks, list) else checks
        require(count == expected, f"Check count mismatch in {relative}")
        if isinstance(checks, list):
            require(all(item["passed"] for item in checks), f"Failed check in {relative}")
        if relative.startswith(("ros/", "lk/")):
            provenance = read(run_root / relative.split("/")[0] / "runtime-provenance.json")
        else:
            provenance = result
        require(provenance["module_sha256"].lower() == module_hash and provenance["drive_profile_sha256"].lower() == profile_hash,
                f"Input hash mismatch in {relative}")
        images = result.get("decoded_captures", 0)
        captures += images
        verified.append({"path": path.relative_to(base).as_posix(), "checks": count, "decoded_pngs": images,
                         "status": "passed", "verification_sha256": sha(path)})
    responses = []
    for repeat in ("01", "02"):
        path = base / f"{candidate}-response-{repeat}" / "verification.json"
        result = read(path)
        require(result["status"] == "passed", f"Response repeat {repeat} did not pass")
        hashes = result["provenance"]["hashes"]
        require(hashes["module"].lower() == module_hash and hashes["profile"].lower() == profile_hash, "Response uses different inputs")
        responses.append({"path": path.relative_to(base).as_posix(), "native_checks": result["native_checks"],
                          "decoded_pngs": result["decoded_pngs"], "metrics": result["metrics"], "verification_sha256": sha(path)})
    require(responses[0]["metrics"] == responses[1]["metrics"], "Response repeats differ; investigate before claiming repeatability")
    original = read(base / "baseline-01/verification.json")
    offline = read(base / "offline/verification.json")
    require(offline["status"] == "passed", "Offline checks failed")
    rejects = []
    for path in sorted(base.rglob("control-result.json")):
        result = read(path)
        verification = path.parent / "verification.json"
        analyzed = read(verification) if verification.exists() else {}
        if result.get("status") == "failed" or analyzed.get("status") == "failed":
            rejects.append({"path": path.relative_to(base).as_posix(), "native_status": result.get("status"),
                            "analysis_status": analyzed.get("status"),
                            "reason": result.get("failure_reason") or analyzed.get("analysis_failure"), "raw_sha256": sha(path)})
    return {"status": "native_windows_verified", "scope": "AIM suspension tuning, native transient and existing contact/control/ROS/LK regression",
            "candidate": candidate, "module_sha256": module_hash, "drive_profile_sha256": profile_hash, "rig_sha256": rig_hash,
            "execution_scope": gate["scope"], "execution_interruption": gate.get("execution_interruption"),
            "changed_parameters": {key: {"before": before["physics"][key.split(".")[1]], "after": current["physics"][key.split(".")[1]]} for key in changed},
            "regression_checks": sum(x["checks"] for x in verified), "regression_pngs": captures, "regression_runs": verified,
            "response_runs": responses, "original_response": {"path": "baseline-01/verification.json", "status": original["status"], "metrics": original["metrics"]},
            "offline": offline, "rejected_or_failed_experiments": rejects,
            "preserved": ["mass", "preload", "travel", "source tyre hulls", "all drive/steering gains", "friction", "natural sleep and collision semantics"],
            "not_verified_this_release": ["OS keyboard end-to-end latency and user feel", "native Ubuntu", "real-robot dynamics calibration", "Point-LIO/Nav2 under the new suspension", "full sentry/referee/multiplayer"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence_root", type=Path)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--regression", required=True)
    args = parser.parse_args()
    try:
        report = summarize(args.evidence_root.resolve(), args.candidate, args.regression)
    except (OSError, KeyError, ValueError, TypeError) as exc:
        print(f"Release summary rejected: {exc}")
        return 2
    (args.evidence_root / "release-summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "candidate", "regression_checks", "regression_pngs", "changed_parameters")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
