"""Configure the source ground decals' RVT projection in the saved Flanders world.

The caller owns material graphs, project VT settings, World saving and rendering
validation.  This module creates one RVT asset and binds one source HISM writer.
The source mesh, instances and collisions are preserved.

UE 5.8 source contracts used here:
  RuntimeVirtualTexture.cpp: GetProducerDescription / GetClampedTile*;
  RuntimeVirtualTextureComponent.cpp: CalcBounds uses the unit cube [0, 1]^3;
  RuntimeVirtualTextureSetBounds.cpp: actor origin=Bounds.Min, scale=Bounds.Size;
  HierarchicalInstancedStaticMesh.cpp: DrawStaticElements emits RVT batches.
"""
import math
import re

import unreal


FOLDER = "/Game/RMSim/Maps/wt_vlaanderen"
ASSET_NAME = "WT_GroundDecalRVT"
WRITER_ID = "world_decal_000"
VOLUME_LABEL = "RMSim_WT_GroundDecalRVT"
VOLUME_TAG = "RMSim.SourceGroundDecalRVT.v1"
SCHEMA = "wt_ground_decal_rvt_v1"
MATERIAL_TYPE = "BASECOLORNORMALSPECULARMASKYCOCG"

# UE fields store exponents/indices, not literal tile dimensions.  The ordinary
# page-table limit is 2^12 tiles; adaptive addressing is deliberately disabled.
SETTINGS = {
    "adaptive": False,
    "tile_count": 12,
    "tile_size": 2,          # 1 << (2 + 6) = 256 pixels
    "tile_border_size": 2,   # 2 * 2 = 4 pixels per edge
    "compress_textures": True,
    "clear_textures": True,
    "single_physical_space": True,
    "private_space": True,
    "continuous_update": False,
    "remove_low_mips": 0,
}


def _require(condition, message):
    if not condition:
        raise RuntimeError("ground decal RVT: " + message)


def _xyz(value):
    result = [float(value.x), float(value.y), float(value.z)]
    _require(all(math.isfinite(x) for x in result), "nonfinite native vector")
    return result


def _close(actual, expected, tolerance=1e-5):
    return len(actual) == len(expected) and all(
        math.isfinite(a) and abs(a-b) <= tolerance for a, b in zip(actual, expected))


def _material_type():
    enum = unreal.RuntimeVirtualTextureMaterialType
    names = [name for name in dir(enum)
             if name.replace("_", "").upper() == MATERIAL_TYPE]
    _require(len(names) == 1, "cannot identify exact Mask YCoCg material type: " + str(names))
    return getattr(enum, names[0])


def _enabled():
    value = unreal.SystemLibrary.get_console_variable_int_value("r.VirtualTextures")
    _require(value == 1, "r.VirtualTextures must equal 1 in this process; configure the project and restart")
    return value


def _asset_path(folder):
    _require(folder == FOLDER, "this adapter is scoped to the original wt_vlaanderen package")
    return folder + "/Materials/" + ASSET_NAME


def _asset_state(rvt, folder=FOLDER):
    _require(isinstance(rvt, unreal.RuntimeVirtualTexture), "RVT asset has the wrong class")
    expected = _asset_path(folder) + "." + ASSET_NAME
    _require(rvt.get_path_name() == expected, "unexpected RVT asset path")
    values = {name: rvt.get_editor_property(name) for name in SETTINGS}
    for name, expected_value in SETTINGS.items():
        _require(values[name] == expected_value, "asset setting differs: " + name)
    material_type = rvt.get_editor_property("material_type")
    _require(material_type == _material_type(), "asset must retain BaseColor_Normal_Specular_Mask_YCoCg")
    size = int(rvt.get_size())
    page_table_size = int(rvt.get_page_table_size())
    _require(size == 1048576 and page_table_size == 4096, "native tile-index interpretation differs")
    return dict(asset=rvt.get_path_name(), material_type=str(material_type), settings=values,
                native_asset_max_virtual_size=size, native_asset_page_table_size=page_table_size,
                nominal_physical_formats=["PF_DXT5", "PF_BC5", "PF_DXT5"],
                compressed_bytes_per_physical_page=3 * 264 * 264,
                nominal_resolution_scope="asset_settings_before_device_profile_bias",
                clear_mask_value=128.0 / 255.0)


