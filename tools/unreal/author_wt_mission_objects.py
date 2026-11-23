"""Author only the original Flanders zeppelin supplement into the saved Flanders World.

Run inside the project editor after a caller-owned World backup and project mutex.
This script never starts/stops Unreal, builds code, or modifies the original game.
All created assets and actors have an explicit MissionObjects namespace and tag.
"""
import argparse
import datetime
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
import traceback
import unreal

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import wt_materials as w
import wt_object_normals
from import_wt_scene import import_mesh, unwrap
from wt_rendinst_decals import contract as decal_contract

FOLDER = "/Game/RMSim/Maps/wt_vlaanderen/MissionObjects"
WORLD = "/Game/RMSim/Maps/wt_vlaanderen/World"
OWNER = "RMSim.WTMissionObjects.Zeppelin"
FIXED = "RMSim.WTFixedLandmark"
SUPPORTED = {
    "rendinst_simple": "Surface", "rendinst_layered": "Surface",
    "rendinst_perlin_layered": "Surface", "rendinst_simple_glass": "Glass",
    "rendinst_deferred_decal": "DecalDiffuse",
    "rendinst_deferred_modulate2x_decal": "DecalModulate",
}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def vector(value):
    return [float(value.x), float(value.y), float(value.z)]


def transform(value):
    q = value.rotation
    return dict(position=vector(value.translation), rotation=[float(q.x), float(q.y), float(q.z), float(q.w)],
                scale=vector(value.scale3d))


def tags(obj, field="tags"):
    return [str(t) for t in obj.get_editor_property(field)]


def all_actors(world):
    return list(unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Actor))


def static_components(actor):
    return list(actor.get_components_by_class(unreal.StaticMeshComponent))


def mesh_path(component):
    mesh = component.get_editor_property("static_mesh")
    return mesh.get_path_name() if mesh else ""


def unowned_state(world):
    result = []
    for actor in all_actors(world):
        if OWNER in tags(actor):
            continue
        result.append(dict(path=actor.get_path_name(), type=actor.get_class().get_path_name(),
                           transform=transform(actor.get_actor_transform()), tags=tags(actor),
                           meshes=sorted(mesh_path(c) for c in static_components(actor))))
    payload = json.dumps(sorted(result, key=lambda a: a["path"]), sort_keys=True).encode()
    return dict(actors=len(result), sha256=hashlib.sha256(payload).hexdigest())


def glass_parent(textures):
    """Source T0 alpha + T2 AG normal/R smoothness with lit UE surface translucency."""
    name = "WT_Parent_Glass"
    material = w._load_existing(f"{FOLDER}/Materials/{name}") or unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        name, FOLDER + "/Materials", unreal.Material, unreal.MaterialFactoryNew())
    w._clear_graph(material)
    material.set_editor_property("material_domain", unreal.MaterialDomain.MD_SURFACE)
    material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_DEFAULT_LIT)
    material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
    material.set_editor_property("translucency_lighting_mode", unreal.TranslucencyLightingMode.TLM_SURFACE_PER_PIXEL_LIGHTING)
    material.set_editor_property("two_sided", True)
    material.set_editor_property("tangent_space_normal", True)
    w.EDIT.set_material_usage(material, unreal.MaterialUsage.MATUSAGE_INSTANCED_STATIC_MESHES)
    inputs = {}
    for i in range(7):
        color = i in (0, 3, 5)
        default = "White" if i in (0, 1, 3, 5) else "NormalAG"
        inputs["T" + str(i)] = (w._texture(material, "T" + str(i), textures.get(None, color, default), color), "")
    for i in range(3):
        inputs["UV" + str(i)] = (w._expr(material, unreal.MaterialExpressionTextureCoordinate, coordinate_index=i), "")
    for key, value in (("Tiles", (1, 1, 1, 1)), ("Mask", (1, 1, 1, 1)), ("LayerWeights", (1, 1, 1, 0))):
        inputs[key] = (w._vector(material, key, value), "RGBA")
    for key, value in (("Family", 0), ("AlphaEnabled", 1), ("AlphaFromT1", 0)):
        inputs[key] = (w._scalar(material, key, value), "")
    inputs["World"] = (w._expr(material, unreal.MaterialExpressionWorldPosition), "")
    inputs["GeometricNormal"] = (w._expr(material, unreal.MaterialExpressionVertexNormalWS), "")
    node = w._custom(material, wt_object_normals.surface_code(w.OBJECT_CODE), inputs)
    normal = w._expr(material, unreal.MaterialExpressionReroute)
    w._wire(node, "NormalOut", normal, "")
    w._prop(normal, "", w.P.MP_NORMAL)
    w._prop(node, "OpacityOut", w.P.MP_OPACITY)
    # Original glass.dshl initializes metalness to zero; diffuse alpha is surface opaqueness.
    w._prop(w._expr(material, unreal.MaterialExpressionConstant, r=0), "", w.P.MP_METALLIC)
    w._save_parent(material)
    unreal.EditorAssetLibrary.set_metadata_tag(material, "RMSim.SourceGlass",
        "rendinst_simple_glass: T0.rgb/color T0.a/opacity T2.ag/normal T2.r/smoothness; UE surface forward translucency")
    unreal.EditorAssetLibrary.save_loaded_asset(material)
    return material


