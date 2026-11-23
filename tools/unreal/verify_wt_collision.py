"""Read-only native reload audit of derived Flanders collision assets/cells.

Loads the saved World, creates only a transient audit actor, tests source-bound
surface/gap rays and synchronous spatial-cell changes, and saves no assets.
"""
import datetime
import json
import math
from pathlib import Path
import re
import sys
import traceback
import unreal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wt_collision import load_contract, read, save, digest, asset_file, bounds_record, check_native


def argument(name):
    match = re.search(r'-'+name+r'=(?:"([^"]+)"|(\S+))', unreal.SystemLibrary.get_command_line())
    if not match:
        raise ValueError('pass -'+name+'=<path>')
    return match.group(1) or match.group(2)


def ue(point):
    return [point[0]*100, -point[1]*100, point[2]*100]


def xyz(vector):
    return [float(vector.x), float(vector.y), float(vector.z)]


def native(mesh):
    value = unreal.MapImportLibrary.describe_generated_collision(mesh)
    if not isinstance(value, str) or not value:
        raise RuntimeError('native collision description is empty')
    return json.loads(value)


def create(world, manifest_file, anchor, radius):
    result = unreal.MapImportLibrary.create_generated_collision_audit(
        world, str(manifest_file), unreal.Vector(*ue(anchor)), radius)
    if not isinstance(result, tuple) or len(result) != 2:
        raise RuntimeError('CreateGeneratedCollisionAudit must return (component,error)')
    component, error = result
    if component is None or error:
        raise RuntimeError('native audit initialization failed: '+str(error))
    return component


def describe(component):
    value = component.describe()
    if not isinstance(value, str) or not value:
        raise RuntimeError('generated runtime report is empty')
    record = json.loads(value)
    if record.get('schema') != 'rmsim_generated_collision_runtime_v1' or record.get('physics_ready') is not True:
        raise ValueError('generated runtime cells are not physics ready')
    return record


def distance(bounds, position):
    return math.sqrt(sum(max(bounds['lo'][i]-position[i], 0., position[i]-bounds['hi'][i])**2
                         for i in range(2)))


def update_expected(cells, active, anchor, position, load_radius, retain_radius):
    wanted = {cell['id'] for cell in cells
              if min(distance(cell['bounds_m'], anchor), distance(cell['bounds_m'], position)) <= load_radius}
    retained = {cell['id'] for cell in cells if cell['id'] in active
                and min(distance(cell['bounds_m'], anchor), distance(cell['bounds_m'], position)) <= retain_radius}
    return wanted | retained


def check_population(record, cells, active):
    selected = [cell for cell in cells if cell['id'] in active]
    expected = dict(active_cells=len(selected),
                    active_native_bodies=sum(cell['instances'] for cell in selected),
                    active_shapes=sum(cell['instances'] for cell in selected),
                    active_source_instances=sum(cell['source_instances'] for cell in selected))
    for key, value in expected.items():
        if record.get(key) != value:
            raise ValueError('native population differs from spatial source bounds: '+key)
    if record['active_native_bodies'] >= sum(cell['instances'] for cell in cells):
        raise ValueError('local streaming unexpectedly instantiated the entire source scene')
    return expected


def enum_bindings():
    # UE5.8 CollisionProfile renames configured entries through ScriptName.
    # PyWrapperEnum.cpp exposes cast(integer); direct construction is forbidden.
    entries = {
        'Visibility': unreal.TraceTypeQuery.cast(0),
        'RMSimLidar': unreal.TraceTypeQuery.cast(2),
        'lidar_collision_channel': unreal.CollisionChannel.cast(14),
        # EngineTypes.h:1345 exports ECollisionResponse as CollisionResponseType.
        # CollisionResponse is the unrelated FCollisionResponse struct.
        'block_response': unreal.CollisionResponseType.cast(2),
    }
    expected = dict(Visibility=0, RMSimLidar=2, lidar_collision_channel=14, block_response=2)
    for key, value in entries.items():
        if value.value != expected[key]:
            raise ValueError('native collision enum value differs: '+key)
    for key in ('RMSimLidar', 'lidar_collision_channel'):
        if str(entries[key].get_display_name()) != 'RMSimLidar':
            raise ValueError('native collision enum does not identify RMSimLidar: '+key)
    return entries