def _pool_state():
    """Read the real config CDO and mirror its documented ordered match rule.

    FindPoolConfig is native C++, not a reflected Python function.  The report
    distinguishes this observed-config match from GPU allocation/occupancy.
    """
    # UVirtualTexturePoolConfig has no exported Python class in this 5.8 build.
    # PyCore::GetDefaultObject accepts an actual UClass from load_class; read the
    # native editor properties by their C++ names, without generated-name glue.
    config_class = unreal.load_class(None, "/Script/Engine.VirtualTexturePoolConfig")
    _require(config_class is not None, "native VirtualTexturePoolConfig class is unavailable")
    config = unreal.get_default_object(config_class)
    target = ["PF_DXT5", "PF_BC5", "PF_DXT5"]

    def format_name(value):
        matches = re.findall(r"(?:PF_)?(?:DXT5|BC5)\b", str(value))
        return "PF_" + matches[0].removeprefix("PF_") if len(matches) == 1 else str(value)

    observed = []
    chosen = None
    for collection, native_collection in (("transient_pools", "TransientPools"), ("pools", "Pools")):
        values = list(config.get_editor_property(native_collection))
        for index in range(len(values)-1, -1, -1):
            entry = values[index]
            record = dict(collection=collection, index=index,
                          formats=[format_name(x) for x in entry.get_editor_property("Formats")])
            for name, native_name in (
                    ("min_tile_size", "MinTileSize"), ("max_tile_size", "MaxTileSize"),
                    ("size_in_megabyte", "SizeInMegabyte"),
                    ("enable_residency_mip_map_bias", "bEnableResidencyMipMapBias"),
                    ("residency_mip_map_bias_group", "ResidencyMipMapBiasGroup"),
                    ("allow_size_scale", "bAllowSizeScale"),
                    ("min_scaled_size_in_megabyte", "MinScaledSizeInMegabyte"),
                    ("max_scaled_size_in_megabyte", "MaxScaledSizeInMegabyte")):
                record[name] = entry.get_editor_property(native_name)
            record["matches"] = (record["formats"] == target and record["min_tile_size"] <= 264
                                 and (record["max_tile_size"] == 0 or record["max_tile_size"] >= 264))
            observed.append(record)
            if chosen is None and record["matches"]:
                chosen = record
    _require(chosen is not None, "project must configure the DXT5/BC5/DXT5 pool for 264px bordered tiles")
    _require(chosen["size_in_megabyte"] == 256 and chosen["allow_size_scale"] is False,
             "matched local pool must have a fixed 256 MiB limit: " + str(chosen))
    page_bytes = 3 * 264 * 264
    tiles_per_side = math.isqrt(256 * 1024 * 1024 // page_bytes)
    return dict(config_class=config.get_class().get_name(),
                default_size_in_megabyte=config.get_editor_property("DefaultSizeInMegabyte"),
                pool_auto_grow_in_editor=config.get_editor_property("bPoolAutoGrowInEditor"),
                matching_tile_size_including_border=264, matched_config=chosen,
                observed_configs_in_lookup_order=observed,
                configured_limit_bytes=256 * 1024 * 1024,
                nominal_square_cache_tiles=tiles_per_side ** 2,
                nominal_square_cache_bytes=tiles_per_side ** 2 * page_bytes,
                match_scope="observed_native_config_mirrors_FindPoolConfig",
                actual_gpu_pool_allocation_verified=False)


def ensure_rvt(folder=FOLDER):
    """Create/configure/save the exact RVT asset and return its native UObject."""
    _enabled()
    _pool_state()
    package_path = _asset_path(folder)
    # Loading a missing asset logs an engine Error even if creation succeeds
    # afterward, which makes the commandlet process exit nonzero.
    rvt = (unreal.EditorAssetLibrary.load_asset(package_path)
           if unreal.EditorAssetLibrary.does_asset_exist(package_path) else None)
    if rvt is None:
        directory, name = package_path.rsplit("/", 1)
        unreal.EditorAssetLibrary.make_directory(directory)
        rvt = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            name, directory, unreal.RuntimeVirtualTexture, unreal.RuntimeVirtualTextureFactory())
    _require(isinstance(rvt, unreal.RuntimeVirtualTexture), "could not create/load the RVT asset")
    # Adaptive first: changing it can clamp TileCount in PostEditChangeProperty.
    for name, value in SETTINGS.items():
        if rvt.get_editor_property(name) != value:
            rvt.set_editor_property(name, value)
    if rvt.get_editor_property("material_type") != _material_type():
        rvt.set_editor_property("material_type", _material_type())
    _asset_state(rvt, folder)
    unreal.EditorAssetLibrary.set_metadata_tag(rvt, "RMSimGroundDecalRVTSchema", SCHEMA)
    unreal.EditorAssetLibrary.set_metadata_tag(rvt, "RMSimGroundDecalWriter", WRITER_ID)
    _require(unreal.EditorAssetLibrary.save_loaded_asset(rvt, only_if_is_dirty=False), "RVT asset save failed")
    return rvt


