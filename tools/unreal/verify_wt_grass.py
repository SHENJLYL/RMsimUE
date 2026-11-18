"""Independent read-only reload audit of saved Flanders Landscape Grass.

Run after grass authoring and save, in a separate UnrealEditor-Cmd process:
  RMSim.uproject -run=pythonscript -script=<this file> -NullRHI -unattended
  -MapPackage=<assets/maps/wt_vlaanderen>
  -GrassSampleFixture=<native-sample-fixture.json>
  -GrassAuditOutput=<fresh directory>

Reads saved GrassData, types, meshes and source-PNG sample expectations. Does not
rebuild grass maps, generate instances, edit materials, or save any UE asset.
"""
from __future__ import annotations
import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wt_grass import validate_foliage

POINTS_ROS = ((1028.0,-1020.0),(1074.25,-1011.75),(893.75,-1029.25),(1019.25,-1217.75))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    temporary.replace(path)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def number(value):
    return type(value) in (int,float) and math.isfinite(value)


def close(actual, expected, tolerance=1e-5):
    return number(actual) and number(expected) and abs(actual-expected)<=tolerance


def rows_by_name(rows, names):
    require(isinstance(rows,list) and len(rows)==len(names), 'native grass row count differs')
    mapped = {row['name']:row for row in rows}
    require(len(mapped)==len(rows) and set(mapped)==set(names), 'native grass names differ')
    return mapped


def validate_layer_readback(native, definition, folder):
    require(native.get('schema')=='rmsim_landscape_grass_readback_v1' and native.get('status')=='read_back',
            'unexpected native grass readback')
    total = native['landscape_components']
    require(number(total) and total>0 and native['components_with_valid_data']==total,
            'not every saved landscape component has valid grass data')
    require(0<native['components_with_weights']<=total, 'saved grass weights are empty')
    require(native['collision_enabled_grass_components']==0, 'decorative grass enables collision')
    layers = {item['id']:item for item in definition['layers']}
    actual = rows_by_name(native['layers'], layers)
    records = []
    for name, layer in layers.items():
        row = actual[name]
        require(layer['weight_statistics']['nonzero_weight_pixels']>0, 'source fixture unexpectedly has an empty layer')
        for key in ('weighted_components','nonzero_weight_samples','weight_byte_sum'):
            require(number(row.get(key)) and row[key]>0, name+': saved '+key+' is zero or invalid')
        require(row['weighted_components']<=total and
                row['nonzero_weight_samples']<=row['weight_byte_sum']<=255*row['nonzero_weight_samples'],
                name+': saved weight accounting is invalid')
        expected_type = folder+'/Foliage/Types/WT_'+name+'.WT_'+name
        expected_mesh = folder+'/Foliage/Meshes/'+layer['model_id']+'.'+layer['model_id']
        require(row['grass_type']==expected_type and row['varieties']==1 and len(row['variety_settings'])==1,
                name+': GrassType identity or variety count differs')
        settings = row['variety_settings'][0]
        require(settings['mesh']==expected_mesh, name+': source grass mesh differs')
        expected = dict(density_per_100m2=layer['density_per_100m2'],
            start_cull_cm=round(layer['start_cull_distance_cm']),
            end_cull_cm=round(layer['end_cull_distance_cm']),
            scale_x_min=layer['scale_x'][0],scale_x_max=layer['scale_x'][1],
            scale_z_min=layer['scale_z'][0],scale_z_max=layer['scale_z'][1])
        for key,value in expected.items():
            require(close(settings.get(key),value,1e-4), name+': native '+key+' differs')
        records.append(dict(id=name,status='passed',source_model=layer['model_id'],
            source_nonzero_pixels=layer['weight_statistics']['nonzero_weight_pixels'],
            native=row,expected_settings=expected))
    return records