def trace(world, fixture, ignored, channel, complex_trace, bindings):
    start, end = ue(fixture['start_ros_m']), ue(fixture['end_ros_m'])
    query_type = bindings[channel]
    raw = unreal.SystemLibrary.line_trace_single(
        world, unreal.Vector(*start), unreal.Vector(*end), query_type, complex_trace,
        ignored, unreal.DrawDebugTrace.NONE, False)
    expected = fixture['expected']
    result = dict(name=fixture['name'], kind=fixture['kind'], channel=channel,
                  trace_complex=complex_trace, expected=expected, start_cm=start, end_cm=end)
    if raw is None:
        result['blocking_hit'] = False
        if expected['hit']:
            raise ValueError('native ray missed source geometry: '+fixture['name'])
        return result
    if not isinstance(raw, unreal.HitResult):
        raise RuntimeError('LineTraceSingle must return HitResult or None')
    values = raw.to_tuple()
    if not isinstance(values, tuple) or len(values) != 18 or values[0] is not True:
        raise RuntimeError('unexpected UE5.8 HitResult native-break contract')
    if not expected['hit']:
        raise ValueError('native collision filled a verified source air gap: '+fixture['name'])
    component = values[10]
    if (component is None
            or not component.component_has_tag('RMSimGeneratedCollision')
            or component.get_collision_response_to_channel(bindings['lidar_collision_channel'])
               != bindings['block_response']):
        raise ValueError('ray did not hit the generated source-bound BlockAll collider')
    if expected.get('model') == 'world_static_rails':
        source_index = expected['source_instance']['object_index']
        expected_mesh = 'gc_static_rail_%04d' % source_index
        if (not isinstance(component, unreal.InstancedStaticMeshComponent)
                or component.static_mesh.get_name() != expected_mesh
                or component.get_instance_count() != 1 or int(values[13]) != 0):
            raise ValueError('ray hit a different original steel rail instance: '+fixture['name'])
    impact = xyz(values[5])
    target = ue(expected['impact_ros_m'])
    error = math.dist(impact, target)
    if error > fixture['tolerance_cm']:
        raise ValueError('native/source ray position error '+str(error)+'cm: '+fixture['name'])
    result.update(blocking_hit=True, initial_overlap=bool(values[1]),
                  impact_cm=impact, source_expected_cm=target, max_error_cm=error,
                  actor=values[9].get_path_name() if values[9] else None,
                  component=component.get_path_name(), item=int(values[13]),
                  face_index=int(values[15]))
    return result


def all_traces(world, fixture, ignored, bindings):
    return [trace(world, fixture, ignored, channel, complex_trace, bindings)
            for channel in ('Visibility', 'RMSimLidar')
            for complex_trace in (False, True)]


