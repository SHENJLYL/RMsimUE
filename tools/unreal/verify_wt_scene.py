"""Independent native audit of the saved original Flanders world.

Run only after import has finished, in a separate UnrealEditor-Cmd process:
  RMSim.uproject -run=pythonscript -script=<this file>
  -SceneSource=<package/scene-source.json> [-SceneAuditOutput=<fresh directory>]

Loads the actual saved level. Checks native actors/components and native collision
queries against source counts and four original uint16 height samples. It does
not modify or save the level, and does not certify vehicle driving or rendering.
"""
import collections
import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import sys
import traceback

import unreal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wt_mesh_geometry import triangle_accounting, verify_native_material_triangles


HEIGHT_SAMPLES_GRID = ((2056, 2040), (2040, 2040), (2056, 2024), (2072, 2040))
HEIGHT_TOLERANCE_CM = 0.1
COLLIDER_PART = "prop_sicily_kiosk_a"
COLLIDER_INSTANCE = 2


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, data):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def package_file(package, relative):
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("source path must be package relative")
    result = (package / relative).resolve(strict=True)
    if not result.is_relative_to(package) or not result.is_file():
        raise ValueError("source file escapes package")
    return result


def xyz(vector):
    result = [float(vector.x), float(vector.y), float(vector.z)]
    if not all(math.isfinite(value) for value in result):
        raise ValueError("native query returned nonfinite coordinates")
    return result


def saved_asset_file(content, asset):
    package_name = asset.split(".", 1)[0]
    if not package_name.startswith("/Game/"):
        raise ValueError("saved mesh must be in the project Content directory")
    result = (content / (package_name[len("/Game/"):] + ".uasset")).resolve(strict=True)
    if not result.is_relative_to(content) or not result.is_file():
        raise ValueError("saved mesh file escapes the project Content directory")
    return result


def native_geometry_record(mesh, specification, accounting):
    """Inspect the saved World's actual mesh object and its rendered LOD0 sections."""
    # DescribeMapMesh returns FString alone. Do not apply bool/out tuple unpacking.
    raw = unreal.MapImportLibrary.describe_map_mesh(mesh)
    if not isinstance(raw, str) or not raw:
        raise RuntimeError("DescribeMapMesh must return nonempty JSON FString; got " + type(raw).__name__)
    native = json.loads(raw)
    if not isinstance(native, dict) or type(native.get("triangles")) is not int:
        raise ValueError("native geometry must contain an integer triangle count")
    expected = accounting["expected_native_triangles"]
    if native["triangles"] != expected:
        raise ValueError("saved native triangles differ: actual=" + str(native["triangles"])
                         + ", expected=" + str(expected)
                         + ", source=" + str(accounting["source_triangles"])
                         + ", proven_coincident=" + str(accounting["strictly_coincident_source_triangles"]))
    sections = native.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ValueError("native mesh must contain rendered LOD0 sections")
    slots = [str(slot.get_editor_property("material_slot_name"))
             for slot in mesh.get_editor_property("static_materials")]
    for section in sections:
        index = section.get("material_index")
        count = section.get("triangles")
        if (type(index) is not int or not 0 <= index < len(slots)
                or type(count) is not int or count < 0
                or section.get("material_slot") != slots[index]):
            raise ValueError("native section has invalid material identity/count: " + json.dumps(section))
    if sum(section["triangles"] for section in sections) != native["triangles"]:
        raise ValueError("native LOD0 section counts do not cover the complete mesh")
    material_counts = verify_native_material_triangles(accounting, native)
    if specification.get("leaf_adapter") and (
            native.get("uv_channels") != 5 or native.get("full_precision_uvs") is not True):
        raise ValueError("saved leaf mesh must retain five full-precision UV channels")
    if specification.get("source_vertex_opacity") and (
            native.get("uv_channels") != 2 or native.get("full_precision_uvs") is not True):
        raise ValueError("saved source vertex opacity mesh must retain two full-precision UV channels")
    return dict(status="passed", mesh_id=specification["id"], asset=mesh.get_path_name(),
                geometry_accounting=accounting, native_geometry=native,
                verified_native_material_triangles=material_counts,
                source_vertex_opacity_verified=bool(specification.get("source_vertex_opacity")),
                scope="saved_world_referenced_static_mesh_rendered_lod0")


