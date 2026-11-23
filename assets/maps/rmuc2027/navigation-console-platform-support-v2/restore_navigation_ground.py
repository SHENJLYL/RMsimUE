"""Restore both local ground grids from the pinned RMUC collision GLB."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

import numpy as np


PACKAGE = Path(__file__).resolve().parent
TOOLS = PACKAGE.parents[3] / "tools/maps"


def verify(path, expected):
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError(f"SHA-256 mismatch for {path}")


def restore(blender, collision, output):
    manifest = PACKAGE / "SOURCE.json"
    specification = json.loads(manifest.read_text(encoding="utf-8"))
    release = json.loads((PACKAGE / "navigation-release.json").read_text(encoding="utf-8"))
    if release.get("schema") != "rmsim_navigation_release_v1":
        raise ValueError("Unrecognized navigation release")
    verify(manifest, release["map_package_sha256"])
    survey = release["surveyed_ground"]
    support = release["simulation_navigation"]["ground_support"]
    missing = []
    for record in (survey, support):
        target = output / record["file"]
        if target.is_file():
            verify(target, record["sha256"])
        else:
            missing.append(record)
    if not missing:
        return

    verify(collision, specification["map_metadata"]["collision_sha256"])
    verify(PACKAGE / "map.pgm", specification["raster_sha256"])
    descriptor = PACKAGE.parent / "mesh-source.json"
    scene = PACKAGE.parent / "map.json"
    with TemporaryDirectory(prefix="rmuc-ground-") as temporary:
        scratch = Path(temporary)
        columns = scratch / "columns"
        raster = scratch / "support"
        command = [blender, "--background", "--factory-startup", "--disable-autoexec", "--python-exit-code", "1",
                   "--python-expr", "import sys\nsys.dont_write_bytecode = True"]
        subprocess.run([*command, "--python", str(TOOLS / "sample_rmuc_navigation_columns.py"), "--",
            "--source", str(collision), "--descriptor", str(descriptor), "--output", str(columns),
            "--resolution", "0.05", "--max-layers", "128"], check=True)
        subprocess.run([*command, "--python", str(TOOLS / "export_rmuc_navigation_candidate.py"), "--",
            "--source", str(collision), "--descriptor", str(descriptor), "--columns", str(columns),
            "--map", str(scene), "--output", str(raster)], check=True)
        heights = raster / "support-heights.npz"
        subprocess.run([sys.executable, "-B", str(TOOLS / "export_surveyed_ground.py"),
            str(heights), str(PACKAGE), str(scratch / survey["file"])], check=True)
        with np.load(heights, allow_pickle=False) as grid:
            z = np.asarray(grid["z"], dtype="<f4")
            header = f"RMSIM_SUPPORT_V1 {z.shape[1]} {z.shape[0]} {grid['x'][0]:.10f} {grid['y'][0]:.10f} 0.05\n"
            (scratch / support["file"]).write_bytes(header.encode("ascii") + z.tobytes())
        for record in (survey, support):
            verify(scratch / record["file"], record["sha256"])
        output.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".restore-ground-", dir=output) as target_workspace:
            for record in missing:
                name = record["file"]
                temporary_file = Path(target_workspace) / name
                shutil.copyfile(scratch / name, temporary_file)
                temporary_file.replace(output / name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", default="blender", help="Blender executable")
    parser.add_argument("--collision", type=Path, required=True, help="Local full-map-collision.glb")
    parser.add_argument("--output", type=Path, default=PACKAGE, help="Output directory for the verified grids")
    args = parser.parse_args()
    restore(args.blender, args.collision.resolve(), args.output)