def author_materials(package, manifest):
    extraction = Path(manifest["extraction_root"])
    textures = w.Textures(package, extraction, FOLDER)
    mapping = {}
    parents = {}
    records = []
    for spec in manifest["materials"]:
        shader = spec["shader"]
        if shader not in SUPPORTED:
            raise ValueError("Unsupported zeppelin shader; refusing opaque fallback: " + shader)
        flavor = SUPPORTED[shader]
        if w.material_flavor(shader) != flavor:
            raise ValueError("Source shader adapter changed: " + shader)
        if flavor not in parents:
            parents[flavor] = glass_parent(textures) if flavor == "Glass" else w.parent_material(FOLDER, flavor, textures)
        parent = parents[flavor]
        identity, material = w.make_instance(FOLDER, spec, parent, textures)
        mapping[identity] = material
        expected_domain = unreal.MaterialDomain.MD_DEFERRED_DECAL if flavor == "DecalDiffuse" else unreal.MaterialDomain.MD_SURFACE
        expected_blend = (unreal.BlendMode.BLEND_TRANSLUCENT if flavor in ("Glass", "DecalDiffuse") else
                          unreal.BlendMode.BLEND_MODULATE if flavor == "DecalModulate" else unreal.BlendMode.BLEND_MASKED)
        if parent.get_editor_property("material_domain") != expected_domain or parent.get_editor_property("blend_mode") != expected_blend:
            raise ValueError("Source material transparency/domain was not preserved")
        inputs = []
        required = (0,) if flavor == "DecalModulate" else (0, 2) if flavor == "DecalDiffuse" else range(7)
        for index in required:
            source_name = spec["texture_slots"][index] if index < len(spec["texture_slots"]) else None
            if not source_name:
                continue
            actual = w.EDIT.get_material_instance_texture_parameter_value(material, "T" + str(index))
            color = flavor == "DecalDiffuse" and index == 0 if flavor.startswith("Decal") else index in (0, 3, 5)
            wanted = textures.get(source_name, color)
            if actual is None or actual.get_path_name() != wanted.get_path_name():
                raise ValueError("Original texture slot was not bound: " + identity + "/" + str(index))
            if bool(actual.get_editor_property("srgb")) != bool(color):
                raise ValueError("Original color/data texture interpretation differs")
            inputs.append(dict(slot=index, source=source_name, asset=actual.get_path_name(), srgb=bool(color)))
        record = dict(id=identity, source_shader=shader, asset=material.get_path_name(), parent=parent.get_path_name(),
                      native_domain=str(expected_domain), native_blend=str(expected_blend), source_textures=inputs,
                      source_parameters=spec["parameters"])
        if flavor == "Glass":
            if parent.get_editor_property("translucency_lighting_mode") != unreal.TranslucencyLightingMode.TLM_SURFACE_PER_PIXEL_LIGHTING:
                raise ValueError("Glass surface lighting does not include normals/specular")
            record["channels"] = dict(opacity="T0.a", normal="T2.ag", roughness="clamp(1-T2.r,0.04,1)", metallic=0)
            record["renderer_adaptation"] = "Lit UE alpha translucency; Dagor raytraced refraction, environmental probes and tint/fog passes are not reproduced."
        elif flavor.startswith("Decal"):
            record["source_decal"] = decal_contract(spec)
            record["renderer_adaptation"] = ("Linear color multiplication using native Surface/Unlit/Modulate; source smoothness/metalness multiplication remains metadata."
                                               if flavor == "DecalModulate" else
                                               "Native mesh DeferredDecal; shared UE opacity uses original diffuse alpha and gamma.")
        elif shader == "rendinst_perlin_layered":
            record["renderer_adaptation"] = "Original layers/UV/AG normal/R smoothness/B metalness; shared map adapter uses texture height weights without Dagor spatial Perlin."
        elif "use_painting" in spec["parameters"]:
            record["renderer_adaptation"] = "Original source textures and layer parameters; global Dagor palette painting is retained as source metadata."
        params = w.parameters(spec["parameters"])
        if params.get("atest", [0])[0] > 0 and flavor == "Surface":
            override = material.get_editor_property("base_property_overrides")
            threshold = float(override.get_editor_property("opacity_mask_clip_value"))
            expected = params["atest"][0] / 255.
            if not override.get_editor_property("override_opacity_mask_clip_value") or abs(threshold - expected) > 1e-6:
                raise ValueError("Original torn-fabric alpha threshold changed")
            record["alpha_test"] = threshold
        records.append(record)
    return mapping, dict(materials=records, textures=textures.imported)


