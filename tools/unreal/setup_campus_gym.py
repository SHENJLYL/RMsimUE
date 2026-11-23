"""Prepare and import the campus gym into an existing native UE project.

Downloads are pinned and the preparation script reuses verified cached files.
The source preparer currently has no offline switch. This wrapper does not
build the project, launch a game, or edit the map's arena-shell.json selector.
Windows and Linux executables are selected for the host running this script;
native import evidence is recorded only for that host.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_report(path: Path, report: dict) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_stage(name: str, command: list[str], expected_report: Path,
              report_dir: Path, working_dir: Path, timeout: int, report: dict) -> None:
    stage = {
        "name": name, "status": "running", "command": command,
        "cwd": str(working_dir), "report": str(expected_report),
        "stdout_log": str(report_dir / (name + ".stdout.log")),
    }
    report["stages"].append(stage)
    summary = report_dir / "verification.json"
    save_report(summary, report)
    started = time.monotonic()
    try:
        with Path(stage["stdout_log"]).open("wb") as output:
            completed = subprocess.run(command, cwd=working_dir,
                stdout=output, stderr=subprocess.STDOUT, timeout=timeout, check=False)
        stage["exit_code"] = completed.returncode
        if not expected_report.is_file():
            raise RuntimeError(f"{name}: expected report was not written; see {stage['stdout_log']}")
        data = json.loads(expected_report.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError(f"{name}: report must be a JSON object")
        stage["report_status"] = data.get("status")
        stage["report_sha256"] = digest(expected_report)
        if completed.returncode != 0 or data.get("status") != "passed":
            raise RuntimeError(f"{name}: exit={completed.returncode}, report={data.get('status')}; see {expected_report}")
        stage["status"] = "passed"
    except (Exception, KeyboardInterrupt) as error:
        stage["status"] = "failed"
        stage["error"] = str(error) or type(error).__name__
        if isinstance(error, subprocess.TimeoutExpired):
            stage["timeout_seconds"] = timeout
        raise
    finally:
        stage["elapsed_seconds"] = time.monotonic() - started
        save_report(summary, report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-root", type=Path, required=True,
                        help="UE installation root containing the Engine directory")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--project", type=Path,
                        help="Existing .uproject; defaults to <repo-root>/unreal/RMSim/RMSim.uproject")
    parser.add_argument("--report-dir", type=Path, required=True,
                        help="Fresh evidence directory; it must not already exist")
    args = parser.parse_args()
    root = args.repo_root.resolve()
    engine_root = args.engine_root.resolve()
    project = (args.project or root / "unreal/RMSim/RMSim.uproject").resolve()
    report_dir = args.report_dir.resolve()
    try:
        report_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error("--report-dir already exists; choose a new directory")
    report = {
        "schema": "rmsim_campus_gym_setup_v1", "status": "running",
        "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "platform": sys.platform, "python": sys.version,
        "repo_root": str(root), "engine_root": str(engine_root), "project": str(project),
        "scope": "Pinned source preparation, deterministic layout and native UE asset import; gameplay and final rendering require separate acceptance.",
        "stages": [],
    }
    summary = report_dir / "verification.json"
    started = time.monotonic()
    try:
        if sys.platform == "win32":
            editor = engine_root / "Engine/Binaries/Win64/UnrealEditor-Cmd.exe"
        elif sys.platform.startswith("linux"):
            editor = engine_root / "Engine/Binaries/Linux/UnrealEditor-Cmd"
        else:
            raise RuntimeError("Supported native hosts are Windows and Linux")
        prepare = root / "tools/maps/prepare_campus_gym_assets.py"
        layout = root / "tools/maps/build_campus_gym_layout.py"
        importer = root / "tools/unreal/import_campus_gym_assets.py"
        for label, path in (("UE commandlet executable", editor), ("UE project", project),
                            ("source preparer", prepare), ("layout generator", layout),
                            ("native asset importer", importer)):
            if not path.is_file():
                raise FileNotFoundError(f"Missing {label}: {path}")
        if project.suffix.lower() != ".uproject":
            raise ValueError("--project must point to an existing .uproject file")
        report["scripts_sha256"] = {
            str(path.relative_to(root)): digest(path) for path in (prepare, layout, importer)
        }
        asset_root = root / "assets/maps/rmuc2027/arena/campus-gym"
        run_stage("prepare", [sys.executable, str(prepare),
            "--asset-root", str(asset_root), "--cache", str(root / "artifacts/cache/campus-gym"),
            "--report", str(report_dir / "prepare.json")],
            report_dir / "prepare.json", report_dir, root, 900, report)
        run_stage("layout", [sys.executable, str(layout),
            "--output", str(asset_root / "layout.json"),
            "--summary", str(report_dir / "layout.json")],
            report_dir / "layout.json", report_dir, root, 60, report)
        native_report = report_dir / "native-import.json"
        run_stage("native-import", [str(editor), str(project), "-run=pythonscript",
            "-script=" + str(importer), "-GymAssetRoot=" + str(asset_root),
            "-GymImportReport=" + str(native_report), "-NullRHI", "-unattended",
            "-nosplash", "-nosound", "-NoShaderWorker",
            "-abslog=" + str(report_dir / "native-runtime.log")],
            native_report, report_dir, project.parent, 900, report)
        report["source_manifest_sha256"] = digest(asset_root / "SOURCE.json")
        report["layout_sha256"] = digest(asset_root / "layout.json")
        report["status"] = "passed"
    except (Exception, KeyboardInterrupt) as error:
        report["status"] = "failed"
        report["error"] = str(error) or type(error).__name__
    finally:
        report["elapsed_seconds"] = time.monotonic() - started
        report["finished_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        save_report(summary, report)
    print(json.dumps({"status": report["status"], "verification": str(summary),
                      "error": report.get("error")}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
