"""UE Editor commandlet script for the derived RMUC2027 field device assets."""
import hashlib
import json
from pathlib import Path
import shlex
import sys

import unreal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from import_vehicle_assets import import_asset


ROOT = '/Game/RMSim/Maps/rmuc2027'
OUTPUT = ROOT + '/Rune'
RUNE_NEUTRAL_TRIM = (.035, .035, .035, 1.)
ROTOR_CAD_COLOR_SLOTS = {'RM27_M00', 'RM27_M02', 'RM27_M07'}
RUNE_HUB_SLOTS = {'RuneHubRed', 'RuneHubBlue'}


def lamp_material(name, paint):
    material = unreal.load_asset(f'{OUTPUT}/{name}') or unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        name, OUTPUT, unreal.Material, unreal.MaterialFactoryNew())
    edit = unreal.MaterialEditingLibrary
    edit.delete_all_material_expressions(material)
    base = edit.create_material_expression(material, unreal.MaterialExpressionVectorParameter, -400, 0)
    base.set_editor_property('parameter_name', 'RunePaintTint')
    base.set_editor_property('default_value', unreal.LinearColor(*paint))
    edit.connect_material_property(base, '', unreal.MaterialProperty.MP_BASE_COLOR)
    color = edit.create_material_expression(material, unreal.MaterialExpressionVectorParameter, -400, 140)
    color.set_editor_property('parameter_name', 'RuneLampColor')
    color.set_editor_property('default_value', unreal.LinearColor(0., 0., 0., 1.))
    edit.connect_material_property(color, '', unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    material.set_editor_property('two_sided', True)
    edit.recompile_material(material)
    unreal.EditorAssetLibrary.save_loaded_asset(material)
    return material


def rune_track_material():
    name = 'RuneTrackGlow'
    material = unreal.load_asset(f'{OUTPUT}/{name}') or unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        name, OUTPUT, unreal.Material, unreal.MaterialFactoryNew())
    edit = unreal.MaterialEditingLibrary
    edit.delete_all_material_expressions(material)
    material.set_editor_property('shading_model', unreal.MaterialShadingModel.MSM_UNLIT)
    material.set_editor_property('two_sided', True)
    color = edit.create_material_expression(material, unreal.MaterialExpressionVectorParameter, -400, 0)
    color.set_editor_property('parameter_name', 'RuneLampColor')
    color.set_editor_property('default_value', unreal.LinearColor(0., 0., 0., 1.))
    scale = edit.create_material_expression(material, unreal.MaterialExpressionMultiply, -180, 0)
    # Keep the LED overlay visible against a daylight-exposed arena while preserving the animated intensity.
    scale.set_editor_property('const_b', 62.)
    if not edit.connect_material_expressions(color, '', scale, 'A') or not edit.connect_material_property(
            scale, '', unreal.MaterialProperty.MP_EMISSIVE_COLOR):
        raise RuntimeError('Could not connect the rune track emissive material')
    edit.recompile_material(material)
    if not unreal.EditorAssetLibrary.save_loaded_asset(material):
        raise RuntimeError('Could not save the rune track material')
    unreal.log(f'RMUC_RUNE_TRACK_MATERIAL {material.get_path_name()}')


def dart_cad_material(folder, name, finish):
    material = unreal.load_asset(f'{folder}/{name}') or unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        name, folder, unreal.Material, unreal.MaterialFactoryNew())
    graph = unreal.MaterialEditingLibrary
    graph.delete_all_material_expressions(material)
    colour = graph.create_material_expression(material, unreal.MaterialExpressionConstant3Vector, -320, 0)
    colour.set_editor_property('constant', unreal.LinearColor(*finish['base_color']))
    graph.connect_material_property(colour, '', unreal.MaterialProperty.MP_BASE_COLOR)
    for key, prop in [('metallic', unreal.MaterialProperty.MP_METALLIC), ('roughness', unreal.MaterialProperty.MP_ROUGHNESS)]:
        value = graph.create_material_expression(material, unreal.MaterialExpressionConstant, -320, 100)
        value.set_editor_property('r', finish[key])
        graph.connect_material_property(value, '', prop)
    graph.recompile_material(material)
    unreal.EditorAssetLibrary.save_loaded_asset(material)
    return material