def read_rows(package, part):
    data = (package / part["instances_file"]).read_bytes()
    if data[:4] != b"RMI1":
        raise ValueError("Invalid instance stream")
    count = struct.unpack_from("<I", data, 4)[0]
    if len(data) != 8 + count * 40:
        raise ValueError("Invalid instance stream length")
    return [struct.unpack_from("<10f", data, 8 + i * 40) for i in range(count)]


def native_readback(world, package, manifest):
    declarations = {p["id"]: p for p in manifest["meshes"]}
    binding = {p["id"]: p for p in read(package / "instances.json")["parts"]}
    owned = [a for a in all_actors(world) if OWNER in tags(a)]
    if len(owned) != len(declarations):
        raise ValueError("Owned zeppelin actor count differs")
    seen = set()
    records = []
    visual_instances = 0
    collision_instances = 0
    world_lo = [math.inf] * 3
    world_hi = [-math.inf] * 3
    maximum_pose_error = 0.
    for actor in owned:
        name = actor.get_actor_label()
        if name not in declarations or name in seen or FIXED not in tags(actor):
            raise ValueError("Unexpected owned zeppelin actor")
        seen.add(name)
        spec = declarations[name]
        expected = binding[name]
        rows = read_rows(package, spec)
        components = list(actor.get_components_by_class(unreal.InstancedStaticMeshComponent))
        expected_components = 2 if expected["collision_asset"] else 1
        if len(components) != expected_components:
            raise ValueError("Mission actor has unexpected instanced components")
        parts = []
        component_paths = set()
        for component in components:
            path = mesh_path(component)
            blocking = path == expected["collision_asset"]
            if path not in (expected["visual_asset"], expected["collision_asset"]) or path in component_paths:
                raise ValueError("Mission component uses an unrelated/duplicate mesh")
            component_paths.add(path)
            if component.get_instance_count() != len(rows):
                raise ValueError("Native zeppelin instance count differs")
            if component.get_editor_property("mobility") != unreal.ComponentMobility.STATIC or component.is_simulating_physics():
                raise ValueError("Zeppelin source landmark must remain fixed")
            if FIXED not in tags(component, "component_tags"):
                raise ValueError("Missing fixed landmark component ownership")
            enabled = component.get_collision_enabled()
            wanted = unreal.CollisionEnabled.QUERY_AND_PHYSICS if blocking else unreal.CollisionEnabled.NO_COLLISION
            if enabled != wanted:
                raise ValueError("Native mission collision policy differs")
            if blocking:
                if component.get_collision_object_type() != unreal.CollisionChannel.ECC_WORLD_STATIC:
                    raise ValueError("Original wreck collider is not WorldStatic")
                mesh = component.get_editor_property("static_mesh")
                body = mesh.get_editor_property("body_setup")
                if body is None or body.get_editor_property("collision_trace_flag") != unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE:
                    raise ValueError("Original frame/opening collision lost complex triangle mode")
                if not body.get_editor_property("double_sided_geometry"):
                    raise ValueError("Original static source collision must be two-sided")
                collision_instances += len(rows)
            else:
                visual_instances += len(rows)
            native_geometry = json.loads(unreal.MapImportLibrary.describe_map_mesh(component.get_editor_property("static_mesh")))
            maximum = 0.
            for index, row in enumerate(rows):
                value = component.get_instance_transform(index, world_space=True)
                if not isinstance(value, unreal.Transform):
                    raise ValueError("Native instance readback returned no transform")
                pose = transform(value)
                expected_position = [row[0] * 100, -row[1] * 100, row[2] * 100]
                error = max(abs(a - b) for a, b in zip(pose["position"], expected_position))
                if error > .025 or max(abs(a - b) for a, b in zip(pose["rotation"], [0, 0, 0, 1])) > 1e-6 or max(abs(a - 1) for a in pose["scale"]) > 1e-6:
                    raise ValueError("Native source affine-baked instance placement changed")
                maximum = max(maximum, error)
                if not blocking:
                    bounds = component.get_editor_property("static_mesh").get_bounding_box()
                    for axis, low, high in zip(range(3), vector(bounds.min), vector(bounds.max)):
                        world_lo[axis] = min(world_lo[axis], low + pose["position"][axis])
                        world_hi[axis] = max(world_hi[axis], high + pose["position"][axis])
            maximum_pose_error = max(maximum_pose_error, maximum)
            parts.append(dict(mesh=path, blocking=blocking, collision_enabled=str(enabled), native_geometry=native_geometry,
                              instances=len(rows), maximum_pose_error_cm=maximum, mobility="Static", simulating_physics=False))
        records.append(dict(actor=actor.get_path_name(), label=name, components=parts))
    expected_bounds = {
        "lo": [manifest["bounds_m"]["lo"][0] * 100, -manifest["bounds_m"]["hi"][1] * 100, manifest["bounds_m"]["lo"][2] * 100],
        "hi": [manifest["bounds_m"]["hi"][0] * 100, -manifest["bounds_m"]["lo"][1] * 100, manifest["bounds_m"]["hi"][2] * 100],
    }
    maximum_bounds_error = max(abs(a - b) for actual, expected in ((world_lo, expected_bounds["lo"]), (world_hi, expected_bounds["hi"])) for a, b in zip(actual, expected))
    if maximum_bounds_error > .05 or visual_instances != manifest["source_coverage"]["rendinst_instances"]:
        raise ValueError("Native original wreck world coverage differs")
    return dict(actors=len(records), visual_instances=visual_instances, collision_instances=collision_instances,
                native_visual_bounds_ue_cm=dict(lo=world_lo, hi=world_hi), source_visual_bounds_ue_cm=expected_bounds,
                max_bounds_error_cm=maximum_bounds_error, max_instance_pose_error_cm=maximum_pose_error, records=records)