def native_trace(world, start, end, ignored):
    """Use reflected engine functions; never manufacture a missing hit."""
    raw = unreal.SystemLibrary.line_trace_single(
        world, unreal.Vector(*start), unreal.Vector(*end),
        unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, True, ignored,
        unreal.DrawDebugTrace.NONE, False)
    api_shape = type(raw).__name__
    query = dict(api_shape=api_shape, trace_start_cm=list(start), trace_end_cm=list(end),
                 trace_channel="Visibility/TraceTypeQuery1", trace_complex=True,
                 ignored_actor_count=len(ignored))
    # UE 5.8 PyGenUtil.cpp:PackReturnValues removes a bool CPF_ReturnParm when
    # out parameters exist. LineTraceSingle is HitResult on true, None on false.
    if raw is None:
        return dict(query, blocking_hit=False, initial_overlap=False,
                    actor=None, component=None), None, None
    if not isinstance(raw, unreal.HitResult):
        raise RuntimeError("LineTraceSingle must return HitResult or None; got " + api_shape)
    # NativeBreakFunc is deliberately omitted from GameplayStatics' Python API.
    # HitResult.HasNativeBreak selects it through PyWrapperStruct::BreakStruct;
    # to_tuple returns its 18 outputs, retaining both bool out parameters.
    values = raw.to_tuple()
    if not isinstance(values, tuple) or len(values) != 18:
        raise RuntimeError("HitResult.to_tuple must return the 18 UE 5.8 native-break outputs")
    if not isinstance(values[0], bool) or not isinstance(values[1], bool):
        raise RuntimeError("HitResult.to_tuple blocking/overlap outputs must be bool")
    blocking, initial = values[0], values[1]
    if not blocking:
        raise RuntimeError("LineTraceSingle returned success with no blocking hit")
    actor, component = values[9], values[10]
    record = dict(query, blocking_hit=blocking, initial_overlap=initial,
                  impact_point_cm=xyz(values[5]), impact_normal=xyz(values[7]),
                  distance_cm=float(values[3]), hit_item=int(values[13]),
                  face_index=int(values[15]),
                  actor=actor.get_path_name() if actor else None,
                  actor_label=actor.get_actor_label() if actor else None,
                  component=component.get_path_name() if component else None)
    if component and isinstance(component, unreal.StaticMeshComponent):
        mesh = component.get_editor_property("static_mesh")
        record["static_mesh"] = mesh.get_path_name() if mesh else None
    return record, actor, component


def transformed_bounds(spec, values):
    """Independent source quaternion/scale application, before ROS-to-UE conversion."""
    x, y, z, w = values[3:7]
    rotation = (
        (1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)),
        (2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)),
        (2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)))
    bounds = spec["bounds_m"]
    center = [(a+b)/2 for a, b in zip(bounds["lo"], bounds["hi"])]
    extent = [(b-a)/2 for a, b in zip(bounds["lo"], bounds["hi"])]
    placed = [values[i] + sum(rotation[i][j]*values[7+j]*center[j] for j in range(3))
              for i in range(3)]
    radius = [sum(abs(rotation[i][j]*values[7+j])*extent[j] for j in range(3))
              for i in range(3)]
    return [placed[i]-radius[i] for i in range(3)], [placed[i]+radius[i] for i in range(3)]