def validate_sample_fixture(fixture, checked):
    definition = checked['definition']
    require(fixture.get('schema')=='wt_grass_native_sample_fixture_v1' and fixture.get('status')=='passed',
            'unsupported source grass sample fixture')
    require(fixture['definition_sha256']==checked['definition_sha256'] and
            fixture['source_level_sha256']==definition['source_level_sha256'],
            'source sample fixture belongs to another foliage contract')
    require(fixture['world_to_weight_uv']==definition['weight_world_uv'], 'fixture weight UV differs')
    expected = {item['id']:dict(file=item['file'],sha256=item['sha256']) for item in definition['weight_textures']}
    require(fixture['source_weights']==expected, 'sample fixture source PNG hashes differ')
    height_file = checked['package']/'heightfield.json'
    height = read(height_file)
    require(sha(height_file)==fixture['heightfield_sha256'] and
            sha(checked['package']/height['height_file'])==fixture['source_height_sha256'],
            'sample fixture height source differs')
    require(len(fixture['points'])==4 and fixture['byte_tolerance']==1, 'source sample fixture coverage differs')
    names = [item['id'] for item in definition['layers']]
    for index,(point,xy) in enumerate(zip(fixture['points'],POINTS_ROS)):
        require(point['ros_xy_m']==list(xy), 'source grass fixture point moved')
        require(close(point['world_cm'][0],xy[0]*100) and close(point['world_cm'][1],-xy[1]*100),
                'source grass fixture coordinate conversion differs')
        require(point['expected_grass']==('zero' if index==0 else 'positive'), 'source field predicate differs')
        rows_by_name(point['layers'],names)


def validate_point_readback(native, fixture, definition):
    require(native.get('schema')=='rmsim_grass_weight_samples_v1' and len(native.get('samples',[]))==4,
            'native sample helper schema or point count differs')
    names = [item['id'] for item in definition['layers']]
    result = []
    for index,(sample,expected) in enumerate(zip(native['samples'],fixture['points'])):
        require(sample.get('valid_data') is True, 'native grass sample data is not valid')
        require(sample.get('component') and sample.get('grid_side')==128,
                'native grass sample component/grid differs from the saved 127-quad landscape')
        require(len(sample['world_cm'])==3 and
                all(close(a,b,.0001) for a,b in zip(sample['world_cm'],expected['world_cm'])),
                'native grass sample world coordinates differ')
        rows = rows_by_name(sample['layers'],names)
        expected_rows = rows_by_name(expected['layers'],names)
        records,nonzero,max_corner_error,max_interpolation_error=[],0,0.,0.
        for name in names:
            row,wanted=rows[name],expected_rows[name]
            corners=row.get('corners')
            require(isinstance(corners,list) and len(corners)==4 and
                    all(number(v) and 0<=v<=255 and v==round(v) for v in corners),
                    name+': native grass corner bytes are invalid')
            world_corners=row.get('corner_world_xy_cm')
            require(isinstance(world_corners,list) and len(world_corners)==4 and
                    all(len(a)==2 and all(close(x,y,.001) for x,y in zip(a,b))
                        for a,b in zip(world_corners,expected['corner_world_xy_cm'])),
                    name+': native grass corners sample another world grid')
            error=max(abs(a-b) for a,b in zip(corners,wanted['expected_corners_byte']))
            interpolation=row.get('interpolated_byte')
            require(number(interpolation), 'native grass interpolation is nonfinite')
            delta=abs(interpolation-wanted['expected_interpolated_byte'])
            require(error<=1 and delta<=1, name+': baked grass weight differs from original PNG samples')
            max_corner_error=max(max_corner_error,error)
            max_interpolation_error=max(max_interpolation_error,delta)
            nonzero+=interpolation>0
            records.append(dict(name=name,actual_corners=corners,expected_corners=wanted['expected_corners_byte'],
                actual_interpolated_byte=interpolation,expected_interpolated_byte=wanted['expected_interpolated_byte'],
                max_corner_error_byte=error,interpolation_error_byte=delta))
        require((nonzero==0) if index==0 else (nonzero>0),
                'native station/grass-field zero versus positive predicate differs')
        result.append(dict(id=expected['id'],status='passed',world_cm=sample['world_cm'],
            component=sample['component'],nonzero_native_layers=nonzero,
            max_corner_error_byte=max_corner_error,max_interpolation_error_byte=max_interpolation_error,
            layers=records))
    return result