def hit_result_fields(hit, known_blocking=None):
    """Use the reflected struct API; NativeBreakFunc is hidden from GameplayStatics."""
    diagnostic = dict(type=type(hit).__name__, available_names=[name for name in dir(hit) if not name.startswith("__")])
    details = None
    if hasattr(hit, "to_tuple"):
        try:
            details = hit.to_tuple()
            diagnostic["tuple_types"] = [type(v).__name__ for v in details]
            diagnostic["tuple_length"] = len(details)
            if isinstance(details, (tuple, list)):
                blocking = known_blocking
                if blocking is None and details and isinstance(details[0], bool):
                    blocking = details[0]
                component = next((v for v in details if isinstance(v, unreal.PrimitiveComponent)), None)
                if isinstance(blocking, bool) and (not blocking or component is not None):
                    return blocking, component, "HitResult.to_tuple"
        except Exception as error:
            diagnostic["to_tuple_error"] = str(error)
    values = {}
    for name in ("blocking_hit", "b_blocking_hit", "bBlockingHit", "hit_component", "component"):
        try:
            values[name] = hit.get_editor_property(name)
        except Exception:
            try:
                values[name] = getattr(hit, name)
            except Exception:
                pass
    blocking = known_blocking
    if blocking is None:
        blocking = next((values[name] for name in ("blocking_hit", "b_blocking_hit", "bBlockingHit")
                         if isinstance(values.get(name), bool)), None)
    component = next((values[name] for name in ("hit_component", "component")
                      if isinstance(values.get(name), unreal.PrimitiveComponent)), None)
    diagnostic["readable_fields"] = {name: type(value).__name__ for name, value in values.items()}
    if isinstance(blocking, bool) and (not blocking or component is not None):
        return blocking, component, "HitResult.reflected_fields"
    raise RuntimeError("Unrecognized HitResult struct API: " + json.dumps(diagnostic))