def main():
    args = shlex.split(unreal.SystemLibrary.get_command_line().replace('\\', '/'))
    if '-RuneLightsOnly' in args:
        rune_track_material()
        return
    report_path = Path(next(arg.split('=', 1)[1] for arg in args if arg.startswith(
        ('-RuneSource=', '-DartSource=', '-OutpostSource=', '-VisibilitySource=', '-BaseArmorSource='))))
    report = json.loads(report_path.read_text(encoding='utf-8'))
    is_visibility = 'collision_source_sha256' in report
    if is_visibility:
        destination, log_prefix = ROOT + '/Visibility', 'RMUC_CENTRE_VISIBILITY_IMPORTED'
    else:
        destination, log_prefix = {
            4774244: (ROOT + '/BaseArmor', 'RMUC_BASE_ARMOR_IMPORTED'),
            3749800: (ROOT + '/Dart', 'RMUC_DART_IMPORTED'),
            2603236: (ROOT + '/RuneOutposts', 'RMUC_OUTPOST_IMPORTED'),
        }.get(report.get('source_product'), (OUTPUT, 'RMUC_RUNE_IMPORTED'))
    cad_finishes = {name: dart_cad_material(destination, name, values)
                    for name, values in report.get('launcher_cad_palette', {}).items()}
    lamps = {}
    if destination == OUTPUT:
        rune_track_material()
        lamps[''] = lamp_material('RuneLamp', RUNE_NEUTRAL_TRIM)
        for name in report.get('rune_paints', {}):
            lamps[name] = lamp_material(f'RuneLamp_{name}', RUNE_NEUTRAL_TRIM)
    hub = None
    if any(RUNE_HUB_SLOTS.intersection(spec['materials']) for spec in report['assets']):
        hub = lamp_material('RuneHubLamp', (.305, .305, .305, 1.))
    for spec in report['assets']:
        path = report_path.parent / spec['file']
        if hashlib.sha256(path.read_bytes()).hexdigest() != spec['sha256']:
            raise ValueError(f'{path.name} does not match the derived CAD report')
        options = unreal.FbxImportUI()
        for key, value in {'import_mesh': True, 'import_as_skeletal': False,
                'mesh_type_to_import': unreal.FBXImportType.FBXIT_STATIC_MESH,
                'import_materials': False, 'import_textures': False}.items():
            options.set_editor_property(key, value)
        settings = options.get_editor_property('static_mesh_import_data')
        for key, value in {'combine_meshes': True, 'convert_scene': True, 'convert_scene_unit': True,
                'force_front_x_axis': True, 'transform_vertex_to_absolute': True, 'bake_pivot_in_vertex': False,
                'auto_generate_collision': False, 'reorder_material_to_fbx_order': True}.items():
            settings.set_editor_property(key, value)
        settings.set_editor_property('normal_import_method', unreal.FBXNormalImportMethod.FBXNIM_IMPORT_NORMALS)
        meshes = [asset for asset in import_asset(path, destination, spec['name'], options) if isinstance(asset, unreal.StaticMesh)]
        if len(meshes) != 1:
            raise RuntimeError(f'Expected one mesh for {path.name}')
        mesh = meshes[0]
        slots = mesh.get_editor_property('static_materials')
        names = {str(slot.get_editor_property('material_slot_name')) for slot in slots}
        expected = set(spec['materials'])
        # Reimport retains these slots after their faces move to the native dart head.
        retired = ({'RM27_M22', 'RM27_M39', 'RM27_M40', 'RM27_M41'} if spec['name'] == 'base_fixed_visual'
                   and report.get('triangle_totals', {}).get('visual', {}).get('dart_heads') else set())
        if spec['name'] in {'base_blue_armor_1', 'base_blue_armor_2', 'base_blue_armor_3'} and 'RM27_M25' in expected:
            retired.add('RM27_M02')
        if len(names) != len(slots) or not expected <= names or names - expected - retired:
            raise RuntimeError(f'{path.name} material slot import mismatch: {sorted(names)} vs {sorted(expected)}')
        for index, slot in enumerate(slots):
            name = str(slot.get_editor_property('material_slot_name'))
            if name in cad_finishes:
                mat = cad_finishes[name]
            elif name in RUNE_HUB_SLOTS:
                mat = hub
            elif destination == OUTPUT and spec['name'] == 'rune_rotor' and name in ROTOR_CAD_COLOR_SLOTS:
                mat = lamps['']
            else:
                mat = (unreal.load_asset(f'{ROOT}/Materials/{name}') if name.startswith('RM27_M')
                    else lamps.get(name.partition('_')[2]))
            if not mat:
                raise RuntimeError(f'Missing source arena material {name}')
            mesh.set_material(index, mat)
        if (is_visibility or spec.get('query', False)
                or (report.get('source_product') == 3749800 and not spec.get('visual_only'))
                or spec['name'].endswith('_rotor')):
            body = mesh.get_editor_property('body_setup')
            body.set_editor_property('collision_trace_flag', unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE)
            body.set_editor_property('double_sided_geometry', True)
        unreal.EditorAssetLibrary.save_loaded_asset(mesh)
        box = mesh.get_bounding_box()
        unreal.log(f'{log_prefix} {mesh.get_path_name()} bounds_cm={box.min}..{box.max}')


if __name__ == '__main__':
    main()
