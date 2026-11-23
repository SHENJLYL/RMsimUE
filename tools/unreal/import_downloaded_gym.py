"""Import a pinned complete sports gym, preserving source geometry and PBR slots.

Run using UnrealEditor-Cmd -ExecutePythonScript in the full Editor environment.
StaticMeshEditorSubsystem needs the Editor's initialized asset subsystems.
Generates only assets
below Arena/DownloadedGym; the source GLB, competition CAD and vehicle are read-only.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import math
import re
import shutil
import traceback
import unreal

BASE = "/Game/RMSim/Maps/rmuc2027/Arena/DownloadedGym"
CAMPUS = "/Game/RMSim/Maps/rmuc2027/Arena/Campus"
MEL = unreal.MaterialEditingLibrary
EAL = unreal.EditorAssetLibrary
TOOLS = unreal.AssetToolsHelpers.get_asset_tools()
SMS = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("gym_pbr_helpers", ROOT / "tools/unreal/import_campus_gym_assets.py")
PBR = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PBR)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def argument(name):
    match = re.search(r'(?:^|\s)(?:"-' + re.escape(name) + r'=([^"]+)"|-' + re.escape(name) + r'=(?:"([^"]+)"|([^\s"]+)))', unreal.SystemLibrary.get_command_line())
    if not match:
        raise ValueError("Required argument -" + name)
    value = Path(next(v for v in match.groups() if v is not None))
    if not value.is_absolute():
        raise ValueError("Expected absolute path for " + name)
    return value.resolve()


def checked(root, relative, manifest):
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("Invalid source-relative path")
    path = (root / rel).resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError("Source escaped asset root")
    records = manifest.get("files", {})
    if isinstance(records, list):
        records = {x.get("file", x.get("path")): x for x in records}
    record = records.get(rel.as_posix())
    if record and (sha(path) != record["sha256"] or path.stat().st_size != record["bytes"]):
        raise ValueError("Prepared asset identity changed: " + str(rel))
    return path


def import_asset(path, directory, name, options=None, factory=None):
    task = unreal.AssetImportTask()
    for key, value in {"filename": str(path), "destination_path": directory,
                       "destination_name": name, "automated": True, "save": True,
                       "replace_existing": True, "replace_existing_settings": True}.items():
        task.set_editor_property(key, value)
    if options is not None:
        task.set_editor_property("options", options)
    if factory is not None:
        task.set_editor_property("factory", factory)
    TOOLS.import_asset_tasks([task])
    objects = list(task.get_objects())
    if not objects:
        objects = [unreal.load_asset(p) for p in task.get_editor_property("imported_object_paths")]
    if not objects:
        raise RuntimeError("Import produced no object: " + str(path))
    return objects


def texture(root, relative, role, manifest, cache, report):
    key = (relative, role)
    if key in cache:
        return cache[key]
    path = checked(root, relative, manifest)
    name = "T_" + re.sub(r"[^A-Za-z0-9_]", "_", Path(relative).stem) + "_" + role
    value = next((x for x in import_asset(path, BASE + "/Textures", name)
                  if isinstance(x, unreal.Texture2D)), None)
    if value is None:
        raise RuntimeError("Expected Texture2D: " + relative)
    value.set_editor_property("srgb", role in ("Color", "Emissive"))
    value.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_NORMALMAP
                              if role == "Normal" else unreal.TextureCompressionSettings.TC_DEFAULT)
    value.set_editor_property("max_texture_size", 4096)
    if relative.startswith("branding/"):
        value.set_editor_property("address_x", unreal.TextureAddress.TA_CLAMP)
        value.set_editor_property("address_y", unreal.TextureAddress.TA_CLAMP)
    if not EAL.save_loaded_asset(value):
        raise RuntimeError("Cannot save texture: " + relative)
    cache[key] = value
    report["textures"].append({"source": relative, "source_sha256": sha(path),
                                "asset": value.get_path_name(), "role": role})
    return value


def sample(mat, tex, role, y=0):
    return PBR.expr(mat, unreal.MaterialExpressionTextureSample, y=y, texture=tex,
                    sampler_type=unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL if role == "Normal"
                    else unreal.MaterialSamplerType.SAMPLERTYPE_COLOR if role in ("Color", "Emissive")
                    else unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR)


def detail_material(mat, semantic, factor):
    presets = {
        "wood_floor": ("WoodFloor051", 1.8, [1., 1., 1.], .70, .15, 0.),
        "wood_panel": ("Wood049", .8, [1., 1., 1.], .80, .12, 0.),
        "painted_wall": ("Concrete046", 2.4, [min(1.3, max(.65, x * 1.3)) for x in factor[:3]], .65, .24, 0.),
        "concrete": ("Concrete046", 2.4, [.8, .82, .85], .90, .08, 0.),
        "structural_steel": ("Metal050A", 1.0, [max(.25, x) for x in factor[:3]], .65, .18, .75),
        "roof_metal": ("Metal050A", 1.8, [.85, .87, .9], .80, .12, .65),
    }
    if semantic not in presets:
        return None
    identifier, tile, tint, scale, bias, metal = presets[semantic]
    maps = {}
    for channel in ("Color", "NormalDX", "Roughness"):
        path = CAMPUS + "/Textures/T_" + identifier + "_" + channel
        value = unreal.load_asset(path)
        if not isinstance(value, unreal.Texture2D):
            raise RuntimeError("Verified CC0 detail material missing: " + path)
        maps[channel] = value
    PBR.pbr(mat, maps, tile, tint, scale, bias, metal)
    return {"provider": "ambientCG", "id": identifier, "tile_m": tile,
            "source_manifest": "arena/campus-gym/SOURCE.json", "source_base_color_factor": factor}


def make_material(name, data, root, manifest, cache, report):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
        raise ValueError("Material slot must be ASCII: " + name)
    semantic = data.get("semantic", "source")
    if semantic in ("glass", "window_glass") or data.get("alpha_mode") == "BLEND":
        value = unreal.load_asset(CAMPUS + "/Materials/M_glass")
        if not isinstance(value, unreal.MaterialInterface):
            raise RuntimeError("Verified thin-translucent glass missing")
        report["materials"].append({"slot": name, "semantic": semantic,
                                    "asset": value.get_path_name(), "policy": "native thin-translucent glass"})
        return value
    path = BASE + "/Materials/M_" + name
    mat = unreal.load_asset(path)
    if mat is None:
        mat = TOOLS.create_asset("M_" + name, BASE + "/Materials", unreal.Material, unreal.MaterialFactoryNew())
    if not isinstance(mat, unreal.Material):
        raise RuntimeError("Cannot create material: " + name)
    MEL.delete_all_material_expressions(mat)
    mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_OPAQUE)
    mat.set_editor_property("two_sided", bool(data.get("double_sided", False)))
    mat.set_editor_property("tangent_space_normal", True)
    mat.set_editor_property("used_with_instanced_static_meshes", True)
    mat.set_editor_property("used_with_nanite", True)
    factor = data.get("base_color_factor", [1., 1., 1., 1.])
    detail = detail_material(mat, semantic, factor)
    if detail is None:
        if data.get("base_color_texture"):
            tex = texture(root, data["base_color_texture"], "Color", manifest, cache, report)
            color = sample(mat, tex, "Color")
            if any(abs(factor[i] - 1.) > 1.e-8 for i in range(3)):
                color = PBR.color_tint(mat, color, factor[:3])
        else:
            color = PBR.vector(mat, factor[:3])
        PBR.property_input(color, unreal.MaterialProperty.MP_BASE_COLOR)
        rough_floor = .04 if semantic in ("polished_steel", "brushed_steel", "light_reflector") else .12
        rough = max(rough_floor, min(.96, float(data.get("roughness_factor", .55))))
        metal = max(0., min(1., float(data.get("metallic_factor", 0.))))
        PBR.property_input(PBR.scalar(mat, rough, 250), unreal.MaterialProperty.MP_ROUGHNESS)
        PBR.property_input(PBR.scalar(mat, metal, 350), unreal.MaterialProperty.MP_METALLIC)
        if data.get("normal_texture"):
            tex = texture(root, data["normal_texture"], "Normal", manifest, cache, report)
            PBR.property_input(sample(mat, tex, "Normal", 500), unreal.MaterialProperty.MP_NORMAL)
        emissive = data.get("emissive_factor", [0., 0., 0.])
        if semantic in ("light", "emissive_light", "light_fixture_emissive", "light_emitter"):
            emissive = [14., 13.5, 12.5]
        if max(emissive) > 0:
            if data.get("emissive_texture"):
                tex = texture(root, data["emissive_texture"], "Emissive", manifest, cache, report)
                node = PBR.color_tint(mat, sample(mat, tex, "Emissive", 600), emissive, 600)
            else:
                node = PBR.vector(mat, emissive, 600)
            PBR.property_input(node, unreal.MaterialProperty.MP_EMISSIVE_COLOR)
        if data.get("alpha_mode") == "MASK":
            mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_MASKED)
            mat.set_editor_property("opacity_mask_clip_value", float(data.get("alpha_cutoff", .5)))
            if not data.get("base_color_texture"):
                raise ValueError("Masked material has no texture")
            PBR.property_input(sample(mat, texture(root, data["base_color_texture"], "Color", manifest, cache, report), "Color", 750),
                               unreal.MaterialProperty.MP_OPACITY_MASK, "A")
    MEL.recompile_material(mat)
    if not EAL.save_loaded_asset(mat):
        raise RuntimeError("Cannot save material")
    report["materials"].append({"slot": name, "semantic": semantic,
                                "asset": mat.get_path_name(), "detail_upgrade": detail,
                                "used_with_instanced_static_meshes": bool(mat.get_editor_property("used_with_instanced_static_meshes")),
                                "used_with_nanite": bool(mat.get_editor_property("used_with_nanite")),
                                "source_texture": data.get("base_color_texture")})
    return mat


def import_mesh(entry, root, manifest, materials):
    source = checked(root, entry["file"], manifest)
    # Legacy UE OBJ import performs its own right-to-left handed Y reflection
    # even with convert_scene=False. Preserve canonical source files and undo
    # that reflection in a transport copy; final native bounds stay canonical.
    transport_dir = root / "fbx_transport"
    transport_dir.mkdir(exist_ok=True)
    transport = transport_dir / source.name
    positions = [None]
    source_triangles = 0
    zero_area_triangles = 0
    with source.open("r", encoding="utf-8") as reader, transport.open("w", encoding="utf-8", newline="\n") as writer:
        for line in reader:
            if line.startswith(("v ", "vn ")):
                parts = line.split()
                parts[2] = format(-float(parts[2]), ".12g")
                if parts[0] == "v":
                    positions.append(tuple(float(value) for value in parts[1:4]))
                writer.write(" ".join(parts) + "\n")
            elif line.startswith("f "):
                corners = line.split()[1:]
                if len(corners) != 3:
                    raise ValueError("Prepared OBJ must already be triangulated")
                vertices = [positions[int(corner.split("/")[0])] for corner in corners]
                a = [vertices[1][i] - vertices[0][i] for i in range(3)]
                b = [vertices[2][i] - vertices[0][i] for i in range(3)]
                cross = (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
                source_triangles += 1
                if cross == (0.0, 0.0, 0.0):
                    zero_area_triangles += 1
                    continue
                writer.write("f " + " ".join(reversed(corners)) + "\n")
            else:
                writer.write(line)
    if source_triangles != int(entry["triangles"]):
        raise ValueError("Prepared OBJ face count differs from its manifest")
    expected_triangles = source_triangles - zero_area_triangles
    if source.with_suffix(".mtl").is_file():
        shutil.copy2(source.with_suffix(".mtl"), transport.with_suffix(".mtl"))
    name = entry["asset_name"]
    if not re.fullmatch(r"(?:SM_|RMVenue)[A-Za-z0-9_]+", name):
        raise ValueError("Unsafe mesh asset name")
    path = BASE + "/Meshes/" + name
    options = unreal.FbxImportUI()
    for key, value in {"import_mesh": True, "import_as_skeletal": False,
                       "mesh_type_to_import": unreal.FBXImportType.FBXIT_STATIC_MESH,
                       "automated_import_should_detect_type": False, "import_materials": False,
                       "import_textures": False}.items():
        options.set_editor_property(key, value)
    opts = options.get_editor_property("static_mesh_import_data")
    for key, value in {"combine_meshes": True, "convert_scene": False, "convert_scene_unit": False,
                       "force_front_x_axis": False, "transform_vertex_to_absolute": True,
                       "bake_pivot_in_vertex": False, "auto_generate_collision": False,
                       "build_nanite": False,
                       "remove_degenerates": False,
                       "generate_lightmap_u_vs": False, "reorder_material_to_fbx_order": True,
                       "import_uniform_scale": 100.}.items():
        opts.set_editor_property(key, value)
    opts.set_editor_property("normal_import_method", unreal.FBXNormalImportMethod.FBXNIM_IMPORT_NORMALS)
    opts.set_editor_property("normal_generation_method", unreal.FBXNormalGenerationMethod.BUILT_IN)
    if EAL.does_asset_exist(path):
        if not EAL.delete_asset(path):
            raise RuntimeError("Cannot recreate generated mesh: " + path)
        unreal.SystemLibrary.collect_garbage()
    meshes = [x for x in import_asset(transport, BASE + "/Meshes", name, options, unreal.FbxFactory())
              if isinstance(x, unreal.StaticMesh)]
    if len(meshes) != 1:
        raise RuntimeError("Expected one native mesh per spatial block")
    mesh = meshes[0]
    bounds = mesh.get_bounds()
    center = [bounds.origin.x, bounds.origin.y, bounds.origin.z]
    extent = [bounds.box_extent.x, bounds.box_extent.y, bounds.box_extent.z]
    minimum = [center[i] - extent[i] for i in range(3)]
    maximum = [center[i] + extent[i] for i in range(3)]
    expected_min = entry["bounds_min_m"]
    expected_max = entry["bounds_max_m"]
    if any(abs(minimum[i] - expected_min[i] * 100) > .1 or
           abs(maximum[i] - expected_max[i] * 100) > .1 for i in range(3)):
        raise ValueError("Native geometry bounds/orientation mismatch: " + name + repr([minimum, maximum]))
    triangles = int(mesh.get_num_triangles(0))
    if triangles != expected_triangles:
        raise ValueError("Native triangle count differs from prepared block: " + name +
                         " actual=" + str(triangles) + " expected=" + str(expected_triangles))
    slots = list(mesh.get_editor_property("static_materials"))
    if {str(slot.get_editor_property("material_slot_name")) for slot in slots} != set(entry["material_slots"]):
        raise ValueError("Native material slot set differs from prepared block: " + name)
    assignments = []
    opaque = True
    for index, slot in enumerate(slots):
        slot_name = str(slot.get_editor_property("material_slot_name"))
        if slot_name not in materials:
            raise ValueError("Unmapped native material slot: " + slot_name)
        material = materials[slot_name]
        mesh.set_material(index, material)
        source_data = manifest["materials"][slot_name]
        opaque &= source_data.get("alpha_mode") != "BLEND" and source_data.get("semantic") not in ("glass", "window_glass")
        assignments.append({"slot": slot_name, "asset": material.get_path_name()})
    mesh.set_editor_property("has_navigation_data", False)
    mesh.set_editor_property("generate_mesh_distance_field", opaque)
    build = SMS.get_lod_build_settings(mesh, 0)
    build.set_editor_property("distance_field_resolution_scale", 1.0 if opaque else 0.0)
    build.set_editor_property("generate_distance_field_as_if_two_sided", True)
    build.set_editor_property("max_lumen_mesh_cards", 32)
    build.set_editor_property("use_full_precision_u_vs", True)
    build.set_editor_property("remove_degenerates", False)
    build.set_editor_property("use_mikk_t_space", False)
    SMS.set_lod_build_settings(mesh, 0, build)
    nanite = SMS.get_nanite_settings(mesh)
    nanite.set_editor_property("enabled", opaque)
    nanite.set_editor_property("fallback_relative_error", 0.0)
    nanite.set_editor_property("fallback_percent_triangles", 1.0)
    SMS.set_nanite_settings(mesh, nanite, True)
    if bool(SMS.get_nanite_settings(mesh).get_editor_property("enabled")) != opaque:
        raise RuntimeError("Nanite readback differs from import contract")
    if not EAL.save_loaded_asset(mesh):
        raise RuntimeError("Cannot save native mesh")
    nanite_triangles = int(mesh.get_num_nanite_triangles())
    if opaque and nanite_triangles <= 0:
        raise RuntimeError("Opaque mesh has no built Nanite triangles: " + name)
    return {"asset": mesh.get_path_name(), "source_sha256": sha(source),
            "transport_sha256": sha(transport), "transport_y_reflection_compensated": True,
            "source_triangles": source_triangles, "strict_zero_area_triangles_removed": zero_area_triangles,
            "imported_source_triangles": triangles, "material_slots": assignments,
            "bounds_min_cm": minimum, "bounds_max_cm": maximum,
            "nanite_enabled": opaque, "generate_distance_field": opaque,
            "built_nanite_triangles": nanite_triangles,
            "distance_field_resolution_scale": 1.0 if opaque else 0.0,
            "lumen_mesh_cards": 32, "import_uniform_scale": 100.}


def main():
    root = argument("VenueAssetRoot")
    output = argument("VenueImportReport")
    if output.exists():
        raise FileExistsError("Choose a fresh VenueImportReport")
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {"status": "running", "asset_root": str(root), "materials": [], "textures": [], "meshes": [],
              "scope": "Native model/material/Nanite import; rendered light quality and vehicle movement are checked separately"}
    try:
        if SMS is None:
            raise RuntimeError("Use -ExecutePythonScript with the full Editor; -run=pythonscript does not initialize StaticMeshEditorSubsystem")
        manifest = json.loads((root / "prepared.json").read_text(encoding="utf-8-sig"))
        report["prepared_sha256"] = sha(root / "prepared.json")
        if not 1 <= len(manifest["meshes"]) <= 32:
            raise ValueError("Expected 1..32 spatially separated venue blocks")
        cache, materials = {}, {}
        for name, data in manifest["materials"].items():
            materials[name] = make_material(name, data, root, manifest, cache, report)
        for entry in manifest["meshes"]:
            report["meshes"].append(import_mesh(entry, root, manifest, materials))
            output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        report["status"] = "passed"
    except Exception as error:
        report.update(status="failed", error=str(error), traceback=traceback.format_exc())
        unreal.log_error("RMSIM_DOWNLOADED_GYM_IMPORT_FAILED " + str(error))
        raise
    finally:
        report["script_sha256"] = sha(Path(__file__).resolve())
        directory = Path(unreal.Paths.project_content_dir()).resolve() / "RMSim/Maps/rmuc2027/Arena/DownloadedGym"
        report["native_files"] = {p.relative_to(directory).as_posix(): {"bytes": p.stat().st_size, "sha256": sha(p)}
                                  for p in sorted(directory.rglob("*.uasset"))}
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    unreal.log("RMSIM_DOWNLOADED_GYM_IMPORT_COMPLETE " + str(output))


if __name__ == "__main__":
    main()
