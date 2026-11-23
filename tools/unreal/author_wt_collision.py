"""Author only the local derived sleeper/curb collision assets; never save World.

Use Run-WTNativeScript.ps1 -ReportArgument CollisionReport (NullRHI is sufficient).
Source/prepared visual files and original World remain byte-identical.
"""
import datetime
import json
from pathlib import Path
import re
import sys
import traceback
import unreal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wt_collision import load_contract, read, save, digest, local, asset_file, bounds_record, check_native
from import_vehicle_assets import import_asset


def argument(name):
    match = re.search(r'-'+name+r'=(?:"([^"]+)"|(\S+))', unreal.SystemLibrary.get_command_line())
    if not match:
        raise ValueError('pass -'+name+'=<path>')
    return match.group(1) or match.group(2)


def options():
    value = unreal.FbxImportUI()
    for key, setting in dict(import_mesh=True, import_as_skeletal=False,
            mesh_type_to_import=unreal.FBXImportType.FBXIT_STATIC_MESH,
            import_materials=False, import_textures=False).items():
        value.set_editor_property(key, setting)
    settings = value.get_editor_property('static_mesh_import_data')
    for key, setting in dict(combine_meshes=True, convert_scene=True, convert_scene_unit=True,
            force_front_x_axis=True, transform_vertex_to_absolute=True, bake_pivot_in_vertex=False,
            auto_generate_collision=False, reorder_material_to_fbx_order=True,
            generate_lightmap_u_vs=False, remove_degenerates=False).items():
        settings.set_editor_property(key, setting)
    settings.set_editor_property('normal_import_method', unreal.FBXNormalImportMethod.FBXNIM_COMPUTE_NORMALS)
    return value


def native(mesh):
    raw = unreal.MapImportLibrary.describe_generated_collision(mesh)
    if not isinstance(raw, str) or not raw:
        raise RuntimeError('DescribeGeneratedCollision returned no JSON')
    return json.loads(raw)


def author(source_file, output):
    source_file = Path(source_file).resolve(strict=True)
    evidence = Path(output).resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    content = Path(unreal.Paths.project_content_dir()).resolve()
    world = content/'RMSim/Maps/wt_vlaanderen/World.umap'
    before = dict(world_sha256=digest(world), scene_source_sha256=digest(source_file))
    started = datetime.datetime.now(datetime.timezone.utc)
    report = dict(schema='wt_generated_collision_author_v1', status='running',
                  started_utc=started.isoformat(), assets=[], before=before,
                  script_sha256=digest(Path(__file__)),
                  validator_sha256=digest(Path(__file__).with_name('wt_collision.py')))
    save(evidence/'verification.json', report)
    try:
        root, manifest, contract = load_contract(source_file)
        report['contract'] = contract
        for index, spec in enumerate(manifest['meshes']):
            target = spec['asset']
            file = asset_file(content, target)
            mesh = unreal.load_asset(target) if unreal.EditorAssetLibrary.does_asset_exist(target) else None
            reused = False
            if isinstance(mesh, unreal.StaticMesh) and file.is_file():
                try:
                    check_native(native(mesh), spec, manifest)
                    bounds_record(mesh, spec)
                    reused = True
                except (ValueError, RuntimeError):
                    pass
            if not reused:
                folder, name = target.rsplit('/', 1)
                name = name.split('.')[0]
                objects = import_asset(local(root, spec['file']), folder, name, options())
                candidates = [item for item in objects if isinstance(item, unreal.StaticMesh)]
                if len(candidates) != 1 or candidates[0].get_path_name() != target:
                    raise RuntimeError('derived FBX did not yield the requested single mesh')
                mesh = candidates[0]
                points = [unreal.Vector(v[0]*100, -v[1]*100, v[2]*100)
                          for v in spec.get('convex_vertices_m', [])]
                # UE 5.8 bool + FString& success returns the FString; false returns None.
                error = unreal.MapImportLibrary.prepare_generated_collision(
                    mesh, spec['mode'], spec['triangles'], spec['sha256'],
                    manifest['recipe_sha256'], points)
                if not isinstance(error, str) or error:
                    raise RuntimeError('native collision cook failed: '+str(error))
                if not unreal.EditorAssetLibrary.save_loaded_asset(mesh, only_if_is_dirty=False):
                    raise RuntimeError('cannot save generated collision mesh')
            actual = native(mesh)
            check_native(actual, spec, manifest)
            measured = bounds_record(mesh, spec)
            if not file.is_file():
                raise RuntimeError('saved generated collision asset absent')
            report['assets'].append(dict(id=spec['id'], reused=reused, native=actual,
                                         bounds=measured, saved_asset_sha256=digest(file)))
            save(evidence/'verification.json', report)
            if (index+1) % 25 == 0:
                unreal.log('WT_GENERATED_COLLISION_ASSETS '+str(index+1)+'/'+str(len(manifest['meshes'])))
                mesh = None
                unreal.SystemLibrary.collect_garbage()
        report.update(status='passed', meshes=len(report['assets']),
                      reused_meshes=sum(item['reused'] for item in report['assets']),
                      source_visuals_unchanged=True, saved_world_unchanged=True,
                      native_scene_queries_pending=True)
    except Exception as error:
        report.update(status='failed', error=str(error), traceback=traceback.format_exc())
        raise
    finally:
        report['after'] = dict(world_sha256=digest(world), scene_source_sha256=digest(source_file))
        if report['after'] != before:
            report.update(status='failed', error='author changed protected World or scene-source bytes')
        report['finished_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save(evidence/'verification.json', report)
    if report['status'] != 'passed':
        raise RuntimeError(report['error'])
    unreal.log('WT_GENERATED_COLLISION_AUTHORED '+str(evidence/'verification.json'))


if __name__ == '__main__':
    author(argument('SceneSource'), argument('CollisionReport'))
