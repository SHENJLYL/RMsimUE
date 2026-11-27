"""Create the black and white print material for the RMUC2027 supply fiducials."""
import unreal

package = "/Game/RMSim/Maps/rmuc2027/SupplyTags"
name = "SupplyTagSurface"
path = package + "/" + name
assets = unreal.EditorAssetLibrary
edit = unreal.MaterialEditingLibrary
if assets.does_asset_exist(path):
    material = unreal.load_asset(path)
else:
    tool = unreal.AssetToolsHelpers.get_asset_tools()
    material = tool.create_asset(name, package, unreal.Material, unreal.MaterialFactoryNew())
if not material:
    raise RuntimeError(f"Could not load or create {path}")
edit.delete_all_material_expressions(material)
material.set_editor_property("two_sided", True)
material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
ink = edit.create_material_expression(material, unreal.MaterialExpressionVectorParameter, -320, 0)
ink.set_editor_property("parameter_name", "TagInk")
ink.set_editor_property("default_value", unreal.LinearColor(.75, .75, .75, 1))
if not edit.connect_material_property(ink, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR):
    raise RuntimeError("Failed to bind the supply tag ink colour")
edit.recompile_material(material)
if not assets.save_loaded_asset(material):
    raise RuntimeError(f"Could not save {path}")
unreal.log(f"RMUC_SUPPLY_TAG_MATERIAL_READY {path}")
