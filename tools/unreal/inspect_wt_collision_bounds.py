"""Read-only float32 bound diagnostics; completion is not collision acceptance.

Run-WTNativeScript.ps1 -ReportArgument CollisionBoundsReport. The diagnostic
loads existing rail assets only and never imports, cooks, saves assets or World.
"""
import datetime
import json
import math
from pathlib import Path
import re
import struct
import sys
import unreal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wt_collision import load_contract, save, digest, asset_file, STATIC_RAIL_SOURCE


def argument(name):
    match = re.search(r'-'+name+r'=(?:"([^"]+)"|(\S+))', unreal.SystemLibrary.get_command_line())
    if not match:
        raise ValueError('pass -'+name+'=<path>')
    return match.group(1) or match.group(2)


def f32(value):
    return struct.unpack('<f', struct.pack('<f', float(value)))[0]


def ulp32(value):
    value = abs(f32(value))
    return math.ldexp(1., math.frexp(value)[1]-24) if value >= 2.**-126 else 2.**-149


def vector(value):
    return [float(value.x), float(value.y), float(value.z)]


def difference(a, b):
    return {key: [a[key][i]-b[key][i] for i in range(3)] for key in ('lo', 'hi')}


def maximum(value):
    return max(abs(x) for row in value.values() for x in row)


def rounded_bounds(source):
    result = {key: list(map(f32, source[key])) for key in ('lo', 'hi')}
    origin = [f32(f32(result['lo'][i]+result['hi'][i])*.5) for i in range(3)]
    extent = [f32(f32(result['hi'][i]-result['lo'][i])*.5) for i in range(3)]
    box = dict(lo=[f32(origin[i]-extent[i]) for i in range(3)],
               hi=[f32(origin[i]+extent[i]) for i in range(3)])
    return result, dict(origin=origin, extent=extent), box


def property_vector(mesh, name):
    try:
        return vector(mesh.get_editor_property(name))
    except Exception as error:
        return dict(unavailable=str(error))


def bounds_value(mesh, name):
    try:
        value = getattr(mesh, name)()
        origin, extent = vector(value.origin), vector(value.box_extent)
        return dict(origin=origin, extent=extent, sphere_radius=float(value.sphere_radius),
                    reconstructed_double=dict(
                        lo=[origin[i]-extent[i] for i in range(3)],
                        hi=[origin[i]+extent[i] for i in range(3)]),
                    reconstructed_float32=dict(
                        lo=[f32(origin[i]-extent[i]) for i in range(3)],
                        hi=[f32(origin[i]+extent[i]) for i in range(3)]))
    except Exception as error:
        return dict(unavailable=str(error))


def main():
    source = Path(argument('SceneSource')).resolve(strict=True)
    out = Path(argument('CollisionBoundsReport')).resolve()
    out.mkdir(parents=True, exist_ok=False)
    root, manifest, contract = load_contract(source)
    content = Path(unreal.Paths.project_content_dir()).resolve()
    world = content/'RMSim/Maps/wt_vlaanderen/World.umap'
    before = dict(world_sha256=digest(world), source_sha256=digest(source),
                  manifest_sha256=digest(root/'manifest.json'))
    report = dict(schema='wt_collision_bounds_diagnostic_v1', status='running',
                  scope='diagnostic completion only; no collision acceptance',
                  started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  script_sha256=digest(Path(__file__)), contract=contract,
                  source_unit='m', native_unit='cm', assets=[], before=before)
    save(out/'verification.json', report)
    for spec in manifest['meshes']:
        if STATIC_RAIL_SOURCE not in spec.get('source_models', []):
            continue
        file = asset_file(content, spec['asset'])
        row = dict(id=spec['id'], asset=spec['asset'], exists=file.is_file())
        if not file.is_file():
            row['state'] = 'not_yet_authored'
            report['assets'].append(row)
            continue
        asset_hash = digest(file)
        mesh = unreal.load_asset(spec['asset'])
        if not isinstance(mesh, unreal.StaticMesh):
            row['state'] = 'failed_to_load_static_mesh'
            report['assets'].append(row)
            continue
        box = mesh.get_bounding_box()
        actual = dict(lo=vector(box.min), hi=vector(box.max))
        expected = dict(
            lo=[spec['bounds_m']['lo'][0]*100., -spec['bounds_m']['hi'][1]*100.,
                spec['bounds_m']['lo'][2]*100.],
            hi=[spec['bounds_m']['hi'][0]*100., -spec['bounds_m']['lo'][1]*100.,
                spec['bounds_m']['hi'][2]*100.])
        rounded, origin_extent, rebuilt = rounded_bounds(expected)
        errors = dict(source_double=difference(actual, expected),
                      source_cm_float32=difference(actual, rounded),
                      source_cm_float32_origin_extent=difference(actual, rebuilt))
        row.update(state='diagnosed', actual_get_bounding_box_cm=actual,
                   actual_get_bounds_cm=bounds_value(mesh, 'get_bounds'),
                   actual_get_imported_bounds_cm=bounds_value(mesh, 'get_imported_bounds'),
                   positive_bounds_extension_cm=property_vector(mesh, 'positive_bounds_extension'),
                   negative_bounds_extension_cm=property_vector(mesh, 'negative_bounds_extension'),
                   source_double_cm=expected, source_cm_float32=rounded,
                   float32_origin_extent_cm=origin_extent,
                   float32_origin_extent_rebuilt_cm=rebuilt,
                   source_cm_float32_ulp={key: list(map(ulp32, expected[key])) for key in expected},
                   errors_cm=errors, maximum_errors_cm={key: maximum(value) for key, value in errors.items()},
                   saved_asset_sha256=asset_hash, saved_asset_unchanged=digest(file)==asset_hash)
        report['assets'].append(row)
        save(out/'verification.json', report)
    report['after'] = dict(world_sha256=digest(world), source_sha256=digest(source),
                           manifest_sha256=digest(root/'manifest.json'))
    if report['after'] != before or not all(x.get('saved_asset_unchanged', True)
                                            for x in report['assets']):
        raise RuntimeError('diagnostic altered protected bytes')
    report.update(status='passed', diagnosed=sum(x.get('state')=='diagnosed' for x in report['assets']),
                  missing=sum(x.get('state')=='not_yet_authored' for x in report['assets']),
                  finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  acceptance='not_evaluated')
    save(out/'verification.json', report)
    unreal.log('WT_COLLISION_BOUNDS_DIAGNOSTIC '+str(out/'verification.json'))


if __name__ == '__main__':
    main()
