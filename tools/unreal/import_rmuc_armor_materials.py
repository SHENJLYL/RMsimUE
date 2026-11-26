"""Generate the matte armor material with independent masked lamp emission."""
import unreal

OUTPUT = "/Game/RMSim/Maps/rmuc2027/ArmorPresentation"
EDIT = unreal.MaterialEditingLibrary
PROP = unreal.MaterialProperty


def material(name):
    path = f"{OUTPUT}/{name}"
    asset = unreal.load_asset(path) if unreal.EditorAssetLibrary.does_asset_exist(path) else (
        unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            name, OUTPUT, unreal.Material, unreal.MaterialFactoryNew()))
    if not asset:
        raise RuntimeError(f"Could not create {path}")
    EDIT.delete_all_material_expressions(asset)
    asset.set_editor_property("two_sided", True)
    return asset


def bind(node, prop):
    if not EDIT.connect_material_property(node, "", prop):
        raise RuntimeError(f"Could not connect {prop}")


def connect(source, target, pin, channel=""):
    if not EDIT.connect_material_expressions(source, channel, target, pin):
        raise RuntimeError(f"Could not connect {source.get_name()} to {pin}")


def constant(mat, value, y):
    node = EDIT.create_material_expression(mat, unreal.MaterialExpressionConstant, -420, y)
    node.set_editor_property("r", value)
    return node


def finish(mat):
    EDIT.recompile_material(mat)
    if not unreal.EditorAssetLibrary.save_loaded_asset(mat):
        raise RuntimeError(f"Could not save {mat.get_path_name()}")
    unreal.log(f"RMUC_ARMOR_MATERIAL_READY {mat.get_path_name()}")


if not unreal.EditorAssetLibrary.does_asset_exist(f"{OUTPUT}/NeutralArmorFace"):
    sticker = material("NeutralArmorFace")
    sticker.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
    texture = EDIT.create_material_expression(sticker, unreal.MaterialExpressionTextureSampleParameter2D, -620, 0)
    texture.set_editor_property("parameter_name", "ArmorSticker")
    white = unreal.load_asset("/Engine/EngineResources/WhiteSquareTexture.WhiteSquareTexture")
    if not white:
        raise RuntimeError("Engine default texture is unavailable")
    texture.set_editor_property("texture", white)
    fill = EDIT.create_material_expression(sticker, unreal.MaterialExpressionMultiply, -190, 120)
    connect(texture, fill, "A", "RGB")
    # Keep the print achromatic under colored spot lighting. Light spill is handled by the actual lamps.
    connect(constant(sticker, .70, 80), fill, "B")
    color = EDIT.create_material_expression(sticker, unreal.MaterialExpressionVectorParameter, -600, 360)
    color.set_editor_property("parameter_name", "ArmorLampColor")
    color.set_editor_property("default_value", unreal.LinearColor(.008, .008, .008, 1))
    lamp = EDIT.create_material_expression(sticker, unreal.MaterialExpressionMultiply, -190, 380)
    connect(texture, lamp, "A", "A")
    connect(color, lamp, "B")
    emission = EDIT.create_material_expression(sticker, unreal.MaterialExpressionAdd, 40, 160)
    connect(fill, emission, "A")
    connect(lamp, emission, "B")
    adapted = EDIT.create_material_expression(sticker, unreal.MaterialExpressionEyeAdaptationInverse, 220, 160)
    connect(emission, adapted, EDIT.get_material_expression_input_names(adapted)[0])
    bind(adapted, PROP.MP_EMISSIVE_COLOR)
    finish(sticker)

if not unreal.EditorAssetLibrary.does_asset_exist(f"{OUTPUT}/TeamEmitter"):
    emitter = material("TeamEmitter")
    emitter.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
    team = EDIT.create_material_expression(emitter, unreal.MaterialExpressionVectorParameter, -420, 0)
    team.set_editor_property("parameter_name", "ArmorLampColor")
    team.set_editor_property("default_value", unreal.LinearColor(.008, .008, .008, 1))
    normalized = EDIT.create_material_expression(emitter, unreal.MaterialExpressionEyeAdaptationInverse, -190, 0)
    connect(team, normalized, EDIT.get_material_expression_input_names(normalized)[0])
    bind(normalized, PROP.MP_EMISSIVE_COLOR)
    finish(emitter)

