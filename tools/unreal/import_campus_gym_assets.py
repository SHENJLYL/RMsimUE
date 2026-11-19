"""Author the campus-gym UE assets from the verified CC0 source selection.

Run in UnrealEditor-Cmd with -GymAssetRoot=<absolute source directory> and
-GymImportReport=<fresh report.json>. The caller must inspect shader logs and
render the resulting scene; this report covers import, bounds and graph setup.
"""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import re
import traceback
import unreal

BASE = "/Game/RMSim/Maps/rmuc2027/Arena/Campus"
MEL = unreal.MaterialEditingLibrary
EAL = unreal.EditorAssetLibrary
TOOLS = unreal.AssetToolsHelpers.get_asset_tools()
EXPECTED_CM = [290.8013701438904, 40.0010883808136, 75.00060200691223]
MATERIAL_IDS = ["plaster", "concrete", "wood_floor", "acoustic_wood", "steel",
                "steel_light", "seat_blue", "seat_red", "seat_gray", "glass",
                "rubber", "emissive", "scoreboard", "court_line", "environment"]
FN_ROOT = "/Engine/Functions/Engine_MaterialFunctions01/Texturing/"
ENVIRONMENT_PIN = {
    "id": "quadrangle_cloudy",
    "source_url": "https://polyhaven.com/a/quadrangle_cloudy",
    "license": "CC0-1.0", "license_url": "https://polyhaven.com/license",
    "download_url": "https://dl.polyhaven.org/file/ph-assets/HDRIs/hdr/1k/quadrangle_cloudy_1k.hdr",
    "file": "environments/quadrangle_cloudy_1k.hdr", "bytes": 1671356,
    "sha256": "6b1f8153e612ebc6059fa28ec652f29b60df9ff6e7db4a401c653a09fd74c527",
    "width": 1024, "height": 512,
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def argument(name):
    # UE/Windows callers may quote either the value or the complete argument.
    # Keep backslashes literal rather than using POSIX shell tokenization.
    option = re.escape(name)
    pattern = (r'(?<!\S)(?:"-' + option + r'=([^"]+)"|-' + option +
               r'=(?:"([^"]+)"|([^\s"]+)))(?=\s|$)')
    match = re.search(pattern, unreal.SystemLibrary.get_command_line())
    if not match:
        raise ValueError("Required argument -" + name)
    path = Path(next(value for value in match.groups() if value is not None))
    if not path.is_absolute():
        raise ValueError("Argument -" + name + " must contain an absolute path")
    return path.resolve()


def checked_file(root, relative, manifest):
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("Asset path escapes package: " + relative)
    path = (root / rel).resolve()
    record = manifest["files"].get(rel.as_posix())
    if not path.is_relative_to(root) or not record or not path.is_file():
        raise ValueError("Missing/unrecorded source file: " + relative)
    if path.stat().st_size != record["bytes"] or sha(path) != record["sha256"]:
        raise ValueError("Source file hash changed: " + relative)
    return path


def import_asset(path, directory, name, options=None, factory=None):
    task = unreal.AssetImportTask()
    for key, value in {"filename": str(path), "destination_path": directory,
        "destination_name": name, "automated": True, "save": True,
        "replace_existing": True, "replace_existing_settings": True}.items():
        task.set_editor_property(key, value)
    if options is not None: task.set_editor_property("options", options)
    if factory is not None: task.set_editor_property("factory", factory)
    TOOLS.import_asset_tasks([task])
    result = list(task.get_objects())
    if not result:
        result = [unreal.load_asset(p) for p in task.get_editor_property("imported_object_paths")]
    if not result:
        raise RuntimeError("No imported object: " + str(path))
    return result


def expr(material, cls, x=-600, y=0, **properties):
    node = MEL.create_material_expression(material, cls, x, y)
    if node is None: raise RuntimeError("Cannot create expression " + str(cls))
    for key, value in properties.items(): node.set_editor_property(key, value)
    return node


def scalar(material, value, y=0):
    return expr(material, unreal.MaterialExpressionConstant, y=y, r=float(value))


def vector(material, values, y=0):
    return expr(material, unreal.MaterialExpressionConstant3Vector, y=y,
                constant=unreal.LinearColor(float(values[0]), float(values[1]), float(values[2]), 1.0))


def connect(source, target, target_pin="", source_pin=""):
    # Unary native expressions may expose an unnamed input even when the UI
    # labels it Input. Resolve declared names, then use UE's documented empty
    # string for the first input only for an implicit/unary Input request.
    names = [str(value) for value in MEL.get_material_expression_input_names(target)]
    normalized = lambda value: re.sub(r"[^a-z0-9]", "", value.lower())
    candidates = []
    if not target_pin:
        candidates.append("")
    else:
        candidates.extend(name for name in names if normalized(name) == normalized(target_pin))
        if not candidates:
            candidates.append(target_pin)
        if target_pin == "Input":
            candidates.append("")
    attempted = []
    for pin in dict.fromkeys(candidates):
        attempted.append(pin)
        if MEL.connect_material_expressions(source, source_pin, target, pin):
            return
    raise RuntimeError("Cannot connect " + source.get_name() + ":" + source_pin +
        " to " + target.get_name() + ":" + target_pin +
        "; native_input_names=" + repr(names) + "; attempted=" + repr(attempted))


def property_input(source, prop, source_pin=""):
    if not MEL.connect_material_property(source, source_pin, prop):
        raise RuntimeError("Cannot connect material property " + str(prop))


def multiply(material, source, factor, y=0, source_pin=""):
    result = expr(material, unreal.MaterialExpressionMultiply, x=-250, y=y, const_b=float(factor))
    connect(source, result, "A", source_pin)
    return result


def add(material, source, value, y=0):
    result = expr(material, unreal.MaterialExpressionAdd, x=-100, y=y, const_b=float(value))
    connect(source, result, "A")
    return result


def color_tint(material, source, tint, y=0, source_pin=""):
    result = expr(material, unreal.MaterialExpressionMultiply, x=-250, y=y)
    connect(source, result, "A", source_pin); connect(vector(material, tint, y+80), result, "B")
    return result


def function_input(call, key):
    names = [str(x) for x in MEL.get_material_expression_input_names(call)]
    normalized = lambda x: re.sub(r"[^a-z0-9]", "", x.lower())
    for name in names:
        if normalized(name) == normalized(key): return name
    raise RuntimeError("Missing function input " + key + " in " + repr(names))


def world_sample(material, texture, channel, tile_m, y):
    normal = channel == "NormalDX"
    name = "WorldAlignedNormal" if normal else "WorldAlignedTexture"
    function = unreal.load_asset(FN_ROOT + name)
    if not isinstance(function, unreal.MaterialFunctionInterface):
        raise RuntimeError("Missing built-in world-aligned material function " + name)
    call = expr(material, unreal.MaterialExpressionMaterialFunctionCall, x=-480, y=y)
    if not call.set_material_function(function):
        raise RuntimeError("Cannot configure function " + name)
    tex = expr(material, unreal.MaterialExpressionTextureObject, x=-1100, y=y, texture=texture)
    tex.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL if normal else
        (unreal.MaterialSamplerType.SAMPLERTYPE_COLOR if channel == "Color" else unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR))
    size = vector(material, [100.0 * tile_m] * 3, y+100)
    connect(tex, call, function_input(call, "TextureObject"))
    connect(size, call, function_input(call, "TextureSize"))
    # UE 5.8's engine function exposes an explicitly world-space normal output.
    output = "XYZ Texture"  # Function output pin; its normal output is world-space.
    return call, output