def native_probes(world, package):
    source = read(package / "collision-probes.json")
    if source["source_manifest_sha256"] != digest(package / "mission-source.json"):
        raise ValueError("Collision probe source identity changed")
    selected = {}
    attempts = []
    for kind, candidates in (("hard_frame_hit", source["hits"]), ("frame_opening_miss", source["misses"])):
        for candidate in candidates:
            result = unreal.SystemLibrary.line_trace_single(world, unreal.Vector(*candidate["start_ue_cm"]),
                unreal.Vector(*candidate["end_ue_cm"]), unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, True, [],
                unreal.DrawDebugTrace.NONE, True)
            if result is None:
                blocking, component, contract = False, None, "None"
            elif isinstance(result, unreal.HitResult):
                blocking, component, contract = hit_result_fields(result)
            elif isinstance(result, tuple) and len(result) == 2:
                hit, success = result if isinstance(result[0], unreal.HitResult) else (result[1], result[0])
                if not isinstance(success, bool) or (success and not isinstance(hit, unreal.HitResult)):
                    raise RuntimeError("Invalid LineTraceSingle tuple values")
                blocking, component, contract = hit_result_fields(hit, success) if success else (False, None, "tuple_no_hit")
            else:
                raise RuntimeError("Native trace output contract changed: " + type(result).__name__ + "; available=" + repr(dir(result)))
            item = dict(candidate, blocking=bool(blocking), python_return_contract=contract)
            if blocking:
                item["actor"] = component.get_owner().get_actor_label()
                item["mesh"] = mesh_path(component) if isinstance(component, unreal.StaticMeshComponent) else ""
                item["owned_fixed_collider"] = bool(OWNER in tags(component.get_owner()) and FIXED in tags(component, "component_tags"))
            expected_asset = FOLDER + "/Collision/col_" + candidate["prepared_mesh_id"] + ".col_" + candidate["prepared_mesh_id"]
            passed = (bool(blocking) and item.get("owned_fixed_collider") and item.get("mesh") == expected_asset) if kind == "hard_frame_hit" else not bool(blocking)
            item["passed"] = bool(passed)
            attempts.append(item)
            if passed:
                selected[kind] = item
                break
        if kind not in selected:
            raise ValueError("Native original wreck probe failed: " + kind + "; attempts=" + json.dumps(attempts))
    return dict(status="passed", selected=selected, attempts=attempts, trace="Visibility, complex, real saved-world components")


