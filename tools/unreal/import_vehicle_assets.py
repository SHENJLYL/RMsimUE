"""Run inside UE Editor Python. Imports prepared FBX/PBR assets, never the source blend.

Example: UnrealEditor-Cmd RMSim.uproject -run=pythonscript -script=<this file>
         -AssetManifest=<asset_import.json> -unattended
Windows native import is covered by project evidence; native Ubuntu remains unverified.
"""

import hashlib
import json
import math
from pathlib import Path
import re
import sys

import unreal
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'assets'))
from wheel_contact_geometry import geometry_hash, source_vertices


def local_path(root, relative):
    value = Path(relative)
    if value.is_absolute() or ".." in value.parts:
        raise ValueError(f"asset path must remain within package: {relative}")
    path = (root / value).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"missing/escaping asset: {relative}")
    return path


def asset_name(value):
    result = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not result:
        raise ValueError("empty asset name")
    return result


def import_asset(filename, destination, name, options=None):
    task = unreal.AssetImportTask()
    for key, value in {
        "filename": str(filename), "destination_path": destination,
        "destination_name": name, "automated": True, "save": True,
        "replace_existing": True, "replace_existing_settings": True,
    }.items():
        task.set_editor_property(key, value)
    if options:
        task.set_editor_property("options", options)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    paths = task.get_editor_property("imported_object_paths")
    if not paths:
        raise RuntimeError(f"UE imported no object from {filename}")
    return [unreal.load_asset(path) for path in paths]


