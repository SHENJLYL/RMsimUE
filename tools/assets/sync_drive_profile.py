"""Refresh a mesh rig's descriptive drive snapshot from its authoritative file.

Both physics backends load drive_profile_file at runtime. This command updates
the embedded import snapshot and its digest without touching rig geometry.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def geometry_digest(rig: dict) -> str:
    geometry = {k: v for k, v in rig.items()
                if k not in ('drive_profile', 'drive_profile_file', 'drive_profile_sha256')}
    return digest(json.dumps(geometry, sort_keys=True, separators=(',', ':'),
                             ensure_ascii=True, allow_nan=False).encode())


def synchronize(rig_path: Path, apply: bool = False) -> dict:
    rig_path = rig_path.resolve(strict=True)
    before_bytes = rig_path.read_bytes()
    rig = json.loads(before_bytes.decode('utf-8-sig'))
    declared = rig.get('drive_profile_file')
    if not isinstance(declared, str) or not declared or Path(declared).is_absolute():
        raise ValueError('A rig requires its local authoritative drive_profile_file')
    profile_path = (rig_path.parent / declared).resolve(strict=True)
    if not profile_path.is_relative_to(rig_path.parent):
        raise ValueError('Drive profile must remain inside the vehicle package')
    profile_bytes = profile_path.read_bytes()
    profile = json.loads(profile_bytes.decode('utf-8-sig'))
    if profile.get('schema') != 'rm_drive_profile_v1' or profile.get('units') != 'SI':
        raise ValueError('Invalid drive profile schema or units')
    for group in ('control', 'physics'):
        values = profile.get(group)
        if not isinstance(values, dict) or not values:
            raise ValueError('Missing drive parameter group: ' + group)
        for key, value in values.items():
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError('Nonfinite or nonnumeric drive parameter: ' + group + '.' + key)
    old = rig.get('drive_profile', {})
    differences = []
    for group in ('control', 'physics'):
        for key, value in profile[group].items():
            previous = old.get(group, {}).get(key)
            if previous != value:
                differences.append(dict(parameter=group + '.' + key,
                                        embedded_before=previous, source=value))
    source_sha = digest(profile_bytes)
    changed = old != profile or rig.get('drive_profile_sha256') != source_sha
    geometry_before = geometry_digest(rig)
    rig['drive_profile'] = profile
    rig['drive_profile_sha256'] = source_sha
    encoded = (json.dumps(rig, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    if geometry_digest(json.loads(encoded)) != geometry_before:
        raise RuntimeError('Rig geometry changed during drive snapshot synchronization')
    if apply and changed:
        if rig_path.read_bytes() != before_bytes or profile_path.read_bytes() != profile_bytes:
            raise RuntimeError('Vehicle inputs changed concurrently; rerun synchronization')
        rig_path.write_bytes(encoded)
    return dict(schema='rmsim_drive_snapshot_sync_v1', rig=str(rig_path),
                source=str(profile_path), source_sha256=source_sha,
                source_parameter_count=sum(len(profile[g]) for g in ('control', 'physics')),
                applied=bool(apply and changed), synchronized=bool(apply or not changed),
                changed=changed, differences=differences,
                geometry_sha256=geometry_before,
                rig_before_sha256=digest(before_bytes),
                rig_after_sha256=digest(rig_path.read_bytes()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rig', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    report = synchronize(args.rig, args.apply)
    text = json.dumps(report, indent=2, ensure_ascii=True) + '\n'
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding='utf-8')
    print(text, end='')
    return 0 if report['synchronized'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