def author(args):
    package = Path(args.package).resolve(strict=True)
    evidence = Path(args.evidence).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    manifest = read(package / "scene-source.json")
    verification = read(package / "preparation-verification.json")
    if manifest["id"] != "wt_vlaanderen_zeppelin" or verification["status"] != "passed" or verification["scene_manifest_sha256"] != digest(package / "scene-source.json"):
        raise ValueError("Prepared mission package is unverified")
    for part in manifest["meshes"] + manifest["collision_meshes"]:
        if digest(package / part["file"]) != part["sha256"]:
            raise ValueError("Prepared original source geometry changed")
    report = dict(schema="wt_mission_authoring_v1", status="running",
                  started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), world=WORLD,
                  source_manifest_sha256=digest(package / "mission-source.json"),
                  scene_manifest_sha256=digest(package / "scene-source.json"), source=str(package),
                  engine=str(unreal.SystemLibrary.get_engine_version()), importer_sha256=digest(Path(__file__)),
                  native_asset_root=FOLDER, ownership_tag=OWNER, fixed_tag=FIXED, meshes=[])
    report["dependency_script_hashes"] = {name: digest(Path(__file__).with_name(name)) for name in ("import_wt_scene.py", "wt_materials.py", "wt_object_normals.py", "wt_rendinst_decals.py", "wt_mesh_geometry.py", "wt_material_slots.py", "wt_vertex_opacity.py")}
    report["collision_probes_sha256"] = digest(package / "collision-probes.json")
    target = evidence / "native-authoring.json"
    save(target, report)
    try:
        if not args.verify_only:
            mats, report["materials"] = author_materials(package, manifest)
            save(target, report)
            cache_file = package / "ue-mesh-import-cache.json"
            cache = read(cache_file) if cache_file.exists() else dict(schema="wt_scene_mesh_cache_v1", assets={})
            if cache.get("schema") != "wt_scene_mesh_cache_v1":
                raise ValueError("Unknown mission asset cache format")
            for collision, specifications in ((False, manifest["meshes"]), (True, manifest["collision_meshes"])):
                for spec in specifications:
                    asset = f"{FOLDER}/{'Collision' if collision else 'Meshes'}/{spec['id']}.{spec['id']}"
                    report["meshes"].append(import_mesh(package, asset, spec, collision, mats, cache, cache_file,
                                                        FOLDER, scene_source_sha256=report["source_manifest_sha256"]))
                    save(target, report)
            unreal.log("WT_MISSION_ASSETS_READY " + str(len(report["meshes"])))
        world = unreal.EditorLoadingAndSavingUtils.load_map(WORLD)
        if world is None:
            raise RuntimeError("Cannot load existing Flanders World")
        before = unowned_state(world)
        report["unowned_before"] = before
        if not args.verify_only:
            subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
            prior = [a for a in all_actors(world) if OWNER in tags(a)]
            for actor in prior:
                if any(not mesh_path(c).startswith(FOLDER + "/") for c in static_components(actor)):
                    raise ValueError("Owned actor contains unrelated content; refusing removal")
            for actor in prior:
                if not subsystem.destroy_actor(actor):
                    raise RuntimeError("Cannot replace previous owned zeppelin actor")
            before_paths = {a.get_path_name() for a in all_actors(world)}
            imported = unwrap(unreal.MapImportLibrary.import_instances(world, str(package / "instances.json")), "ImportMissionInstances")
            if imported != manifest["source_coverage"]["rendinst_instances"]:
                raise ValueError("Original mission instance count differs")
            new = [a for a in all_actors(world) if a.get_path_name() not in before_paths]
            if len(new) != len(manifest["meshes"]):
                raise ValueError("Unexpected number of newly owned mission actors")
            expected_labels = {p["id"] for p in manifest["meshes"]}
            for actor in new:
                if actor.get_actor_label() not in expected_labels:
                    raise ValueError("Unexpected newly authored actor")
                actor.set_editor_property("tags", [unreal.Name(OWNER), unreal.Name(FIXED)])
                for component in static_components(actor):
                    if not mesh_path(component).startswith(FOLDER + "/"):
                        raise ValueError("New zeppelin component is outside its owned folder")
                    component.set_editor_property("component_tags", [unreal.Name(OWNER), unreal.Name(FIXED)])
                    component.set_simulate_physics(False)
            report["replaced_owned_actors"] = len(prior)
        after = unowned_state(world)
        if before != after:
            raise ValueError("Unrelated saved-world actors changed")
        report["unowned_after"] = after
        report["native_readback_before_save"] = native_readback(world, package, manifest)
        if not args.verify_only:
            if not unreal.EditorLoadingAndSavingUtils.save_map(world, WORLD):
                raise RuntimeError("Could not save the authored Flanders World")
            world = unreal.EditorLoadingAndSavingUtils.load_map(WORLD)
            if world is None:
                raise RuntimeError("Saved Flanders World could not reload")
        report["native_readback"] = native_readback(world, package, manifest)
        report["native_collision_probes"] = native_probes(world, package)
        if unowned_state(world) != before:
            raise ValueError("Unrelated actors changed across saved-world reload")
        report["world_file_sha256"] = digest(REPO / "unreal/RMSim/Content/RMSim/Maps/wt_vlaanderen/World.umap")
        report["status"] = "passed"
        report["saved_world_reloaded"] = not args.verify_only
        save(target, report)
        unreal.log("WT_MISSION_AUTHORED " + json.dumps(dict(status=report["status"], actors=report["native_readback"]["actors"],
             visual_instances=report["native_readback"]["visual_instances"], collision_instances=report["native_readback"]["collision_instances"],
             collision_probes=report["native_collision_probes"]["status"], report=str(target))))
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        report["traceback"] = traceback.format_exc()
        save(target, report)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", default=str(REPO / "assets/maps/wt_vlaanderen/mission-objects"))
    parser.add_argument("--evidence", default=str(REPO / "artifacts/maps/wt-zeppelin-minimap-20261119/native-zeppelin-01"))
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    author(args)


if __name__ == "__main__":
    main()
