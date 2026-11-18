"""Publish a verified global landclass package without changing source geometry."""
import argparse
import copy
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'unreal'))
from wt_landclass import SCHEMA, LAND_MATERIAL_ID, digest, validate_landclass_surface


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--scene-source', type=Path, required=True)
    p.add_argument('--extracted', type=Path, required=True)
    p.add_argument('--evidence-root', type=Path, required=True)
    args = p.parse_args()
    scene_file = args.scene_source.resolve(strict=True)
    package = scene_file.parent
    extracted = args.extracted.resolve(strict=True)
    evidence = args.evidence_root.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    before_bytes = scene_file.read_bytes()
    before_hash = digest(scene_file)
    source = json.loads(before_bytes.decode('utf-8-sig'))
    if 'source_landclass_surface' in source or (package/'landclass').exists():
        raise ValueError('landclass package already published; use a fresh independent base')
    updated = copy.deepcopy(source)
    updated['source_landclass_surface'] = dict(schema=SCHEMA, shader='land_mesh',
        material_id=LAND_MATERIAL_ID,
        mesh_ids=sorted(m['id'] for m in source['meshes'] if LAND_MATERIAL_ID in m['material_slots']),
        manifest_file='landclass/landclass.json', manifest_sha256=digest(extracted/'landclass/landclass.json'))
    validate_landclass_surface(extracted, updated)
    (evidence/'before-scene-source.json').write_bytes(before_bytes)
    staging = evidence/'staging'
    staging.mkdir()
    shutil.copytree(extracted/'landclass', staging/'landclass')
    validate_landclass_surface(staging, updated)
    if digest(scene_file) != before_hash:
        raise RuntimeError('scene manifest changed during publication; no package update applied')
    shutil.copytree(staging/'landclass', package/'landclass')
    validate_landclass_surface(package, updated)
    if digest(scene_file) != before_hash:
        raise RuntimeError('scene manifest changed before publication; new landclass directory remains unattached')
    temporary = scene_file.with_name(scene_file.name+'.landclass-tmp')
    temporary.write_text(json.dumps(updated, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    temporary.replace(scene_file)
    actual = json.loads(scene_file.read_text(encoding='utf-8'))
    validate_landclass_surface(package, actual)
    check = copy.deepcopy(actual)
    del check['source_landclass_surface']
    if check != source:
        raise RuntimeError('publication changed unrelated scene fields')
    result = dict(schema='wt_landclass_publication_v1', status='passed',
        source_manifest_before_sha256=before_hash, source_manifest_sha256=digest(scene_file),
        landclass_manifest_sha256=actual['source_landclass_surface']['manifest_sha256'],
        all_existing_scene_fields_unchanged=True, mesh_geometry_and_material_ids_unchanged=True,
        source_level_sha256=source['source_sha256'], receiver_meshes=actual['source_landclass_surface']['mesh_ids'],
        material_id=LAND_MATERIAL_ID, native_render_verified=False)
    (evidence/'verification.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
