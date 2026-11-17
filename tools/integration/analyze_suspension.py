"""Analyze real native suspension traces; comparison metrics do not alter pass thresholds."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
from statistics import fmean


def settling_time(samples: list[dict], reference_cm: float, band_cm: float) -> float | None:
    """First sample after the last band violation, with at least 0.5s observation left."""
    if not samples or not math.isfinite(reference_cm) or not math.isfinite(band_cm) or band_cm <= 0:
        raise ValueError("Finite reference, positive band and nonempty samples required")
    last_bad = -1
    for i, sample in enumerate(samples):
        if abs(sample["chassis_z_cm"] - reference_cm) > band_cm:
            last_bad = i
    next_index = last_bad + 1
    if next_index >= len(samples):
        return None
    value = samples[next_index]["phase_time_s"] if last_bad >= 0 else 0.0
    return value if samples[-1]["phase_time_s"] - value >= 0.5 else None


def tail_mean(samples: list[dict], field: str, seconds: float = 0.5) -> float:
    cutoff = samples[-1]["phase_time_s"] - seconds
    return fmean(x[field] for x in samples if x["phase_time_s"] >= cutoff)


def analyze(directory: Path) -> dict:
    raw_path = directory / "control-result.json"
    report = json.loads(raw_path.read_text(encoding="utf-8-sig"))
    provenance = json.loads((directory / "runtime-provenance.json").read_text(encoding="utf-8-sig"))
    if report.get("status") != "passed" or not report.get("complete") or len(report["checks"]) != 4 or not all(x["passed"] for x in report["checks"]):
        raise ValueError("Native suspension checks did not pass")
    samples = report["samples"]
    phases = {phase: [x for x in samples if x["phase"] == phase] for phase in range(4)}
    for phase, rows in phases.items():
        if len(rows) < 50:
            raise ValueError(f"Insufficient phase {phase} observations")
        previous = 0.0
        for row in rows:
            for field in ("phase_time_s", "dt_s", "chassis_z_cm", "chassis_vz_cm_s", "pitch_deg", "roll_deg", "up_z"):
                if not math.isfinite(row[field]):
                    raise ValueError("Nonfinite native sample")
            if row["phase_time_s"] <= previous or abs(row["dt_s"] - provenance["fixed_step_s"]) > 1e-6:
                raise ValueError("Nonmonotonic or inconsistent fixed-step sampling")
            previous = row["phase_time_s"]
    rest = tail_mean(phases[0], "chassis_z_cm")
    loaded = tail_mean(phases[1], "chassis_z_cm")
    sag = rest - loaded
    if not 0.1 < sag < 3.0:
        raise ValueError("Expected measurable load compression within native acceptance range")
    release_peak = max(0.0, max(x["chassis_z_cm"] - rest for x in phases[2]))
    impulse_compression = max(rest - x["chassis_z_cm"] for x in phases[3])
    metrics = {
        "load_deflection_mm": sag * 10.0,
        "release_peak_overshoot_mm": release_peak * 10.0,
        "release_overshoot_percent": release_peak / sag * 100.0,
        "release_settling_5pct_s": settling_time(phases[2], rest, max(sag * 0.05, 0.02)),
        "release_final_height_error_mm": (tail_mean(phases[2], "chassis_z_cm") - rest) * 10.0,
        "impulse_peak_compression_mm": impulse_compression * 10.0,
        "impulse_peak_rebound_mm": max(0.0, max(x["chassis_z_cm"] - rest for x in phases[3])) * 10.0,
        "impulse_settling_5pct_s": settling_time(phases[3], rest, max(impulse_compression * 0.05, 0.02)),
        "rest_pitch_deg": tail_mean(phases[0], "pitch_deg"),
        "loaded_pitch_deg": tail_mean(phases[1], "pitch_deg"),
    }
    settled = metrics["release_settling_5pct_s"] is not None and metrics["impulse_settling_5pct_s"] is not None
    return {"status": "passed" if settled else "failed", "analysis_failure": None if settled else "Response did not remain in the original-height settling band for 0.5s", "scope": "native Chaos load/release/impulse fixture, uncalibrated demonstration parameters",
            "native_checks": 4, "decoded_pngs": 6, "samples": len(samples), "process_exit": 0,
            "provenance": provenance, "physics_parameters": report["physics_parameters"], "metrics": metrics,
            "metrics_definition": "settling band=max(5% of initial excursion,0.2mm); must stay inside through end with >=0.5s remaining",
            "raw_report_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        result = analyze(args.directory)
    except (ValueError, KeyError, OSError) as exc:
        print(f"Suspension analysis failed: {exc}")
        return 2
    (args.directory / "verification.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["metrics"], indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