def import_textures(root, manifest, report):
    result = {}
    for package in manifest["material_packages"]:
        identifier = package["id"]
        result[identifier] = {}
        for channel, relative in package["selected_files"].items():
            path = checked_file(root, relative, manifest)
            objects = import_asset(path, BASE + "/Textures", "T_" + identifier + "_" + channel)
            texture = next((x for x in objects if isinstance(x, unreal.Texture2D)), None)
            if texture is None: raise RuntimeError("No Texture2D for " + relative)
            texture.set_editor_property("srgb", channel == "Color")
            texture.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_NORMALMAP if channel == "NormalDX" else unreal.TextureCompressionSettings.TC_DEFAULT)
            texture.set_editor_property("max_texture_size", 1024)
            if not EAL.save_loaded_asset(texture): raise RuntimeError("Texture save failed")
            result[identifier][channel] = texture
            report["textures"].append({"source": relative, "asset": texture.get_path_name(),
                "source_sha256": sha(path), "srgb": bool(texture.get_editor_property("srgb")),
                "compression": str(texture.get_editor_property("compression_settings")),
                "max_texture_size": int(texture.get_editor_property("max_texture_size"))})
    return result


def import_environment(root, manifest, report):
    environment = manifest.get("environment", {})
    for key, expected in ENVIRONMENT_PIN.items():
        if environment.get(key) != expected:
            raise ValueError("Unexpected environment manifest field: " + key)
    path = checked_file(root, environment["file"], manifest)
    if path.stat().st_size != ENVIRONMENT_PIN["bytes"] or sha(path) != ENVIRONMENT_PIN["sha256"]:
        raise ValueError("Environment source differs from the pinned Poly Haven HDRI")
    objects = import_asset(path, BASE + "/Textures", "T_CampusCourtyard", factory=unreal.TextureFactory())
    cubes = [obj for obj in objects if isinstance(obj, unreal.TextureCube)]
    if len(cubes) != 1:
        raise RuntimeError("Radiance import must produce one TextureCube; got " +
            repr([(obj.get_name(), obj.get_class().get_name()) for obj in objects]))
    texture = cubes[0]
    if texture.get_path_name().split(".")[0] != BASE + "/Textures/T_CampusCourtyard":
        raise RuntimeError("Environment importer did not honor its required asset path")
    texture.set_editor_property("srgb", False)
    texture.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_HDR)
    texture.set_editor_property("max_texture_size", 512)
    if not EAL.save_loaded_asset(texture): raise RuntimeError("Environment cube save failed")
    report["textures"].append({"source": environment["file"], "asset": texture.get_path_name(),
        "kind": "TextureCube", "source_sha256": sha(path),
        "srgb": bool(texture.get_editor_property("srgb")),
        "compression": str(texture.get_editor_property("compression_settings")),
        "max_texture_size": int(texture.get_editor_property("max_texture_size")),
        "source_equirectangular_size": [environment["width"], environment["height"]]})
    report["environment"] = dict(environment, native_asset=texture.get_path_name())
    return texture