def verify(source_file, fixture_file, output):
    source_file = Path(source_file).resolve(strict=True)
    fixture_file = Path(fixture_file).resolve(strict=True)
    evidence = Path(output).resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    content = Path(unreal.Paths.project_content_dir()).resolve()
    world_file = content/'RMSim/Maps/wt_vlaanderen/World.umap'
    root, manifest, contract = load_contract(source_file)
    fixtures = read(fixture_file)
    if (fixtures.get('schema') != 'wt_generated_collision_source_probes_v1'
            or fixtures.get('status') != 'passed'
            or fixtures['contract']['manifest_sha256'] != contract['manifest_sha256']
            or fixtures['contract']['source_sha256'] != contract['source_sha256']):
        raise ValueError('source-ray fixtures are stale or unbound')
    protected = [world_file, source_file, root/'manifest.json']
    protected.extend(asset_file(content, m['asset']) for m in manifest['meshes'])
    before = {str(path): digest(path) for path in protected}
    report = dict(schema='wt_generated_collision_native_audit_v1', status='running',
                  started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  contract=contract, fixtures_sha256=digest(fixture_file),
                  script_sha256=digest(Path(__file__)), protected_before=before,
                  assets=[], source_rays=[], boundary=[], ranges=[])
    save(evidence/'verification.json', report)
    component = None
    try:
        # The project has one custom trace channel. UE5.8 CollisionProfile first
        # inserts Visibility, Camera, then configured trace channels by index.
        config = Path(unreal.Paths.project_config_dir()).resolve()/'DefaultEngine.ini'
        text = config.read_text(encoding='utf-8-sig')
        custom = [line.strip() for line in text.splitlines() if line.strip().startswith('+DefaultChannelResponses=')]
        expected = '+DefaultChannelResponses=(Channel=ECC_GameTraceChannel1,DefaultResponse=ECR_Block,bTraceType=True,bStaticObject=False,Name="RMSimLidar")'
        if custom != [expected]:
            raise ValueError('trace-query3 mapping needs re-audit for changed channel configuration')
        report['trace_mapping'] = dict(config_sha256=digest(config), Visibility=0, Camera=1, RMSimLidar=2,
                                      basis='zero-based ETraceTypeQuery; UE5.8 CollisionProfile.cpp LoadProfileConfig ordering')
        bindings = enum_bindings()
        report['native_enum_bindings'] = {key: dict(name=value.name, value=value.value,
                                                   display_name=str(value.get_display_name()))
                                          for key, value in bindings.items()}
        for spec in manifest['meshes']:
            mesh = unreal.load_asset(spec['asset'])
            if not isinstance(mesh, unreal.StaticMesh):
                raise ValueError('saved generated mesh unavailable')
            actual = native(mesh)
            check_native(actual, spec, manifest)
            report['assets'].append(dict(id=spec['id'], native=actual, bounds=bounds_record(mesh, spec)))
        level = '/Game/RMSim/Maps/wt_vlaanderen/World'
        if not unreal.EditorLoadingAndSavingUtils.load_map(level):
            raise RuntimeError('could not reload saved World')
        world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
        ignored = list(unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Actor))
        spawn = fixtures['spawn_ros_m']
        component = create(world, root/'manifest.json', spawn, fixtures['query_range_m'])
        active = set()
        # Follow the runtime 8m trigger without refreshing at the 7.99m stage.
        for stage in fixtures['boundary_stages']:
            position = stage['position_ros_m']
            if stage['name'] != 'initial' and stage['refresh']:
                error = component.refresh_at_world_point(unreal.Vector(*ue(position)))
                if not isinstance(error, str) or error:
                    raise RuntimeError('synchronous cell refresh failed: '+str(error))
            population = describe(component)
            if stage['refresh']:
                active = update_expected(manifest['cells'], active, spawn, position,
                                         population['load_radius_m'], population['retain_radius_m'])
            expected_population = check_population(population, manifest['cells'], active)
            rays = []
            for fixture in stage['probes']:
                rays.extend(all_traces(world, fixture, ignored, bindings))
            # Pinned spawn remains physically queryable after moving 160m away.
            if stage['name'] == 'outside_spawn_footprint':
                anchor_probe = next(p for p in fixtures['probes'] if p['name'] == 'russia_rails_sleeper_a_top')
                rays.extend(all_traces(world, anchor_probe, ignored, bindings))
            report['boundary'].append(dict(name=stage['name'], position_ros_m=position,
                                           refresh_performed=stage['refresh'],
                                           expected_population=expected_population,
                                           native=population, rays=rays))
            save(evidence/'verification.json', report)
        for fixture in fixtures['probes']:
            error = component.refresh_at_world_point(unreal.Vector(*ue(fixture['anchor_ros_m'])))
            if not isinstance(error, str) or error:
                raise RuntimeError('source probe cell refresh failed: '+str(error))
            report['source_rays'].extend(all_traces(world, fixture, ignored, bindings))
        report['ranges'].append(dict(query_range_m=40., runtime=describe(component)))
        owner = component.get_owner()
        owner.destroy_actor()
        component = None
        # Supported profile upper limit: actual body/shape count and load timings.
        component = create(world, root/'manifest.json', spawn, 200.)
        maximum = describe(component)
        active = update_expected(manifest['cells'], set(), spawn, spawn,
                                 maximum['load_radius_m'], maximum['retain_radius_m'])
        expected_population = check_population(maximum, manifest['cells'], active)
        report['ranges'].append(dict(query_range_m=200., runtime=maximum,
                                     expected_population=expected_population))
        rays = report['source_rays']+[r for stage in report['boundary'] for r in stage['rays']]
        report.update(status='passed', native_mesh_assets=len(report['assets']),
                      ray_checks=len(rays), source_gap_queries=sum(not r['expected']['hit'] for r in rays),
                      max_source_ray_error_cm=max(r.get('max_error_cm', 0.) for r in rays),
                      streaming_stage_checks=len(report['boundary']), profile_range_checks=len(report['ranges']),
                      generated_source_instances=manifest['source_instances'],
                      runtime_phase='GameMode TG_PostPhysics; synchronous refresh completed for next physics frame',
                      initial_physics_preload=True, spawn_anchor_query_verified=True,
                      vehicle_driving_verified=False)
    except Exception as error:
        report.update(status='failed', error=str(error), traceback=traceback.format_exc())
        raise
    finally:
        if component is not None:
            component.get_owner().destroy_actor()
        after = {str(path): digest(path) for path in protected}
        report['protected_after'] = after
        report['protected_files_unchanged'] = after == before
        if after != before:
            report.update(status='failed', error='read-only audit changed protected saved files')
        report['finished_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save(evidence/'verification.json', report)
    if report['status'] != 'passed':
        raise RuntimeError(report['error'])
    unreal.log('WT_GENERATED_COLLISION_AUDIT_PASSED '+str(evidence/'verification.json'))


if __name__ == '__main__':
    verify(argument('SceneSource'), argument('CollisionFixtures'), argument('CollisionAudit'))
