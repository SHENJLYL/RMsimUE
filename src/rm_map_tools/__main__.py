import argparse
import hashlib
import json
from pathlib import Path
import sys
from .map import MapError, load_map, occupancy


def main(argv=None):
    parser = argparse.ArgumentParser(description='Validate a flat map or export its coherent occupancy grid')
    parser.add_argument('command', choices=('validate', 'export'))
    parser.add_argument('map')
    parser.add_argument('--output', help='new output directory; existing directories are never overwritten')
    parser.add_argument('--resolution', type=float, default=0.05)
    args = parser.parse_args(argv)
    try:
        data, digest = load_map(args.map)
        raw=Path(args.map).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=digest:raise MapError('map changed during export')
        source_md5=hashlib.md5(raw).hexdigest()
        if args.command == 'validate':
            print(f"VALID {data['id']}: {len(data['obstacles'])} obstacles, {len(data['spawns'])} spawns, {len(data['regions'])} regions")
            return 0
        if not args.output:
            raise MapError('export requires --output')
        width, height, origin, pixels = occupancy(data, args.resolution)
        output = Path(args.output)
        output.mkdir(parents=True, exist_ok=False)
        (output / 'map.pgm').write_bytes(f'P5\n{width} {height}\n255\n'.encode('ascii') + pixels)
        (output / 'map.yaml').write_text(
            f'image: map.pgm\nresolution: {args.resolution}\norigin: {origin}\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.196\n', encoding='utf-8')
        (output / 'metadata.json').write_text(json.dumps({'map_id': data['id'], 'source_sha256': digest, 'source_md5':source_md5,
            'width': width, 'height': height, 'resolution': args.resolution, 'origin': origin,
            'semantics': 'flat box projection, no robot inflation, not live localization'}, indent=2), encoding='utf-8')
        print(f"EXPORTED {data['id']}: {width}x{height} -> {output}")
        return 0
    except (MapError, OSError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