def verified_mission_supplement(world, actors, package, source, content, proof_inputs):
    """Accept only the hash-bound original mission supplement; never filter by prefix."""
    owner = "RMSim.WTMissionObjects.Zeppelin"
    owned = [actor for actor in actors
             if owner in [str(tag) for tag in actor.get_editor_property("tags")]]
    supplement = package / "mission-objects"
    if not supplement.exists() and not supplement.is_symlink():
        if owned:
            raise ValueError("mission actors have no original source supplement")
        return None, set()

    # Optional source/author dependencies are loaded only for a present supplement.
    from wt_mission_supplements import preflight
    from author_wt_mission_objects import native_readback
    checked = preflight(package, source)
    if checked is None:
        raise ValueError("present mission supplement has no verified source contract")
    supplement = Path(checked["package"])
    manifest = read(package_file(supplement, "scene-source.json"))
    bindings = read(package_file(supplement, "instances.json"))["parts"]
    expected_labels = {part["id"] for part in manifest["meshes"]}
    if len(expected_labels) != len(manifest["meshes"]):
        raise ValueError("duplicate source mission actor labels")
    expected_meshes = {}
    for role, directory in (("meshes", "Meshes"), ("collision_meshes", "Collision")):
        for spec in manifest[role]:
            asset = "/Game/RMSim/Maps/" + source["id"] + "/MissionObjects/" + directory + "/" + spec["id"] + "." + spec["id"]
            expected_meshes[asset] = (spec, role == "collision_meshes")
    cache_file = package_file(supplement, "ue-mesh-import-cache.json")
    cache = read(cache_file)
    if cache.get("schema") != "wt_scene_mesh_cache_v1" or set(cache.get("assets", {})) != set(expected_meshes):
        raise ValueError("mission saved mesh cache identity differs")
    native_files = []
    for asset, (spec, collision) in expected_meshes.items():
        entry = cache["assets"][asset]
        signature = entry.get("signature", {})
        path = saved_asset_file(content, asset)
        actual_hash = digest(path)
        if (signature.get("fbx_sha256") != spec["sha256"]
                or signature.get("material_slots") != spec["material_slots"]
                or signature.get("triangle_collision") is not collision
                or actual_hash != entry.get("ue_file_sha256")):
            raise ValueError("mission saved mesh/source hash binding differs: " + asset)
        native_files.append(dict(asset=asset, role="saved_mission_mesh", path=str(path), sha256=actual_hash))
    files = {"scene-source.json", "mission-source.json", "source-provenance.json",
             "preparation-verification.json", "instances.json", "collision-probes.json",
             "ue-mesh-import-cache.json", "textures/source-textures.json"}
    for field in ("geometry_file_sha256", "instance_file_sha256", "texture_file_sha256"):
        files.update(checked[field])
    proof_inputs.extend(dict(role="mission_source", path=str(package_file(supplement, name)),
                             sha256=digest(package_file(supplement, name))) for name in sorted(files))
    proof_inputs.extend(native_files)
    for name in ("wt_mission_supplements.py", "author_wt_mission_objects.py"):
        path = Path(__file__).resolve().with_name(name)
        proof_inputs.append(dict(role="mission_validation_script", path=str(path), sha256=digest(path)))
    native = native_readback(world, supplement, manifest)
    verified_paths = {record["actor"] for record in native["records"]}
    if (len(verified_paths) != len(expected_labels)
            or {record["label"] for record in native["records"]} != expected_labels
            or verified_paths != {actor.get_path_name() for actor in owned}
            or native["actors"] != checked["visual_assets"]
            or native["visual_instances"] != checked["instances"]
            or native["collision_instances"] != checked["colliding_instances"]):
        raise ValueError("mission native actor/instance coverage differs from original source")
    expected_components = len(bindings) + sum(bool(part["collision_asset"]) for part in bindings)
    actual_components = 0
    for actor in owned:
        components = list(actor.get_components_by_class(unreal.InstancedStaticMeshComponent))
        hisms = list(actor.get_components_by_class(unreal.HierarchicalInstancedStaticMeshComponent))
        if len(components) != len(hisms):
            raise ValueError("mission source instance component class differs")
        actual_components += len(hisms)
        for component in hisms:
            blocking = component.get_collision_enabled() != unreal.CollisionEnabled.NO_COLLISION
            if bool(component.get_editor_property("hidden_in_game")) != blocking:
                raise ValueError("mission visual/collision visibility role differs")
    if (actual_components != expected_components
            or actual_components != checked["visual_assets"] + checked["collision_assets"]
            or sum(len(record["components"]) for record in native["records"]) != expected_components):
        raise ValueError("mission HISM coverage differs from original source")
    return dict(status="passed", source=checked, native=native,
                loaded_hism_components=actual_components, native_mesh_hashes=native_files,
                ignored_unknown_components=False), verified_paths


