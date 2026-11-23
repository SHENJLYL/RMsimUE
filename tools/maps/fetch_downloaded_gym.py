"""Fetch the pinned author-created indoor basketball gym for RMSim."""
from pathlib import Path
import argparse
import datetime as dt
import hashlib
import json
import urllib.request

URL = "https://huggingface.co/datasets/allenai/objaverse/resolve/main/glbs/000-154/943a9bc962974d02aa34592084687c3c.glb"
SOURCE = "https://sketchfab.com/3d-models/indoor-basketball-court-943a9bc962974d02aa34592084687c3c"
SHA = "2da363924cc4786a6d859990c26a0a48ec7427083a64f32f565817aac4127d99"
SIZE = 56073224


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(data)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.asset_root.resolve()
    raw = root / "source/indoor-basketball-court.glb"
    raw.parent.mkdir(parents=True, exist_ok=True)
    if raw.exists() and (raw.stat().st_size != SIZE or sha(raw) != SHA):
        raise RuntimeError("An existing source differs from the pinned model; preserve it for review")
    if not raw.exists():
        print("Downloading the complete author-created indoor basketball gym", flush=True)
        temporary = raw.with_suffix(".download")
        request = urllib.request.Request(URL, headers={"User-Agent": "RMSim-gym-import/1.0"})
        count = 0
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as stream:
            while True:
                data = response.read(1024 * 1024)
                if not data:
                    break
                count += len(data)
                if count > SIZE:
                    raise RuntimeError("Unexpected source length")
                stream.write(data)
        if count != SIZE or sha(temporary) != SHA:
            raise RuntimeError("Downloaded model differs from the independently inspected source")
        temporary.replace(raw)
    record = {
        "schema": "rmsim_downloaded_gym_source_v1", "title": "Indoor Basketball Court",
        "author": "NGUYENNGHIAKK (Nguyen Trong Nghia)",
        "author_url": "https://sketchfab.com/nguyennghiakk", "source_url": SOURCE,
        "download_url": URL, "mirror": "AllenAI Objaverse public redistribution",
        "license": "CC-BY-4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "source_file": "source/indoor-basketball-court.glb", "bytes": SIZE, "sha256": SHA,
        "retrieved_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "modification_recipe": "prepared.json",
        "source_field_and_vehicle_scale": "unchanged",
        "source_preservation": "Original GLB bytes are retained unchanged; edits are derived exports",
    }
    (root / "SOURCE.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    notice = ("Indoor Basketball Court\nBy NGUYENNGHIAKK (Nguyen Trong Nghia)\n" + SOURCE +
              "\nCC BY 4.0: https://creativecommons.org/licenses/by/4.0/\n" +
              "Adapted for RMSim: uniform whole-venue scale and orientation; basketball equipment removed; " +
              "unrelated sign content replaced with official DJI and RoboMaster artwork; materials and lighting adapted for Unreal Engine.\n" +
              "The original RMUC competition field and vehicle dimensions are unchanged.\n" +
              "Brand artwork retains its respective ownership and is separate from the venue license.\n")
    (root / "NOTICE.txt").write_text(notice, encoding="utf-8")
    print(json.dumps({"status": "passed", "file": str(raw), "bytes": SIZE, "sha256": sha(raw)}), flush=True)


if __name__ == "__main__":
    main()
