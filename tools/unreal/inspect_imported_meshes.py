"""Read actual UE imported asset bounds; run inside the project editor commandlet."""
import json
from pathlib import Path
import unreal
import re

root = Path(unreal.Paths.project_dir()).resolve().parents[1]
manifest_path = root / 'assets/vehicles/aim_sentry/asset_import.json'
manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
rows = []
for part in manifest['parts']:
    mesh = unreal.load_asset('/Game/RMSim/Imported/aim_sentry/' + part['name'])
    box = mesh.get_bounding_box()
    lo, hi = box.min, box.max
    rows.append({'part': part['name'], 'bounds_cm': {'lo': [lo.x, lo.y, lo.z], 'hi': [hi.x, hi.y, hi.z]},
                 'source_bounds_m': part['local_bounds_m']})
match = re.search(r'-BoundsReport=(?:"([^"]+)"|(\S+))',unreal.SystemLibrary.get_command_line())
out = Path(match.group(1) or match.group(2)) if match else root / 'artifacts/unreal/2026-11-15/imported-mesh-bounds.json'
if out.exists():raise RuntimeError('Use a fresh bounds report path')
out.parent.mkdir(parents=True,exist_ok=True)
out.write_text(json.dumps(rows, indent=2), encoding='utf-8')
unreal.log('ASSET_BOUNDS_RECORDED ' + str(out))