def create_material(root, folder, spec):
    name = asset_name(spec["name"])
    path = f"{folder}/Materials/{name}"
    material = unreal.load_asset(path)
    if material is None:
        material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            name, f"{folder}/Materials", unreal.Material, unreal.MaterialFactoryNew())
    if not isinstance(material, unreal.Material):
        raise RuntimeError(f"material unavailable: {path}")
    editing = unreal.MaterialEditingLibrary
    editing.delete_all_material_expressions(material)
    bindings = {
        "base_color": (unreal.MaterialProperty.MP_BASE_COLOR, "RGB", True),
        "normal_dx": (unreal.MaterialProperty.MP_NORMAL, "RGB", False),
        "roughness": (unreal.MaterialProperty.MP_ROUGHNESS, "R", False),
        "metallic": (unreal.MaterialProperty.MP_METALLIC, "R", False),
    }
    textures = spec["textures"]
    for row, (key, (prop, channel, srgb)) in enumerate(bindings.items()):
        if key not in textures:
            raise ValueError(f"material {name} missing prepared {key} texture")
        imported = import_asset(local_path(root, textures[key]), f"{folder}/Textures", f"{name}_{key}")
        texture = next((obj for obj in imported if isinstance(obj, unreal.Texture2D)), None)
        if texture is None:
            raise RuntimeError(f"no texture imported for {name}/{key}")
        texture.set_editor_property("srgb", srgb)
        if key == "normal_dx":
            texture.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_NORMALMAP)
        else:
            texture.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_DEFAULT)
        unreal.EditorAssetLibrary.save_loaded_asset(texture)
        sample = editing.create_material_expression(material, unreal.MaterialExpressionTextureSample, -400, row * 220)
        sample.set_editor_property("texture", texture)
        if key == "normal_dx":
            sample.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
        elif not srgb:
            sample.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR)
        editing.connect_material_property(sample, channel, prop)
    # Exported aim_sentry factors are one. Reject other factors rather than silently changing source appearance.
    if spec.get("base_color_factor", [1, 1, 1, 1]) != [1, 1, 1, 1] or \
       spec.get("metallic_factor", 1) != 1 or spec.get("roughness_factor", 1) != 1:
        raise ValueError(f"material {name}: non-unit texture factors are not supported by this importer")
    opacity = float(spec.get("opacity", 1))
    if not 0 <= opacity <= 1:
        raise ValueError(f"material {name}: opacity outside [0,1]")
    material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT if opacity < 1 else unreal.BlendMode.BLEND_OPAQUE)
    if opacity < 1:
        material.set_editor_property("translucency_lighting_mode", unreal.TranslucencyLightingMode.TLM_SURFACE_PER_PIXEL_LIGHTING)
        opacity_node = editing.create_material_expression(material, unreal.MaterialExpressionConstant, -400, 1100)
        opacity_node.set_editor_property("r", opacity)
        editing.connect_material_property(opacity_node, "", unreal.MaterialProperty.MP_OPACITY)
    strength = float(spec.get("emission_strength", 0))
    if strength:
        color = spec.get("emission_color", [0, 0, 0, 1])
        node = editing.create_material_expression(material, unreal.MaterialExpressionConstant3Vector, -400, 900)
        node.set_editor_property("constant", unreal.LinearColor(color[0] * strength, color[1] * strength, color[2] * strength, 1))
        editing.connect_material_property(node, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    editing.recompile_material(material)
    unreal.EditorAssetLibrary.save_loaded_asset(material)
    return material


def main():
    command = unreal.SystemLibrary.get_command_line()
    match = re.search(r'-AssetManifest=(?:"([^"]+)"|(\S+))', command)
    if not match:
        raise ValueError("pass -AssetManifest=<asset_import.json>")
    manifest_path = Path(match.group(1) or match.group(2)).resolve()
    root = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if manifest.get("units") != "metres" or manifest.get("coordinate_system") != "ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP":
        raise ValueError("asset manifest must declare ROS right-handed coordinates in metres")
    vehicle_id = manifest["id"]
    if not re.fullmatch(r"[A-Za-z0-9_]+", vehicle_id):
        raise ValueError("vehicle id must be an Unreal-safe identifier")
    folder = f"/Game/RMSim/Imported/{vehicle_id}"
    output = root / "ue_mesh_rig.json"
    previous_parts = {}
    if output.exists():
        previous_rig = json.loads(output.read_text(encoding="utf-8-sig"))
        if previous_rig.get("kind") == "rm_ue_mesh_rig" and previous_rig.get("id") == vehicle_id:
            previous_parts = {part["name"]: part for part in previous_rig.get("parts", [])}
    materials = {}
    for spec in manifest["materials"]:
        if asset_name(spec["name"]) in materials:
            raise ValueError("material names collide after Unreal sanitization")
        materials[asset_name(spec["name"])] = create_material(root, folder, spec)
    parts = []
    for part in manifest["parts"]:
        name = asset_name(part["name"])
        if name != part["name"]:
            raise ValueError("part names must already be Unreal-safe identifiers")
        options = unreal.FbxImportUI()
        options.set_editor_property("import_mesh", True)
        options.set_editor_property("import_as_skeletal", False)
        options.set_editor_property("mesh_type_to_import", unreal.FBXImportType.FBXIT_STATIC_MESH)
        options.set_editor_property("import_materials", False)
        options.set_editor_property("import_textures", False)
        data = options.get_editor_property("static_mesh_import_data")
        for key, value in {
            "combine_meshes": True, "convert_scene": True, "convert_scene_unit": True,
            "force_front_x_axis": True, "transform_vertex_to_absolute": True,
            "bake_pivot_in_vertex": False, "auto_generate_collision": False,
            "reorder_material_to_fbx_order": True,
        }.items():
            data.set_editor_property(key, value)
        data.set_editor_property("normal_import_method", unreal.FBXNormalImportMethod.FBXNIM_IMPORT_NORMALS)
        source_fbx = local_path(root, part["fbx"])
        source_hash = hashlib.sha256(source_fbx.read_bytes()).hexdigest()
        imported = import_asset(source_fbx, folder, name, options)
        meshes = [obj for obj in imported if isinstance(obj, unreal.StaticMesh)]
        if len(meshes) != 1:
            raise RuntimeError(f"{name}: expected exactly one combined StaticMesh")
        mesh = meshes[0]
        slots = mesh.get_editor_property("static_materials")
        expected_slots = part["material_slots"]
        if len(slots) != len(expected_slots):
            raise RuntimeError(f"{name}: material slot count differs from exported manifest")
        for index, slot in enumerate(slots):
            source_name = asset_name(str(slot.get_editor_property("material_slot_name")))
            expected = asset_name(expected_slots[index])
            if source_name != expected:
                raise RuntimeError(f"{name}: slot {index} is {source_name}, expected {expected}; review FBX import mapping")
            mesh.set_material(index, materials[expected])
        unreal.EditorAssetLibrary.save_loaded_asset(mesh)
        imported_part = {"name": name, "parent": part["parent"], "origin": part["origin"],
                         "asset": mesh.get_path_name(), "source_fbx_sha256": source_hash}
        old = previous_parts.get(name, {})
        collision_path = old.get("collision_asset", "")
        if collision_path:
            content_root = Path(unreal.Paths.project_content_dir()).resolve()
            collision_file = (content_root / (collision_path.split('.')[0].removeprefix('/Game/') + '.uasset')).resolve()
            collision_unchanged = collision_file.is_relative_to(content_root) and collision_file.is_file() and \
                old.get("collision_asset_sha256") == hashlib.sha256(collision_file.read_bytes()).hexdigest()
            # UE saves can change a visual .uasset's bytes during reimport. Retain geometry-derived
            # collision only using the original FBX identity, never the readable part name alone.
            if old.get("collision_source_fbx_sha256") == source_hash and \
               old.get("collision_geometry_sha256", "") == geometry_hash(part.get("collision_vertices_m")) and \
               collision_path.startswith(f"/Game/RMSim/Collision/{vehicle_id}/") and \
               collision_unchanged and isinstance(unreal.load_asset(collision_path), unreal.StaticMesh):
                for key in ("collision_asset", "collision_source_fbx_sha256", "collision_asset_sha256", "collision_method", "collision_geometry_sha256"):
                    if key in old:
                        imported_part[key] = old[key]
            else:
                unreal.log_warning(f"COLLISION_METADATA_DROPPED {name}: source changed or derived asset unavailable; rebuild collision")
        parts.append(imported_part)
    by_name = {part["name"]: part for part in manifest["parts"]}
    suspension_by_wheel={item['wheel_part']:item for item in manifest.get('suspension',[])}
    if not {"body", "yaw", "pitch"}.issubset(by_name):
        raise ValueError("manual aim_sentry rig requires body/yaw/pitch parts")
    camera_origin = json.loads(json.dumps(manifest["muzzle"]["origin"]))
    camera_origin["xyz"][0] += 0.04 * manifest.get("export_scale_vs_blend", 1.0)  # Scale the preview offset with the source rig.
    rig = {
        "schema_version": 1, "kind": "rm_ue_mesh_rig", "id": vehicle_id, "parts": parts,
        "gimbal": {
            "yaw": {"part": "yaw", "axis": by_name["yaw"]["rotation_axis_ros"]},
            # The rotation about ROS +Y is down-positive.
            "pitch": {"part": "pitch", "axis": by_name["pitch"]["rotation_axis_ros"],
                      "lower": math.radians(-60.0), "upper": math.radians(45.0)},
        },
        "wheels": [{"part": name, "axis": part["rotation_axis_ros"], "radius_m": manifest["wheel_radius_m"],
                    **({"collision_vertices_m":source_vertices(part['collision_vertices_m'])} if 'collision_vertices_m' in part else {}),
                    **({"suspension_part":suspension_by_wheel[name]['part']} if name in suspension_by_wheel else {}),
                    **({"steering_part":suspension_by_wheel[name]['steering_part']} if name in suspension_by_wheel and 'steering_part' in suspension_by_wheel[name] else {})}
                   for name, part in by_name.items() if name.startswith("wheel_")],
        "suspension": manifest.get("suspension", []),
        "steering": manifest.get("steering", []),
        "camera": {"id": "aim_camera", "type": "camera", "parent_link": "pitch",
                   "origin": camera_origin, "rate_hz": 1,
                   "parameters": {"width": 1280, "height": 720, "horizontal_fov_rad": math.radians(75.0)}},
        "limitations": ["asset_geometry_not_a_calibrated_dynamics_model", "source_suspension_travel_is_reference_only",
                        "camera_pose_not_calibrated", "not_ros_ready"],
    }
    if manifest.get('drive_profile'):
        profile_path=local_path(root,manifest['drive_profile'])
        profile=json.loads(profile_path.read_text(encoding='utf-8-sig'))
        if profile.get('schema')!='rm_drive_profile_v1' or profile.get('units')!='SI':
            raise ValueError('invalid drive profile schema/units')
        rig['drive_profile']=profile
        rig['drive_profile_file']=manifest['drive_profile']
        rig['drive_profile_sha256']=hashlib.sha256(profile_path.read_bytes()).hexdigest()
    output.write_text(json.dumps(rig, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    unreal.log(f"MESH_RIG_IMPORT_COMPLETE {output}; native rendering QA still required")


if __name__ == "__main__":
    main()