def pbr(material, texture_set, tile_m, tint, rough_scale=1.0, rough_bias=0.0, metal=0.0):
    color, out = world_sample(material, texture_set["Color"], "Color", tile_m, 0)
    property_input(color_tint(material, color, tint, 0, out), unreal.MaterialProperty.MP_BASE_COLOR)
    rough, out = world_sample(material, texture_set["Roughness"], "Roughness", tile_m, 300)
    red = expr(material, unreal.MaterialExpressionComponentMask, x=-300, y=300, r=True, g=False, b=False, a=False)
    connect(rough, red, "Input", out)
    shaped = add(material, multiply(material, red, rough_scale, 300), rough_bias, 300)
    clamp = expr(material, unreal.MaterialExpressionClamp, x=100, y=300, min_default=.04, max_default=.98)
    connect(shaped, clamp, "Input"); property_input(clamp, unreal.MaterialProperty.MP_ROUGHNESS)
    normal, out = world_sample(material, texture_set["NormalDX"], "NormalDX", tile_m, 600)
    material.set_editor_property("tangent_space_normal", False)
    property_input(normal, unreal.MaterialProperty.MP_NORMAL, out)
    property_input(scalar(material, metal, 800), unreal.MaterialProperty.MP_METALLIC)


def make_material(identifier, textures):
    name = "M_" + identifier; asset = BASE + "/Materials/" + name
    # UE 5.8 can retain a stale ThinTranslucent custom-output cache after
    # delete_all_material_expressions on a saved material. This exact generated
    # asset is recreated so a repeat import starts with no cached output node.
    recreated_glass = identifier == "glass"
    if recreated_glass:
        if EAL.does_asset_exist(asset):
            if not EAL.delete_asset(asset):
                raise RuntimeError("Cannot recreate generated glass material: " + asset)
            unreal.SystemLibrary.collect_garbage()
        material = None
    else:
        material = unreal.load_asset(asset)
    if material is None:
        material = TOOLS.create_asset(name, BASE + "/Materials", unreal.Material, unreal.MaterialFactoryNew())
    if not isinstance(material, unreal.Material): raise RuntimeError("No material " + asset)
    MEL.delete_all_material_expressions(material)
    for key, value in {"blend_mode": unreal.BlendMode.BLEND_OPAQUE,
        "shading_model": unreal.MaterialShadingModel.MSM_DEFAULT_LIT,
        "two_sided": False, "tangent_space_normal": True, "is_sky": False,
        "used_with_instanced_static_meshes": True}.items(): material.set_editor_property(key, value)
    mapping = "constant_pbr"
    specifications = {
        "plaster": ("Concrete046", 2.4, [1.08,1.07,1.02], .20,.66,0.0),
        "concrete": ("Concrete046", 2.4, [.85,.86,.84], .35,.56,0.0),
        "wood_floor": ("WoodFloor051", 1.8, [1.0,.98,.92], .45,.18,0.0),
        "acoustic_wood": ("Wood049", .8, [.88,.78,.65], .22,.54,0.0),
        "steel": ("Metal050A", 1.0, [.32,.36,.40], .42,.26,1.0),
        "steel_light": ("Metal050A", 1.0, [.84,.87,.90], .45,.20,1.0),
    }
    if identifier in specifications:
        source,tile,tint,scale,bias,metal = specifications[identifier]
        pbr(material,textures[source],tile,tint,scale,bias,metal)
        mapping = "world_aligned_xyz_with_world_space_normal"
    elif identifier == "environment":
        material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
        material.set_editor_property("two_sided", True)
        material.set_editor_property("is_sky", True)
        # CameraVectorWS points from the pixel to the camera. Negating it
        # produces the outgoing world-space ray for the environment cube.
        camera = expr(material, unreal.MaterialExpressionCameraVectorWS, x=-1000, y=0)
        direction = multiply(material, camera, -1.0, 0)
        cube = expr(material, unreal.MaterialExpressionTextureSampleParameterCube,
            x=-100, y=0, texture=textures["environment"], parameter_name="CampusCourtyard",
            sampler_type=unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR)
        connect(direction, cube)
        brightness = expr(material, unreal.MaterialExpressionScalarParameter,
            x=-100, y=240, parameter_name="EnvironmentBrightness", default_value=128.0)
        radiance = expr(material, unreal.MaterialExpressionMultiply, x=200, y=0)
        connect(cube, radiance, "A", "RGB"); connect(brightness, radiance, "B")
        property_input(radiance, unreal.MaterialProperty.MP_EMISSIVE_COLOR)
        mapping = "unlit_hdr_cubemap_negated_camera_world_vector"
    elif identifier == "glass":
        material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
        material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_THIN_TRANSLUCENT)
        material.set_editor_property("translucency_lighting_mode", unreal.TranslucencyLightingMode.TLM_SURFACE_PER_PIXEL_LIGHTING)
        material.set_editor_property("two_sided", True)
        material.set_editor_property("screen_space_reflections", True)
        property_input(vector(material,[.075,.10,.105]),unreal.MaterialProperty.MP_BASE_COLOR)
        property_input(scalar(material,.065,150),unreal.MaterialProperty.MP_ROUGHNESS)
        property_input(scalar(material,0.0,250),unreal.MaterialProperty.MP_METALLIC)
        property_input(scalar(material,.5,350),unreal.MaterialProperty.MP_SPECULAR)
        fresnel=expr(material,unreal.MaterialExpressionFresnel,x=-900,y=500,exponent=5.0,base_reflect_fraction=.04)
        opacity=add(material,multiply(material,fresnel,.24,500),.055,500)
        property_input(opacity,unreal.MaterialProperty.MP_OPACITY)
        thin=expr(material,unreal.MaterialExpressionThinTranslucentMaterialOutput,x=0,y=750)
        connect(vector(material,[.965,.985,.99],750),thin)
        mapping="thin_translucent_forward_clear_glass_fresnel_opacity_0.055_to_0.295"
    else:
        colors={"seat_blue":[.035,.115,.20],"seat_red":[.31,.045,.035],"seat_gray":[.17,.185,.19],
                "rubber":[.022,.025,.026],"emissive":[.5,.45,.35],"scoreboard":[.008,.012,.018],"court_line":[.82,.82,.75]}
        roughness=.44 if identifier.startswith("seat_") else (.86 if identifier=="rubber" else .55)
        property_input(vector(material,colors[identifier]),unreal.MaterialProperty.MP_BASE_COLOR)
        property_input(scalar(material,roughness,180),unreal.MaterialProperty.MP_ROUGHNESS)
        property_input(scalar(material,0.0,280),unreal.MaterialProperty.MP_METALLIC)
        if identifier=="emissive": property_input(vector(material,[2000.0,1850.0,1600.0],400),unreal.MaterialProperty.MP_EMISSIVE_COLOR)
        if identifier=="scoreboard": property_input(vector(material,[.012,.025,.032],400),unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    MEL.recompile_material(material)
    if not EAL.save_loaded_asset(material): raise RuntimeError("Cannot save material " + asset)
    report={"id":identifier,"asset":material.get_path_name(),"mapping":mapping,
        "blend_mode":str(material.get_editor_property("blend_mode")),
        "shading_model":str(material.get_editor_property("shading_model")),
        "two_sided":bool(material.get_editor_property("two_sided")),
        "tangent_space_normal":bool(material.get_editor_property("tangent_space_normal")),
        "expression_count":int(MEL.get_num_material_expressions(material))}
    if identifier in specifications:
        report.update(source_material=specifications[identifier][0],texture_tile_m=specifications[identifier][1])
    if identifier=="glass":
        report["translucency_lighting_mode"]=str(material.get_editor_property("translucency_lighting_mode"))
        report["screen_space_reflections"] = bool(material.get_editor_property("screen_space_reflections"))
        report["recreated_before_authoring"] = recreated_glass
        report["thin_translucent_output_nodes_created"] = 1
        if material.get_editor_property("blend_mode") != unreal.BlendMode.BLEND_TRANSLUCENT:
            raise RuntimeError("Glass is not translucent")
    if identifier=="environment":
        report.update(is_sky=bool(material.get_editor_property("is_sky")),
            texture_cube=textures["environment"].get_path_name(),
            brightness_parameter="EnvironmentBrightness", default_brightness=128.0,
            sample_direction="-CameraVectorWS", collision_policy="Assigned by the sky-sphere actor; disable collision and shadows")
    unreal.log("RMSIM_CAMPUS_MATERIAL " + identifier + " " + mapping)
    return material,report


def import_seat(root, manifest, materials):
    source=checked_file(root,manifest["seat_row"]["obj"],manifest)
    options=unreal.FbxImportUI()
    for key,value in {"import_mesh":True,"import_as_skeletal":False,
        "mesh_type_to_import":unreal.FBXImportType.FBXIT_STATIC_MESH,
        "automated_import_should_detect_type":False,"import_materials":False,"import_textures":False}.items(): options.set_editor_property(key,value)
    data=options.get_editor_property("static_mesh_import_data")
    for key,value in {"combine_meshes":True,"convert_scene":False,"convert_scene_unit":False,
        "force_front_x_axis":False,"transform_vertex_to_absolute":True,"bake_pivot_in_vertex":False,
        "auto_generate_collision":False,"generate_lightmap_u_vs":False,
        "reorder_material_to_fbx_order":True,"import_uniform_scale":100.0}.items(): data.set_editor_property(key,value)
    data.set_editor_property("normal_import_method",unreal.FBXNormalImportMethod.FBXNIM_IMPORT_NORMALS)
    expected_path=BASE+"/Meshes/SM_SeatRow"
    # A saved OBJ reimport can retain stale scale state despite explicit import
    # options. Recreate only this generated mesh so each import applies the
    # canonical metre-to-centimetre factor once, through the same fresh path.
    replaced_existing_asset=bool(EAL.does_asset_exist(expected_path))
    if replaced_existing_asset:
        if not EAL.delete_asset(expected_path):
            raise RuntimeError("Cannot recreate generated seat mesh: "+expected_path)
        unreal.SystemLibrary.collect_garbage()
        if EAL.does_asset_exist(expected_path):
            raise RuntimeError("Generated seat mesh remained after deletion: "+expected_path)
    objects=import_asset(source,BASE+"/Meshes","SM_SeatRow",options,unreal.FbxFactory())
    meshes=[x for x in objects if isinstance(x,unreal.StaticMesh)]
    if len(meshes)!=1: raise RuntimeError("Expected exactly one combined seat StaticMesh")
    mesh=meshes[0]
    if mesh.get_path_name().split(".")[0]!=expected_path:
        raise RuntimeError("Seat importer did not honor the required native asset path: "+mesh.get_path_name())
    bounds=mesh.get_bounds();size=[float(v)*2 for v in [bounds.box_extent.x,bounds.box_extent.y,bounds.box_extent.z]]
    center=[float(v) for v in [bounds.origin.x,bounds.origin.y,bounds.origin.z]]
    if any(not math.isfinite(v) for v in size+center) or any(abs(size[i]-EXPECTED_CM[i])>.08 for i in range(3)):
        raise RuntimeError("Seat dimensions/orientation differ from canonical cm bounds: "+repr(size))
    if max(abs(v) for v in center)>.02: raise RuntimeError("Seat pivot is not bbox-centered: "+repr(center))
    slots=list(mesh.get_editor_property("static_materials"))
    if len(slots)!=2: raise RuntimeError("Seat must have exactly two native material slots")
    assignments=[]
    for index,slot in enumerate(slots):
        name=str(slot.get_editor_property("material_slot_name"))
        if "frame" in name.lower(): key="steel"
        elif "plastic" in name.lower(): key="seat_blue"
        else: raise RuntimeError("Unknown imported seat material slot: "+name)
        mesh.set_material(index,materials[key]);assignments.append({"index":index,"source_slot":name,"material":key})
    triangles=int(mesh.get_num_triangles(0));vertices=int(mesh.get_num_vertices(0))
    if triangles!=1380: raise RuntimeError("Seat triangle count changed: "+str(triangles))
    mesh.set_editor_property("has_navigation_data",False)
    if not EAL.save_loaded_asset(mesh): raise RuntimeError("Cannot save seat mesh")
    return {"asset":mesh.get_path_name(),"source_obj_sha256":sha(source),"bounds_cm":size,
        "bbox_center_cm":center,"expected_bounds_cm":EXPECTED_CM,"material_assignments":assignments,
        "triangles":triangles,"vertices":vertices,"sections":int(mesh.get_num_sections(0)),
        "fresh_import":True,"recreated_before_import":replaced_existing_asset,
        "recreation_scope":expected_path,"import_uniform_scale":100.0,
        "import_policy":"OBJ canonical Z-up, X-width, Y-rear; convert_scene=false; convert_scene_unit=false; uniform_scale=100",
        "facing_contract":"local -Y; validate appearance in the native scene"}


def main():
    root=argument("GymAssetRoot");output=argument("GymImportReport")
    if output.exists(): raise FileExistsError("Use a fresh GymImportReport path")
    output.parent.mkdir(parents=True,exist_ok=True)
    report={"status":"running","scope":"Native UE source import, material graph authoring and geometry readback",
            "asset_root":str(root),"textures":[],"materials":[],"visual_acceptance_required":True}
    try:
        manifest_path=root/"SOURCE.json";manifest=json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if manifest.get("schema")!="rmsim_campus_gym_sources_v1" or manifest.get("license")!="CC0-1.0":
            raise ValueError("Invalid gym source manifest")
        report["source_manifest_sha256"]=sha(manifest_path)
        for relative in manifest["files"]: checked_file(root,relative,manifest)
        textures=import_textures(root,manifest,report)
        textures["environment"]=import_environment(root,manifest,report);materials={}
        for identifier in MATERIAL_IDS:
            materials[identifier],entry=make_material(identifier,textures);report["materials"].append(entry)
        report["seat_row"]=import_seat(root,manifest,materials)
        report["status"]="passed"
    except Exception as error:
        report["status"]="failed";report["error"]=str(error);report["traceback"]=traceback.format_exc()
        unreal.log_error("RMSIM_CAMPUS_IMPORT_FAILED "+str(error))
        raise
    finally:
        report["script_sha256"]=sha(Path(__file__).resolve())
        report["native_files"]={}
        content=Path(unreal.Paths.project_content_dir()).resolve()/"RMSim/Maps/rmuc2027/Arena/Campus"
        if content.exists():
            for path in sorted(content.rglob("*.uasset")):
                report["native_files"][path.relative_to(content).as_posix()]={"bytes":path.stat().st_size,"sha256":sha(path)}
        output.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    unreal.log("RMSIM_CAMPUS_IMPORT_COMPLETE "+str(output))


if __name__=="__main__":
    main()