def _specification(source):
    _require(source.get("schema") == "rm_scene_map_source_v1" and source.get("id") == "wt_vlaanderen",
             "unexpected source manifest")
    _require(source.get("units") == "m" and source.get("coordinate_system") ==
             "ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP", "unexpected source units or coordinates")
    matches = [spec for spec in source["meshes"] if spec["id"] == WRITER_ID]
    _require(len(matches) == 1, "source must contain exactly one ground-decal mesh")
    spec = matches[0]
    _require(spec.get("source") == "baked_source_world" and spec.get("instance_count") == 1,
             "writer must be one baked world mesh instance")
    lo = [float(x) for x in spec["bounds_m"]["lo"]]
    hi = [float(x) for x in spec["bounds_m"]["hi"]]
    _require(len(lo) == len(hi) == 3 and all(math.isfinite(x) for x in lo+hi)
             and all(a < b for a, b in zip(lo, hi)), "invalid source bounds")
    # One metre around the complete writer bounds prevents clipping at the volume
    # faces.  This modifies only projection coverage, never source vertex height.
    padding_cm = 100.0
    ue_lo = [100*lo[0]-padding_cm, -100*hi[1]-padding_cm, 100*lo[2]-padding_cm]
    ue_hi = [100*hi[0]+padding_cm, -100*lo[1]+padding_cm, 100*hi[2]+padding_cm]
    size = [b-a for a, b in zip(ue_lo, ue_hi)]
    # Match GetProducerDescription's rounded log2 aspect ratio.  Asset size is
    # nominal here; per-device LOD-group biases remain a native runtime concern.
    largest = 1048576
    shift = int(math.floor(abs(math.log2(size[0]/size[1])) + 0.5))
    virtual_size = ([largest, max(largest >> shift, 256)] if size[0] >= size[1]
                    else [max(largest >> shift, 256), largest])
    return spec, dict(source_bounds_ros_m=dict(lo=lo, hi=hi), padding_cm=padding_cm,
                     min_ue_cm=ue_lo, max_ue_cm=ue_hi, scale_ue_cm=size,
                     nominal_virtual_size_xy=virtual_size,
                     nominal_metres_per_texel_xy=[size[i]/100/virtual_size[i] for i in range(2)])


def _actors_and_writer(world, rvt):
    actors = list(unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Actor))
    mesh_path = FOLDER + "/Meshes/" + WRITER_ID + "." + WRITER_ID
    matches = []
    for actor in actors:
        for component in actor.get_components_by_class(unreal.HierarchicalInstancedStaticMeshComponent):
            mesh = component.get_editor_property("static_mesh")
            is_writer = mesh is not None and mesh.get_path_name() == mesh_path
            if is_writer:
                matches.append((actor, component))
            else:
                _require(rvt not in component.get_editor_property("runtime_virtual_textures"),
                         "another HISM unexpectedly writes this RVT: " + component.get_path_name())
    _require(len(matches) == 1, "expected one native HISM for " + WRITER_ID)
    actor, writer = matches[0]
    _require(writer.get_instance_count() == 1, "native writer must have exactly one instance")
    # Bool+out is reflected as Transform or None by UE Python; no tuple unwrap.
    transform = writer.get_instance_transform(0, world_space=True)
    _require(isinstance(transform, unreal.Transform), "cannot read the writer's native instance transform")
    location = _xyz(transform.translation)
    scale = _xyz(transform.scale3d)
    q = transform.rotation
    rotation = [float(q.x), float(q.y), float(q.z), float(q.w)]
    _require(_close(location, [0, 0, 0]) and _close(scale, [1, 1, 1])
             and _close(rotation[:3], [0, 0, 0]) and abs(abs(rotation[3])-1) <= 1e-5,
             "source writer is not placed with the expected world identity transform")
    _require(writer.get_editor_property("visible") is True
             and writer.get_editor_property("hidden_in_game") is False,
             "RVT writer must remain visible to the virtual-texture pass")
    return actors, actor, writer, dict(translation_cm=location, scale=scale, quaternion=rotation)


def _volume_matches(actors, rvt):
    matches = []
    for actor in actors:
        if not isinstance(actor, unreal.RuntimeVirtualTextureVolume):
            continue
        component = actor.get_editor_property("virtual_texture_component")
        assigned = component.get_editor_property("virtual_texture")
        if actor.get_actor_label() == VOLUME_LABEL or VOLUME_TAG in [str(x) for x in actor.tags] or assigned == rvt:
            matches.append(actor)
    _require(len(matches) <= 1, "multiple ground-decal RVT volumes exist")
    return matches


