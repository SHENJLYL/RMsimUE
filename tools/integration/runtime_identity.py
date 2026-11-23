"""Fingerprint the actual Point-LIO installation and local build inputs.

Standard-library only: build scripts can import this before loading ROS.
Hashes use exact file bytes; the tree hash covers a sorted path-to-hash manifest.
"""
from pathlib import Path
import hashlib
import json
import os


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def capture_pointlio_inputs(root, install_prefix):
    root = Path(root).resolve()
    prefix = Path(install_prefix).resolve()
    source = root / "ros2_ws/src/point_lio"
    required = (source / "CMakeLists.txt", source / "package.xml")
    for path in required:
        if not path.is_file():
            raise FileNotFoundError("Missing Point-LIO build input: " + str(path))
    executable = prefix / "lib/point_lio" / (
        "pointlio_mapping.exe" if os.name == "nt" else "pointlio_mapping")
    base_config = prefix / "share/point_lio/config/mid360.yaml"
    patch = root / "third_party/patches/point-lio-native.patch"
    paths = sorted(path for path in source.rglob("*") if path.is_file() and (
        path.suffix.lower() in (".cpp", ".h", ".hpp", ".cmake")
        or path.name in ("CMakeLists.txt", "package.xml")))
    source_files = {path.relative_to(root).as_posix(): file_sha256(path) for path in paths}
    manifest = json.dumps(source_files, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False).encode("utf-8")
    return {
        "executable_path": str(executable),
        "executable_sha256": file_sha256(executable),
        "source_files": source_files,
        "source_tree_sha256": hashlib.sha256(manifest).hexdigest(),
        "patch_path": patch.relative_to(root).as_posix(),
        "patch_sha256": file_sha256(patch),
        "base_config_path": str(base_config),
        "base_config_sha256": file_sha256(base_config),
    }