def main():
    import unreal
    command=unreal.SystemLibrary.get_command_line()
    def arg(name,default=None):
        match=re.search(r'(?:^|\s)-'+re.escape(name)+r'=(?:"([^"]+)"|([^\s]+))',command,re.IGNORECASE)
        return (match.group(1) or match.group(2)) if match else default
    root=Path(__file__).resolve().parents[2]
    package=Path(arg('MapPackage',str(root/'assets/maps/wt_vlaanderen'))).resolve(strict=True)
    fixture_path=Path(arg('GrassSampleFixture',str(root/'artifacts/maps/wt-scene-20261118/foliage-01/native-sample-fixture.json'))).resolve(strict=True)
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output=Path(arg('GrassAuditOutput',str(root/'artifacts/maps'/('grass-reload-'+stamp)))).resolve()
    output.mkdir(parents=True,exist_ok=True)
    target=output/'verification.json'
    require(not target.exists(), 'use a fresh grass audit output')
    report=dict(schema='wt_grass_native_reload_verification_v1',status='running',
        started_utc=stamp,verifier_sha256=sha(__file__),checks=[])
    snapshots={}
    def check(name,passed,**details):
        report['checks'].append(dict(name=name,passed=bool(passed),**details))
        save(target,report)
        require(passed,'grass audit failed: '+name)
    try:
        checked=validate_foliage(package)
        definition=checked['definition']
        descriptor=read(package/'map.json')
        folder='/Game/RMSim/Maps/'+descriptor['id']
        require(descriptor['id']=='wt_vlaanderen','this native fixture targets original Flanders')
        fixture=read(fixture_path)
        validate_sample_fixture(fixture,checked)
        report.update(definition_sha256=checked['definition_sha256'],
            source_level_sha256=definition['source_level_sha256'],fixture_file=str(fixture_path),
            fixture_sha256=sha(fixture_path),world_asset=folder+'/World')
        check('bound_source_and_field_fixture',True,models=len(checked['models']),
              weights=len(checked['weights']),layers=len(checked['layers']))
        content=Path(unreal.Paths.project_content_dir()).resolve()
        disk=content/folder[len('/Game/'):]
        level_file=disk/'World.umap'
        require(level_file.is_file(),'saved foliage World is missing')
        snapshot_files=list((disk/'Foliage').rglob('*'))
        snapshot_files += list(disk.glob('World*'))
        height=read(package/'heightfield.json')
        terrain_package=height['material'].split('.',1)[0]
        snapshot_files += [content/(terrain_package[len('/Game/'):]+'.uasset'),
                           checked['definition_path'],package/'scene-source.json',package/'map.json']
        snapshots={str(p.resolve()):sha(p) for p in snapshot_files if p.is_file()}
        require(len(snapshots)>20,'saved grass resource inventory is incomplete')
        report['before_hashes']=snapshots
        project=Path(unreal.Paths.project_dir()).resolve()
        module=project/'Binaries/Win64/UnrealEditor-RMSim.dll'
        if module.is_file():report['module_sha256']=sha(module)
        report['native_api_signatures']={name:getattr(unreal.MapVegetationLibrary,name).__doc__
            for name in ('describe_landscape_grass','describe_landscape_grass_samples')}
        world=unreal.EditorLoadingAndSavingUtils.load_map(folder+'/World')
        require(world is not None,'saved World failed to load')
        settings=list(unreal.GameplayStatics.get_all_actors_of_class(world,unreal.WorldSettings))
        require(len(settings)==1,'saved WorldSettings count differs')
        tags=[str(value) for value in settings[0].get_editor_property('tags')]
        check('saved_world_source_identity',
            tags.count('RMSimMapId='+descriptor['id'])==1 and
            tags.count('RMSimSourceSha256='+definition['source_level_sha256'])==1,tags=tags)
        landscapes=list(unreal.GameplayStatics.get_all_actors_of_class(world,unreal.LandscapeProxy))
        require(len(landscapes)==1,'expected one saved source Landscape')
        landscape=landscapes[0]
        native=json.loads(unreal.MapVegetationLibrary.describe_landscape_grass(landscape))
        report['native_saved_grass']=native
        rows=validate_layer_readback(native,definition,folder)
        for row in rows:
            check('saved_grass_layer_'+row['id'],True,**row)
        points=[unreal.Vector(*point['world_cm']) for point in fixture['points']]
        native_samples=json.loads(unreal.MapVegetationLibrary.describe_landscape_grass_samples(landscape,points))
        report['native_weight_samples']=native_samples
        field_rows=validate_point_readback(native_samples,fixture,definition)
        for row in field_rows:
            check('native_weight_field_'+row['id'],True,**row)
        for model in definition['models']:
            path=folder+'/Foliage/Meshes/'+model['id']+'.'+model['id']
            mesh=unreal.load_asset(path)
            require(isinstance(mesh,unreal.StaticMesh),'saved source grass mesh missing')
            geometry=json.loads(unreal.MapImportLibrary.describe_map_mesh(mesh))
            box=mesh.get_bounding_box()
            actual={'lo':[box.min.x,box.min.y,box.min.z],'hi':[box.max.x,box.max.y,box.max.z]}
            expected={edge:[model['bounds_m'][('hi' if edge=='lo' else 'lo') if axis==1 else edge][axis]*(-100 if axis==1 else 100)
                            for axis in range(3)] for edge in ('lo','hi')}
            error=max(abs(actual[edge][axis]-expected[edge][axis]) for edge in ('lo','hi') for axis in range(3))
            check('saved_grass_mesh_'+model['id'],
                geometry.get('triangles')==model['triangles'] and geometry.get('uv_channels')==1 and error<=.01
                and unreal.EditorAssetLibrary.get_metadata_tag(mesh,'RMSimGrassSourceSHA256')==model['sha256'],
                asset=path,native_geometry=geometry,bounds_error_cm=error,expected_bounds_cm=expected)
        after={path:sha(Path(path)) for path in snapshots}
        check('saved_assets_and_sources_unchanged',after==snapshots,file_count=len(snapshots))
        report['status']='passed'
        report['scope']='saved native grass weights/types/geometry and four source-bound sampled fields; runtime population and appearance checked separately'
        report['read_only_operations']=True
        report['finished_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
        save(target,report)
        summary=dict(schema='wt_grass_native_reload_summary_v1',status='passed',
            definition_sha256=checked['definition_sha256'],source_level_sha256=definition['source_level_sha256'],
            world_sha256=snapshots[str(level_file.resolve())],module_sha256=report.get('module_sha256'),
            native_layers=len(rows),native_meshes=len(definition['models']),field_points=len(field_rows),
            source_station_nonzero_layers=field_rows[0]['nonzero_native_layers'],
            positive_grass_field_layers=[r['nonzero_native_layers'] for r in field_rows[1:]],
            max_corner_error_byte=max(r['max_corner_error_byte'] for r in field_rows),
            max_interpolation_error_byte=max(r['max_interpolation_error_byte'] for r in field_rows),
            components_with_valid_data=native['components_with_valid_data'],
            components_with_weights=native['components_with_weights'],
            check_count=len(report['checks']),unchanged_saved_files=len(snapshots),
            verification_file=str(target),verification_sha256=sha(target),scope=report['scope'])
        save(output/'summary.json',summary)
        unreal.log('WT_GRASS_RELOAD_VERIFIED '+json.dumps(summary))
    except Exception as error:
        report['status']='failed'
        report['error']=str(error)
        report['traceback']=traceback.format_exc()
        if snapshots:
            report['saved_files_unchanged_after_failure']=all(Path(p).is_file() and sha(Path(p))==v for p,v in snapshots.items())
        save(target,report)
        unreal.log_error('WT_GRASS_RELOAD_FAILED '+str(error))
        raise


if __name__=='__main__':
    main()