def verify(source_file, output=None):
    source_file = Path(source_file).resolve(strict=True)
    package = source_file.parent
    source = read(source_file)
    if source.get("schema") != "rm_scene_map_source_v1" or source.get("id") != "wt_vlaanderen":
        raise ValueError("this audit uses the original wt_vlaanderen source fixtures")
    if source.get("units") != "m" or source.get("coordinate_system") != "ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP":
        raise ValueError("unexpected source coordinates")
    folder = "/Game/RMSim/Maps/" + source["id"]
    level = folder + "/World"
    started = datetime.datetime.now(datetime.timezone.utc)
    evidence = Path(output).resolve() if output else package.parents[2] / "artifacts/maps/wt_existing" / (
        "scene-audit-" + started.strftime("%Y%m%dT%H%M%S.%fZ"))
    evidence.mkdir(parents=True, exist_ok=False)
    report = dict(schema="wt_native_scene_audit_v1", status="running", complete=False,
                  started_utc=started.isoformat(), source_manifest=str(source_file),
                  source_sha256=source["source_sha256"], manifest_sha256=digest(source_file),
                  map_asset=level, engine=None,
                  scope="loaded_saved_editor_world_native_components_and_collision",
                  vehicle_driving_verified=False, rendered_appearance_verified=False,
                  checks=[], height_samples=[])
    report["audit_script_sha256"] = digest(Path(__file__).resolve())
    report["geometry_accounting_script_sha256"] = digest(Path(__file__).resolve().with_name("wt_mesh_geometry.py"))

    def check(name, passed, **details):
        report["checks"].append(dict(name=name, passed=bool(passed), **details))
        save(evidence / "verification.json", report)
        if not passed:
            raise RuntimeError(name + ": " + json.dumps(details))

    save(evidence / "verification.json", report)
    try:
        project = Path(unreal.Paths.project_dir()).resolve()
        content = Path(unreal.Paths.project_content_dir()).resolve()
        level_file = content / ("RMSim/Maps/" + source["id"] + "/World.umap")
        report["engine"] = unreal.SystemLibrary.get_engine_version()
        report["native_api_signatures"] = {
            name: (getattr(function, "__doc__", "") or "undocumented").splitlines()[0]
            for name, function in (
                ("LineTraceSingle", unreal.SystemLibrary.line_trace_single),
                ("HitResult.to_tuple", unreal.HitResult.to_tuple),
                ("LoadMap", unreal.EditorLoadingAndSavingUtils.load_map),
                ("GetAllActorsOfClass", unreal.GameplayStatics.get_all_actors_of_class),
                ("DescribeMapMesh", unreal.MapImportLibrary.describe_map_mesh))}
        module = project / "Binaries/Win64/UnrealEditor-RMSim.dll"
        if module.is_file():
            report["module_sha256"] = digest(module)
        check("native_api_available", True, signatures=report["native_api_signatures"])
        imported = read(package / "ue-scene-import-report.json")
        check("import_provenance",
              imported.get("status") == "imported_pending_runtime_validation"
              and imported.get("manifest_sha256") == report["manifest_sha256"]
              and imported.get("source_sha256") == source["source_sha256"]
              and level_file.is_file(),
              import_status=imported.get("status"), map_exists=level_file.is_file())
        before_hash = digest(level_file)
        report["map_file_sha256"] = before_hash
        check("saved_map_hash", before_hash == imported.get("map_file_sha256"),
              actual=before_hash, imported=imported.get("map_file_sha256"))

        from wt_vertex_opacity import validate_scene_vertex_opacity
        report["vertex_opacity_validation_script_sha256"] = digest(
            Path(__file__).resolve().with_name("wt_vertex_opacity.py"))
        opacity_records = validate_scene_vertex_opacity(package, source)
        report["source_vertex_opacity"] = opacity_records
        check("source_vertex_opacity_proofs", True,
              mesh_count=len(opacity_records), mesh_ids=sorted(opacity_records))

        expected_visual = {folder + "/Meshes/" + spec["id"] + "." + spec["id"]: spec
                           for spec in source["meshes"]}
        expected_collision = {folder + "/Collision/" + spec["id"] + "." + spec["id"]: spec
                              for spec in source["collision_meshes"]}
        expected_meshes = dict(expected_visual, **expected_collision)
        accounting_by_asset = {
            asset: triangle_accounting(package, spec, source["source_sha256"])
            for asset, spec in expected_meshes.items()}
        proof_inputs = [dict(role="source_manifest", path=str(source_file), sha256=report["manifest_sha256"])]
        from wt_runtime_virtual_texture import inspect_rvt_world
        report["ground_decal_rvt_script_sha256"] = digest(
            Path(__file__).resolve().with_name("wt_runtime_virtual_texture.py"))
        check("imported_ground_decal_rvt_contract",
              imported.get("ground_decal_rvt", {}).get("schema") == "wt_ground_decal_rvt_v1"
              and imported.get("ground_decal_rvt", {}).get("status") == "configured",
              imported_rvt=imported.get("ground_decal_rvt"))
        rvt_asset_path = folder + "/Materials/WT_GroundDecalRVT.WT_GroundDecalRVT"
        rvt_file = saved_asset_file(content, rvt_asset_path)
        proof_inputs.append(dict(asset=rvt_asset_path, role="saved_ground_decal_rvt",
                                 path=str(rvt_file), sha256=digest(rvt_file)))
        project_config = project / "Config/DefaultEngine.ini"
        proof_inputs.append(dict(role="project_rendering_config", path=str(project_config),
                                 sha256=digest(project_config)))
        for asset, accounting in accounting_by_asset.items():
            specification = expected_meshes[asset]
            files = []
            if accounting["geometry_audit_sha256"] is not None:
                files.append(("geometry_proof", package_file(package, accounting["geometry_audit_file"])))
            opacity = opacity_records.get(specification["id"])
            if opacity is not None:
                files.append(("vertex_opacity_proof", package_file(
                    package, opacity["contract"]["raw_verification_file"])))
            if not files:
                continue
            native_file = saved_asset_file(content, asset)
            files.extend([("audited_fbx", package_file(package, specification["file"])),
                          ("saved_native_mesh", native_file)])
            for extension in (".uexp", ".ubulk"):
                sidecar = native_file.with_suffix(extension)
                if sidecar.is_file():
                    files.append(("saved_native_mesh" + extension, sidecar))
            proof_inputs.extend(dict(asset=asset, role=role, path=str(path), sha256=digest(path))
                                for role, path in files)
        report["proof_bound_file_hashes"] = proof_inputs
        decal_asset = folder + "/Meshes/world_decal_000.world_decal_000"
        decal_accounting = accounting_by_asset.get(decal_asset, {})
        check("source_degenerate_geometry_fixture",
              decal_accounting.get("source_triangles") == 87864
              and decal_accounting.get("strictly_coincident_source_triangles") == 34
              and decal_accounting.get("expected_native_triangles") == 87830
              and len(decal_accounting.get("expected_native_material_triangles", {})) == 35
              and decal_accounting.get("geometry_audit_sha256") is not None,
              asset=decal_asset, accounting=decal_accounting)

        world = unreal.EditorLoadingAndSavingUtils.load_map(str(level_file))
        check("saved_world_loaded", isinstance(world, unreal.World),
              world=world.get_path_name() if isinstance(world, unreal.World) else None,
              return_type=type(world).__name__)
        report["ground_decal_rvt"] = inspect_rvt_world(world, source)
        check("native_ground_decal_rvt_configuration",
              report["ground_decal_rvt"]["asset"] == imported["ground_decal_rvt"]["asset"]
              and report["ground_decal_rvt"]["writer"]["source_fbx_sha256"]
                  == imported["ground_decal_rvt"]["writer"]["source_fbx_sha256"],
              ground_decal_rvt=report["ground_decal_rvt"])
        actors = list(unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Actor))
        settings = list(unreal.GameplayStatics.get_all_actors_of_class(world, unreal.WorldSettings))
        expected_tags = ["RMSimMapId=" + source["id"], "RMSimSourceSha256=" + source["source_sha256"]]
        tags = [str(tag) for tag in settings[0].get_editor_property("tags")] if len(settings) == 1 else []
        identity_tags = [tag for tag in tags if tag.startswith(("RMSimMapId=", "RMSimSourceSha256="))]
        check("world_source_identity", len(settings) == 1 and sorted(identity_tags) == sorted(expected_tags),
              settings_count=len(settings), expected=expected_tags, actual=identity_tags)

        mission, mission_actor_paths = verified_mission_supplement(
            world, actors, package, source, content, proof_inputs)
        report["mission_objects"] = mission
        if mission is not None:
            check("native_original_mission_supplement", mission["status"] == "passed",
                  actors=mission["native"]["actors"],
                  hism_components=mission["loaded_hism_components"],
                  visual_instances=mission["native"]["visual_instances"],
                  collision_instances=mission["native"]["collision_instances"],
                  source_manifest_sha256=mission["source"]["source_manifest_sha256"],
                  scene_manifest_sha256=mission["source"]["scene_manifest_sha256"])

        instances = read(package_file(package, source["instances"]))
        parts = instances["parts"]
        by_label = {}
        all_hisms = []
        for actor in actors:
            components = list(actor.get_components_by_class(unreal.HierarchicalInstancedStaticMeshComponent))
            if actor.get_path_name() in mission_actor_paths:
                continue
            if components:
                by_label.setdefault(actor.get_actor_label(), []).append((actor, components))
                all_hisms.extend(components)
        errors = []
        expected_placements = 0
        native_placements = 0
        native_collision_placements = 0
        expected_collision_placements = 0
        expected_collision_groups = 0
        native_visual_groups = 0
        native_collision_groups = 0
        native_meshes = {}
        report["native_mesh_geometry"] = native_meshes
        for part in parts:
            specification = expected_visual[part["visual_asset"]]
            data = package_file(package, part["instances_file"]).read_bytes()
            if len(data) < 8 or data[:4] != b"RMI1":
                raise ValueError("invalid independent RMI source input")
            count = struct.unpack_from("<I", data, 4)[0]
            if len(data) != 8 + count * 40 or count != specification["instance_count"]:
                raise ValueError("independent RMI/manifest count mismatch")
            expected_placements += count
            expected_collision_groups += bool(part["collision_asset"])
            expected_collision_placements += count if part["collision_asset"] else 0
            found = by_label.get(part["id"], [])
            if len(found) != 1:
                errors.append(dict(part=part["id"], error="actor count", actual=len(found)))
                continue
            actor, components = found[0]
            visual = [component for component in components
                      if component.get_collision_enabled() == unreal.CollisionEnabled.NO_COLLISION]
            colliders = [component for component in components
                         if component.get_collision_enabled() != unreal.CollisionEnabled.NO_COLLISION]
            native_visual_groups += len(visual)
            native_collision_groups += len(colliders)
            if len(visual) != 1 or len(colliders) != bool(part["collision_asset"]):
                errors.append(dict(part=part["id"], error="component roles",
                                   visual=len(visual), collision=len(colliders)))
                continue
            for component, expected_path, blocking in (
                    [(visual[0], part["visual_asset"], False)]
                    + [(component, part["collision_asset"], True) for component in colliders]):
                mesh = component.get_editor_property("static_mesh")
                actual_count = component.get_instance_count()
                if blocking:
                    native_collision_placements += actual_count
                else:
                    native_placements += actual_count
                if not mesh or mesh.get_path_name() != expected_path or actual_count != count:
                    errors.append(dict(part=part["id"], error="native asset/count",
                                       expected_asset=expected_path, actual_asset=mesh.get_path_name() if mesh else None,
                                       expected_count=count, actual_count=actual_count))
                if mesh and mesh.get_path_name() == expected_path and expected_path not in native_meshes:
                    try:
                        native_meshes[expected_path] = native_geometry_record(
                            mesh, expected_meshes[expected_path], accounting_by_asset[expected_path])
                    except Exception as error:
                        native_meshes[expected_path] = dict(status="failed", asset=expected_path, error=str(error))
                        errors.append(dict(part=part["id"], error="saved native geometry",
                                           asset=expected_path, detail=str(error)))
                if not blocking and mesh:
                    slots = list(mesh.get_editor_property("static_materials"))
                    names = [str(slot.get_editor_property("material_slot_name")) for slot in slots]
                    expected_slots = collections.Counter(specification["material_slots"])
                    actual_slots = collections.Counter(names)
                    if (set(actual_slots) != set(expected_slots)
                            or any(count > expected_slots[name] for name, count in actual_slots.items())):
                        errors.append(dict(part=part["id"], error="saved material identities",
                                           source_slots=specification["material_slots"], native_slots=names))
                    for slot, name in zip(slots, names):
                        material = slot.get_editor_property("material_interface")
                        expected_material = folder + "/Materials/" + name + "." + name
                        if not material or material.get_path_name() != expected_material:
                            errors.append(dict(part=part["id"], error="saved material binding",
                                               expected=expected_material,
                                               actual=material.get_path_name() if material else None))
                if blocking and mesh:
                    body = mesh.get_editor_property("body_setup")
                    if (component.get_collision_enabled() != unreal.CollisionEnabled.QUERY_AND_PHYSICS
                            or not body
                            or body.get_editor_property("collision_trace_flag") != unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE
                            or not body.get_editor_property("double_sided_geometry")):
                        errors.append(dict(part=part["id"], error="source triangle collision configuration"))
                    if not component.get_editor_property("hidden_in_game"):
                        errors.append(dict(part=part["id"], error="collider visibly rendered"))
        report["inventory"] = dict(
            visual_groups=native_visual_groups, expected_visual_groups=len(parts),
            collision_groups=native_collision_groups, expected_collision_groups=expected_collision_groups,
            visual_placements=native_placements, expected_visual_placements=expected_placements,
            collision_placements=native_collision_placements,
            expected_collision_placements=expected_collision_placements,
            source_stored_instances=source["source_coverage"]["source_instances"],
            affine_baked_source_instances=source["source_coverage"]["affine_baked_instances"],
            loaded_hism_components=len(all_hisms), actor_count=len(actors),
            verified_mission_actors=len(mission_actor_paths),
            verified_mission_hism_components=mission["loaded_hism_components"] if mission else 0,
            total_loaded_hism_components=len(all_hisms) + (mission["loaded_hism_components"] if mission else 0))
        check("native_scene_inventory",
              not errors and len(parts) == len(expected_visual) == 726
              and expected_placements == native_placements == 35922
              and native_collision_groups == expected_collision_groups == 642
              and native_collision_placements == expected_collision_placements == 20033
              and len(all_hisms) == len(parts) + expected_collision_groups == 1368
              and set(by_label) == {part["id"] for part in parts},
              errors=errors, **report["inventory"])

        referenced_assets = {asset for part in parts
                             for asset in (part["visual_asset"], part["collision_asset"]) if asset}
        check("saved_native_geometry_coverage", set(native_meshes) == referenced_assets
              and all(record["status"] == "passed" for record in native_meshes.values()),
              expected_mesh_count=len(referenced_assets), actual_mesh_count=len(native_meshes))
        report["geometry_totals"] = dict(
            referenced_meshes=len(native_meshes),
            source_triangles=sum(accounting_by_asset[asset]["source_triangles"] for asset in referenced_assets),
            expected_native_triangles=sum(accounting_by_asset[asset]["expected_native_triangles"]
                                          for asset in referenced_assets),
            actual_native_triangles=sum(record["native_geometry"]["triangles"] for record in native_meshes.values()),
            strictly_coincident_source_triangles=sum(accounting_by_asset[asset]["strictly_coincident_source_triangles"]
                                                     for asset in referenced_assets))

        landscapes = list(unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Landscape))
        check("native_landscape_exists", len(landscapes) == 1, count=len(landscapes))
        landscape = landscapes[0]
        height = read(package_file(package, source["terrain"]))
        height_path = package_file(package, height["height_file"])
        raw = height_path.read_bytes()
        check("original_height_bytes",
              len(raw) == height["width"] * height["height"] * 2
              and hashlib.sha256(raw).hexdigest() == height["source_samples_sha256"],
              byte_count=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        components = list(landscape.get_components_by_class(unreal.LandscapeComponent))
        collision_components = list(landscape.get_components_by_class(unreal.LandscapeHeightfieldCollisionComponent))
        expected_components = math.ceil(height["width"] / 127) * math.ceil(height["height"] / 127)
        expected_scale = [height["cell_m"]*100, height["cell_m"]*100, height["range_m"]*100*128/65535]
        actual_scale = xyz(landscape.get_actor_scale3d())
        check("native_landscape_components",
              len(components) == len(collision_components) == expected_components
              and max(abs(a-b) for a, b in zip(actual_scale, expected_scale)) < 0.0001
              and landscape.get_editor_property("collision_mip_level") == 0,
              render_components=len(components), collision_components=len(collision_components),
              expected_components=expected_components, actual_scale=actual_scale, expected_scale=expected_scale)

        origin = height["origin_ros_m"]
        ignored = [actor for actor in actors if actor != landscape]
        for grid_x, grid_y in HEIGHT_SAMPLES_GRID:
            if not (0 <= grid_x < height["width"] and 0 <= grid_y < height["height"]):
                raise ValueError("height fixture outside original source")
            value = struct.unpack_from("<H", raw, (grid_y*height["width"]+grid_x)*2)[0]
            expected_z = (origin[2]+height["minimum_m"]+value*height["range_m"]/65535)*100
            ue_x = (origin[0]+grid_x*height["cell_m"])*100
            ue_y = (-origin[1]+grid_y*height["cell_m"])*100
            record, hit_actor, hit_component = native_trace(
                world, [ue_x, ue_y, expected_z+1000], [ue_x, ue_y, expected_z-1000], ignored)
            error = abs(record["impact_point_cm"][2]-expected_z) if record["blocking_hit"] else None
            record.update(grid_ij=[grid_x, grid_y], source_u16=value, expected_z_cm=expected_z,
                          query_xy_cm=[ue_x, ue_y], absolute_z_error_cm=error,
                          tolerance_cm=HEIGHT_TOLERANCE_CM,
                          scope="native_landscape_collision_isolated_from_overlying_scene_objects")
            report["height_samples"].append(record)
            check("native_source_height_" + str(grid_x) + "_" + str(grid_y),
                  record["blocking_hit"] and not record["initial_overlap"] and hit_actor == landscape
                  and isinstance(hit_component, unreal.LandscapeHeightfieldCollisionComponent)
                  and error is not None and error <= HEIGHT_TOLERANCE_CM,
                  sample=record)

        part = next(item for item in parts if item["id"] == COLLIDER_PART)
        source_collision = expected_collision.get(part["collision_asset"]) or expected_visual[part["collision_asset"]]
        instance_data = package_file(package, part["instances_file"]).read_bytes()
        values = struct.unpack_from("<10f", instance_data, 8 + COLLIDER_INSTANCE*40)
        low, high = transformed_bounds(source_collision, values)
        x, y = (low[0]+high[0])*50, -(low[1]+high[1])*50
        target_actor = by_label[COLLIDER_PART][0][0]
        hit, hit_actor, hit_component = native_trace(
            world, [x, y, high[2]*100+200], [x, y, low[2]*100-100],
            [actor for actor in actors if actor != target_actor])
        hit.update(source_part=COLLIDER_PART, source_instance=COLLIDER_INSTANCE,
                   source_collision_asset=part["collision_asset"], source_world_bounds_ros_m=dict(lo=low, hi=high),
                   scope="native_original_HISM_collider_isolated_from_other_actors")
        report["source_collider_query"] = hit
        check("native_original_object_collision",
              hit["blocking_hit"] and not hit["initial_overlap"] and hit_actor == target_actor
              and isinstance(hit_component, unreal.HierarchicalInstancedStaticMeshComponent)
              and hit.get("static_mesh") == part["collision_asset"] and hit["hit_item"] == COLLIDER_INSTANCE,
              sample=hit)
        check("audit_did_not_rewrite_saved_map", digest(level_file) == before_hash)
        for item in proof_inputs:
            item["after_sha256"] = digest(Path(item["path"]))
        check("audit_did_not_rewrite_proof_bound_files",
              all(item["sha256"] == item["after_sha256"] for item in proof_inputs),
              file_count=len(proof_inputs))
        report.update(status="passed", complete=True, native_saved_scene_verified=True,
                      finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
        unreal.log("WT_SCENE_AUDIT_COMPLETE " + str(evidence / "verification.json"))
    except Exception as error:
        report.update(status="failed", complete=True, native_saved_scene_verified=False,
                      error=str(error), traceback=traceback.format_exc(),
                      finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
        unreal.log_error("WT_SCENE_AUDIT_FAILED " + str(error))
        raise
    finally:
        save(evidence / "verification.json", report)
    return report


if __name__ == "__main__":
    command = unreal.SystemLibrary.get_command_line()
    source_arg = re.search(r'-SceneSource=(?:"([^"]+)"|(\S+))', command)
    output_arg = re.search(r'-SceneAuditOutput=(?:"([^"]+)"|(\S+))', command)
    if not source_arg:
        raise ValueError("pass -SceneSource=<scene-source.json>")
    verify(source_arg.group(1) or source_arg.group(2),
           (output_arg.group(1) or output_arg.group(2)) if output_arg else None)
