"""Strict provenance for the separate, validated MuJoCo display-only runtime."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile


ENVIRONMENT_RELATIVE = Path("artifacts/environments/mujoco-4.0.1-viewer-staticflex-vbo.1")
RECEIPT_NAME = "rmsim-viewer-runtime.json"
PATCH_SHA256 = "6fde8a8010410f5ae0a864f049a74a516c84dff2c60c388ff2b3aabe166d1490"
UPSTREAM_COMMIT = "4ed093a9f06b170bd1564942fa37e1bb907d93e3"
EXTENSION = "rmsim.viewer-static-flex-vbo.1"
SOURCE_FILES = ("tools/mujoco/live_viewer.py", "tools/mujoco/viewer_protocol.py",
                "tools/mujoco/viewer_runtime.py")


class ViewerRuntimeError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ViewerRuntimeError("Optimized MuJoCo viewer runtime: " + message)


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    _require(isinstance(value, dict), "expected an object in " + str(path))
    return value


def _path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    _require(path.is_relative_to(root), "a sealed path leaves the repository")
    return path


def _reference(root: Path, value: dict) -> tuple[Path, dict]:
    path = _path(root, value["path"])
    _require(sha256_file(path) == value["sha256"], "evidence hash mismatch: " + str(path))
    return path, _json(path)


def validate_optimized_viewer_runtime(repo_root: Path, viewer_script: Path) -> dict:
    """Validate files only. This function imports no MuJoCo, GLFW or ROS module."""
    try:
        return _validate(Path(repo_root).resolve(), Path(viewer_script).resolve())
    except ViewerRuntimeError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ViewerRuntimeError("Optimized MuJoCo viewer runtime is missing or inconsistent: " + str(error)) from error


def _validate(root: Path, viewer_script: Path, receipt_path: Path | None = None) -> dict:
    _require(viewer_script == root / SOURCE_FILES[0], "unexpected viewer source path")
    environment = root / ENVIRONMENT_RELATIVE
    receipt_path = receipt_path or environment / RECEIPT_NAME
    receipt = _json(receipt_path)
    _require(receipt.get("schema") == "rmsim_optimized_viewer_runtime_v1" and
             receipt.get("status") == "validated", "display runtime has no completed GL validation receipt")
    _require(receipt.get("patch_sha256") == PATCH_SHA256 and
             receipt.get("renderer_extension") == EXTENSION, "unexpected renderer extension/patch")
    sources = receipt["source_sha256"]
    _require(set(sources) == set(SOURCE_FILES), "source binding is incomplete")
    for name in SOURCE_FILES:
        _require(sha256_file(root / name) == sources[name], "source hash mismatch: " + name)
    python = environment / "Scripts/python.exe"
    dll = environment / "Lib/site-packages/mujoco/mujoco.dll"
    _require(sha256_file(python) == receipt["viewer_python_sha256"], "display Python identity mismatch")
    dll_sha256 = sha256_file(dll)
    _require(dll_sha256 == receipt["viewer_dll_sha256"], "display DLL identity mismatch")
    build_path, build = _reference(root, receipt["build_manifest"])
    _require(build.get("schema") == "rmsim_viewer_renderer_build_v1" and
             build.get("status") == "build_and_import_pass_gl_equivalence_pending",
             "build/import provenance is incomplete")
    _require(build.get("upstream_commit") == UPSTREAM_COMMIT and
             build.get("patch_sha256") == PATCH_SHA256 and
             build.get("renderer_extension") == EXTENSION, "build source identity mismatch")
    _require(build.get("public_abi_layout_changed") is False and
             build.get("solver_source_changed") is False, "renderer build changes public ABI or solver source")
    _require(Path(build["viewer_python"]).resolve() == python.resolve() and
             Path(build["viewer_environment"]).resolve() == environment.resolve() and
             build.get("viewer_dll_sha256") == dll_sha256, "overlay environment differs from its build")
    gl_path, gl = _reference(root, receipt["gl_verification"])
    _require(gl.get("schema") == "rmsim_full_model_renderer_gl_equivalence_v1" and
             gl.get("status") == "passed", "full-model GL verification has not passed")
    _require(gl.get("viewer_source_sha256") == sources[SOURCE_FILES[0]] and
             gl["runtime"].get("dll_sha256") == dll_sha256, "GL evidence belongs to another source/runtime")
    _require(gl.get("solver_steps") == 0 and gl.get("control_messages_sent") == 0 and
             gl.get("physics_processes_started") == 0, "display verification scope is invalid")
    _require(all(gl.get(name) is True for name in
                 ("state_unchanged", "solver_time_unchanged", "scene_arrays_unchanged",
                  "protected_dlls_unchanged", "recreated_context_pixels_equal")),
             "state, geometry or context-lifecycle equivalence is incomplete")
    _require(gl.get("all_body_pose_position_error_m") == 0 and
             gl.get("all_body_pose_quaternion_component_error") == 0,
             "GL verification changed a body pose")
    counts = gl["counts"]
    _require(counts.get("nmesh") == 249 and counts.get("nflex") == 14 and counts.get("nbody") == 16 and
             counts.get("nflexelem") == 1608500 and gl.get("static_flex_faces") == 3217000,
             "GL verification does not cover the full sealed scene")
    cases = gl["cases"]
    _require(len(cases) >= 3 and len({case["name"] for case in cases}) == len(cases),
             "GL verification requires three distinct camera cases")
    for case in cases:
        stats = case["cache_after_cached"]
        _require(case.get("color_equal") is True and case.get("depth_equal") is True and
                 case.get("color_changed_pixels") == 0 and case.get("max_depth_difference") == 0,
                 "GL color/depth parity failed: " + case["name"])
        _require(stats.get("enabled") is True and stats.get("live_contexts") == 1 and
                 stats.get("live_flex") == counts["nflex"] and stats.get("live_bytes") == 231624000 and
                 stats.get("uploads") == counts["nflex"] and stats.get("fast_batches", 0) > 0 and
                 stats.get("failures") == 0 and stats.get("rejected_contexts") == 0,
                 "GL verification did not exercise every static flex cache")
    cleanup = gl["after_final_free"]
    _require(cleanup.get("enabled") is False and all(cleanup.get(name) == 0 for name in
             ("live_contexts", "live_flex", "live_bytes", "failures", "rejected_contexts")),
             "GL context resources were not completely released")
    manifest_path = root / "assets/vehicles/aim_sentry/mujoco/rmuc2027/manifest.json"
    manifest = _json(manifest_path)
    identity = gl["identity"]
    _require(sha256_file(manifest_path) == identity["manifest_sha256"] and
             manifest["model_sha256"] == identity["model_sha256"] and
             manifest["physics_identity_sha256"] == identity["physics_identity_sha256"] and
             manifest["geometry_manifest_sha256"] == identity["geometry_manifest_sha256"],
             "GL verification belongs to another sealed model")
    _require(all(counts[name] == manifest["model_counts"].get(name, 0) for name in counts),
             "compiled GL dimensions differ from the sealed model")
    xml = (manifest_path.parent / manifest["model_xml"]).resolve()
    _require(sha256_file(xml) == manifest["model_sha256"], "sealed model XML hash mismatch")
    runtime_identity = {
        "receipt_sha256": sha256_file(receipt_path),
        "build_manifest_sha256": receipt["build_manifest"]["sha256"],
        "gl_verification_sha256": receipt["gl_verification"]["sha256"],
        "viewer_dll_sha256": dll_sha256,
        "source_sha256": sources[SOURCE_FILES[0]],
    }
    return {"python": str(python), "environment": str(environment), "dll_path": str(dll),
            "runtime_identity": runtime_identity, "source_sha256": dict(sources),
            "build_manifest": {"path": str(build_path), "sha256": runtime_identity["build_manifest_sha256"]},
            "gl_verification": {"path": str(gl_path), "sha256": runtime_identity["gl_verification_sha256"]},
            "receipt": {"path": str(receipt_path), "sha256": runtime_identity["receipt_sha256"]}}


def seal_optimized_viewer_runtime(repo_root: Path, build_manifest: Path, gl_verification: Path) -> dict:
    """Atomically install a receipt only after all existing integrity/GL gates pass."""
    root = Path(repo_root).resolve()
    environment = root / ENVIRONMENT_RELATIVE
    build_manifest, gl_verification = Path(build_manifest).resolve(), Path(gl_verification).resolve()
    _require(build_manifest.is_relative_to(root) and gl_verification.is_relative_to(root),
             "build/GL evidence must be inside the repository")
    receipt = {
        "schema": "rmsim_optimized_viewer_runtime_v1", "status": "validated",
        "renderer_extension": EXTENSION, "patch_sha256": PATCH_SHA256,
        "source_sha256": {name: sha256_file(root / name) for name in SOURCE_FILES},
        "viewer_python_sha256": sha256_file(environment / "Scripts/python.exe"),
        "viewer_dll_sha256": sha256_file(environment / "Lib/site-packages/mujoco/mujoco.dll"),
        "build_manifest": {"path": build_manifest.relative_to(root).as_posix(), "sha256": sha256_file(build_manifest)},
        "gl_verification": {"path": gl_verification.relative_to(root).as_posix(), "sha256": sha256_file(gl_verification)},
    }
    handle, name = tempfile.mkstemp(prefix="rmsim-viewer-receipt-", suffix=".tmp", dir=environment)
    temporary = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(receipt, indent=2) + "\n")
        _validate(root, root / SOURCE_FILES[0], temporary)
        temporary.replace(environment / RECEIPT_NAME)
    finally:
        temporary.unlink(missing_ok=True)
    return validate_optimized_viewer_runtime(root, root / SOURCE_FILES[0])


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--seal", action="store_true", help="write a receipt after validating completed build/GL evidence")
    parser.add_argument("--build-manifest", type=Path)
    parser.add_argument("--gl-verification", type=Path)
    args = parser.parse_args()
    if args.seal:
        if args.build_manifest is None or args.gl_verification is None:
            parser.error("--seal requires --build-manifest and --gl-verification")
        result = seal_optimized_viewer_runtime(args.repo_root, args.build_manifest, args.gl_verification)
    else:
        result = validate_optimized_viewer_runtime(args.repo_root, args.repo_root / SOURCE_FILES[0])
    print(json.dumps(result, indent=2))
