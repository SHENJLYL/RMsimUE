"""Fetch a pinned, small CC0 gym asset selection and write its provenance.

Only selected texture members are extracted. Original archives stay in the
ignored artifact cache; source files, licenses and per-file hashes are separate.
"""
from __future__ import annotations
import argparse
import datetime
import hashlib
import io
import json
from pathlib import Path
import shutil
import urllib.request
import zipfile

from prepare_campus_seat_row import prepare as prepare_seats, SOURCE_SHA256

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = (
    ("WoodFloor051", 5104319, "3f493484eab1ec5e1c466b90e515003b286fc6d7f84ff8ff6485900bfc26cef5", 1.8),
    ("Concrete046", 5989511, "72bf4321acbb39ddbc3b786f5996813b2a3cede12efefb47955e97ea9668985b", 2.4),
    ("Wood049", 6784006, "74ec51bcf5ddfc2e9af6205dc5cbcc90ad51c22117e013ff8b1c7258f8ecbc69", .8),
    ("Metal050A", 2648186, "c2785502e4e2c14869f728f819548e1eaeef92df9bd9d4ec8e4904daaed24805", 1.0),
)
SEAT_PAGE = "https://www.innerscene.com/tools/library/3d-parts/stadium-seats-on-a-concrete-riser-row-of-6-000f8e6d"
SEAT_URL = "https://files.innerscene.com/library/stadium-seats-on-a-concrete-riser-row-of-6-000f8e6d/929496367130d8cac119/stadium-seats-on-a-concrete-riser-row-of-6.glb"
ENVIRONMENT = {
    "id": "quadrangle_cloudy", "author": "Savva Zakharov",
    "source_url": "https://polyhaven.com/a/quadrangle_cloudy",
    "license": "CC0-1.0", "license_url": "https://polyhaven.com/license",
    "download_url": "https://dl.polyhaven.org/file/ph-assets/HDRIs/hdr/1k/quadrangle_cloudy_1k.hdr",
    "file": "environments/quadrangle_cloudy_1k.hdr", "bytes": 1671356,
    "sha256": "6b1f8153e612ebc6059fa28ec652f29b60df9ff6e7db4a401c653a09fd74c527",
    "width": 1024, "height": 512, "format": "Radiance RGBE",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fetch_pinned(url, destination, expected_size, expected_hash):
    if destination.exists():
        data = destination.read_bytes()
        if len(data) != expected_size or sha(data) != expected_hash:
            raise ValueError("Existing cached asset hash mismatch: " + str(destination))
        return data
    request = urllib.request.Request(url, headers={"User-Agent": "RMSim-campus-gym/1.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
        data = response.read(expected_size + 1)
    if len(data) != expected_size or sha(data) != expected_hash:
        raise ValueError("Downloaded asset hash/size mismatch: " + url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.write_bytes(data)
    temporary.replace(destination)
    return data


def write_selected(destination, data):
    if destination.exists() and destination.read_bytes() != data:
        raise ValueError("Refusing to overwrite a changed selected asset: " + str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset-root", type=Path, default=ROOT / "assets/maps/rmuc2027/arena/campus-gym")
    parser.add_argument("--cache", type=Path, default=ROOT / "artifacts/cache/campus-gym")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.report and args.report.exists():
        raise FileExistsError("Use a fresh preparation report path")
    source = args.asset_root.resolve(); cache = args.cache.resolve()
    source.mkdir(parents=True, exist_ok=True); cache.mkdir(parents=True, exist_ok=True)
    manifest = {"schema": "rmsim_campus_gym_sources_v1", "license": "CC0-1.0",
                "prepared_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "material_packages": [], "files": {}, "license_sources": [
                    {"provider": "ambientCG", "url": "https://docs.ambientcg.com/license/", "license": "CC0-1.0"},
                    {"provider": "Innerscene", "url": SEAT_PAGE, "license": "CC0-1.0"},
                    {"provider": "Poly Haven", "url": ENVIRONMENT["license_url"], "license": "CC0-1.0"}],
                "reference_image_policy": "User reference image is composition guidance; it is not included as a texture or redistributed."}
    def record(path):
        content = path.read_bytes()
        manifest["files"][path.relative_to(source).as_posix()] = {"bytes": len(content), "sha256": sha(content)}
    for identifier, size, digest, tile in PACKAGES:
        archive_name = identifier + "_1K-JPG.zip"
        url = "https://ambientcg.com/get?file=" + archive_name
        archive = fetch_pinned(url, cache / archive_name, size, digest)
        channels = ["Color", "NormalDX", "Roughness"]
        if identifier == "Metal050A": channels.append("Metalness")
        package = {"id": identifier, "source_url": "https://ambientcg.com/view?id=" + identifier,
                   "download_url": url, "package_bytes": size, "package_sha256": digest,
                   "license": "CC0-1.0", "tile_size_m": tile, "selected_files": {}}
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            for channel in channels:
                member = identifier + "_1K-JPG_" + channel + ".jpg"
                info = z.getinfo(member)
                if info.is_dir() or info.file_size > 8 * 1024 * 1024:
                    raise ValueError("Invalid selected archive member: " + member)
                path = source / "textures" / member
                write_selected(path, z.read(info)); record(path)
                package["selected_files"][channel] = path.relative_to(source).as_posix()
        manifest["material_packages"].append(package)
        print("RMSIM_GYM_PACKAGE_READY " + identifier, flush=True)
    environment = fetch_pinned(ENVIRONMENT["download_url"],
        cache / Path(ENVIRONMENT["file"]).name, ENVIRONMENT["bytes"], ENVIRONMENT["sha256"])
    environment_path = source / ENVIRONMENT["file"]
    write_selected(environment_path, environment); record(environment_path)
    manifest["environment"] = dict(ENVIRONMENT)
    print("RMSIM_GYM_ENVIRONMENT_READY " + ENVIRONMENT["id"], flush=True)
    raw_seat = cache / "stadium-seats-row-6.glb"
    fetch_pinned(SEAT_URL, raw_seat, 166508, SOURCE_SHA256)
    models = source / "models"; models.mkdir(exist_ok=True)
    derivative = prepare_seats(raw_seat, models / "SeatRow.glb", models / "SeatRow.obj")
    for name in ["SeatRow.glb", "SeatRow.obj", "SeatRow.mtl", "SeatRow_Frame.png", "SeatRow_Plastic.png"]:
        record(models / name)
    manifest["seat_row"] = {"source_url": SEAT_PAGE, "download_url": SEAT_URL,
        "source_sha256": SOURCE_SHA256, "source_bytes": 166508,
        "license": "CC0-1.0", "derivative": derivative,
        "obj": "models/SeatRow.obj", "glb": "models/SeatRow.glb",
        "script_sha256": sha((Path(__file__).parent / "prepare_campus_seat_row.py").read_bytes())}
    notice = ("# Campus gym CC0 source notice\n\n"
        "Textures: ambientCG, https://ambientcg.com/\n"
        "License declaration: https://docs.ambientcg.com/license/\n"
        "WoodFloor051, Concrete046, Wood049 and Metal050A are CC0-1.0.\n\n"
        "Seat row: Innerscene original model, released CC0-1.0.\n" + SEAT_PAGE + "\n\n"
        "The source seat model includes CC0-1.0 in its glTF metadata.\n"
        "Derivative changes: omitted concrete riser and its texture, combined seat/frame\n"
        "primitives into one mesh, recentered vertices without resizing. OBJ converts\n"
        "to X-width/Y-rear/Z-up, with seats facing -Y.\n\n"
        "Courtyard environment: Quadrangle Cloudy by Savva Zakharov, Poly Haven.\n"
        "Source: https://polyhaven.com/a/quadrangle_cloudy\n"
        "License: CC0-1.0, https://polyhaven.com/license\n"
        "The selected 1024x512 Radiance HDRI is redistributed without modification.\n\n"
        "CC0 legal code: https://creativecommons.org/publicdomain/zero/1.0/legalcode\n"
        "Archive hashes and selected-file hashes are recorded in SOURCE.json.\n")
    (source / "LICENSE.txt").write_text(notice, encoding="utf-8"); record(source / "LICENSE.txt")
    (source / "SOURCE.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    report = {"status": "passed", "scope": "Pinned download hashes, selected source files and deterministic seat reduction", "asset_root": str(source),
              "source_manifest_sha256": sha((source / "SOURCE.json").read_bytes()),
              "selected_file_count": len(manifest["files"]), "seat_row": derivative,
              "environment": manifest["environment"],
              "native_unreal_import_verified": False}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