def inspect_rvt_world(world, source, rvt=None):
    """Read back exact native configuration; does not mutate or save anything."""
    enabled = _enabled()
    if rvt is None:
        rvt = unreal.EditorAssetLibrary.load_asset(_asset_path(FOLDER))
    asset = _asset_state(rvt)
    spec, bounds = _specification(source)
    actors, actor, writer, transform = _actors_and_writer(world, rvt)
    _require(list(writer.get_editor_property("runtime_virtual_textures")) == [rvt],
             "writer must reference exactly this RVT")
    main_pass = writer.get_editor_property("virtual_texture_render_pass_type")
    _require(main_pass == unreal.RuntimeVirtualTextureMainPassType.NEVER,
             "ground-decal mesh must never enter the main pass")
    volumes = _volume_matches(actors, rvt)
    _require(len(volumes) == 1, "ground-decal RVT volume is missing")
    volume = volumes[0]
    component = volume.get_editor_property("virtual_texture_component")
    _require(component.get_editor_property("virtual_texture") == rvt, "volume RVT assignment differs")
    _require(component.get_editor_property("enable_scalability") is False
             and component.get_editor_property("hide_primitives") is False,
             "volume must retain explicit writer main-pass control and fixed asset resolution")
    _require(_close(_xyz(volume.get_actor_location()), bounds["min_ue_cm"], 0.01), "volume origin differs")
    _require(_close(_xyz(volume.get_actor_scale3d()), bounds["scale_ue_cm"], 0.5), "volume full extent differs")
    rotation = volume.get_actor_rotation()
    _require(_close([float(rotation.pitch), float(rotation.yaw), float(rotation.roll)], [0, 0, 0]),
             "volume must have identity rotation")
    return dict(schema=SCHEMA, status="configured", r_virtual_textures=enabled, asset=asset,
                physical_pool=_pool_state(),
                writer=dict(mesh_id=WRITER_ID, source_fbx_sha256=spec["sha256"],
                            actor=actor.get_path_name(), component=writer.get_path_name(),
                            instance_count=1, world_transform=transform, main_pass=str(main_pass),
                            visible=True, hidden_in_game=False),
                volume=dict(actor=volume.get_path_name(), label=volume.get_actor_label(),
                            component=component.get_path_name(), **bounds),
                rendered_projection_verified=False, runtime_page_production_verified=False)


def bind_rvt_world(world, source, rvt=None):
    """Bind the imported source HISM and create its deterministic RVT volume.

    Call after ImportInstances and before saving World.  Returned data contains
    native readbacks and nominal resolution; the caller includes it in evidence.
    """
    _enabled()
    if rvt is None:
        rvt = ensure_rvt()
    _asset_state(rvt)
    _, bounds = _specification(source)
    actors, _, writer, _ = _actors_and_writer(world, rvt)
    volumes = _volume_matches(actors, rvt)
    if volumes:
        volume = volumes[0]
    else:
        _require(unreal.EditorLevelLibrary.get_editor_world() == world,
                 "target must be the current editor World before spawning its RVT volume")
        volume = unreal.EditorLevelLibrary.spawn_actor_from_class(
            unreal.RuntimeVirtualTextureVolume, unreal.Vector(*bounds["min_ue_cm"]), unreal.Rotator())
        _require(volume is not None, "RVT volume spawn failed")
    volume.set_actor_label(VOLUME_LABEL)
    tags = list(volume.get_editor_property("tags"))
    if VOLUME_TAG not in [str(x) for x in tags]:
        volume.set_editor_property("tags", tags + [unreal.Name(VOLUME_TAG)])
    volume.set_actor_location(unreal.Vector(*bounds["min_ue_cm"]), False, True)
    volume.set_actor_rotation(unreal.Rotator(), False)
    volume.set_actor_scale3d(unreal.Vector(*bounds["scale_ue_cm"]))
    component = volume.get_editor_property("virtual_texture_component")
    component.set_editor_property("virtual_texture", rvt)
    component.set_editor_property("enable_scalability", False)
    component.set_editor_property("hide_primitives", False)
    writer.set_editor_property("runtime_virtual_textures", [rvt])
    writer.set_editor_property("virtual_texture_render_pass_type", unreal.RuntimeVirtualTextureMainPassType.NEVER)
    return inspect_rvt_world(world, source, rvt)
