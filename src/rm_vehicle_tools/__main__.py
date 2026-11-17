"""CLI; validation finishes before export can replace any destination file."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

from .vehicle import VehicleError, load_vehicle, resolve_package


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate and inspect portable RM vehicle packages")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "inspect", "export"):
        sub = commands.add_parser(command)
        sub.add_argument("package", help="package directory or vehicle.json")
        if command == "export":
            sub.add_argument("--output", required=True, help="canonical JSON destination")
    args = parser.parse_args(argv)
    temporary = None
    try:
        data = load_vehicle(args.package)
        if args.command == "validate":
            print(f"VALID {data['id']}: {len(data['links'])} links, {len(data['joints'])} joints, {len(data['sensors'])} sensors")
        elif args.command == "inspect":
            print(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False))
        else:
            output = Path(args.output).resolve()
            package_root, manifest_path = resolve_package(args.package)
            protected = [manifest_path, package_root / data["source"]["urdf_path"]]
            protected += [package_root / item["path"] for item in data["source"]["assets"]]
            if output in [p.resolve() for p in protected]:
                raise VehicleError("output: must not overwrite a source package file")
            text = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                             prefix=".vehicle-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(text)
            os.replace(temporary, output)
            temporary = None
            print(f"EXPORTED {data['id']} -> {output}")
        return 0
    except (VehicleError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
