"""Native compilation gate for saved WT parents, ground, leaf and tree instances.

Run after import, with real rendering enabled, never concurrently with authoring:
  UnrealEditor-Cmd RMSim.uproject -run=pythonscript -script=<this file>
    -AllowCommandletRendering -d3d11 -NoShaderWorker -unattended -nop4 -nosplash
    -SceneSource=<package/scene-source.json> -MaterialAuditOutput=<fresh directory>

Loads saved assets and compiles in memory. Never edits graph connections or saves
assets. Shader statistics come from the target FMaterialResource after its native
FinishCompilation, not from the list of shaders eligible for compilation.
"""
import datetime
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import traceback

import unreal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wt_materials import OBJECT_CODE, HEIGHT_DECAL_CODE, RVT_COMPOSITE_CODE, height_blend_parameters, material_flavor, material_id
from wt_tree_alpha import tree_alpha_contract
from wt_landclass import LANDCLASS_CODE, validate_landclass_surface, shader_vectors
from wt_water import water_surface_contract, COLOR_RGBA as WATER_COLOR_RGBA, UE_ROUGHNESS as WATER_ROUGHNESS, UE_METALLIC as WATER_METALLIC, UE_SPECULAR as WATER_SPECULAR
from wt_vertex_opacity import HEIGHT_DECAL_SHADER, validate_scene_vertex_opacity


STAT_FIELDS = (
    'num_vertex_shader_instructions', 'num_pixel_shader_instructions',
    'num_samplers', 'num_vertex_texture_samples', 'num_pixel_texture_samples',
    'num_virtual_texture_samples', 'num_uv_scalars', 'num_interpolator_scalars')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, result):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def asset_file(asset_path):
    if not asset_path.startswith('/Game/') or '..' in asset_path:
        raise ValueError('only project /Game assets are audited')
    package = asset_path.split('.', 1)[0]
    return Path(unreal.Paths.project_content_dir()).resolve() / (package[6:] + '.uasset')


def completed_texture_size(texture):
    # UE5.8 GetSizeX/Y returns placeholder dimensions during async compilation.
    # TextureCompiler.cpp registers this command with the native texture manager;
    # KismetSystemLibrary routes it directly even without a World context.
    unreal.SystemLibrary.execute_console_command(None, 'Editor.AsyncTextureCompilationFinishAll')
    return [int(texture.blueprint_get_size_x()), int(texture.blueprint_get_size_y())]


def landclass_texture_record(texture, entry, material_folder):
    if not isinstance(texture, unreal.Texture2D):
        raise RuntimeError('landclass parameter is missing its original Texture2D')
    name = entry['name'] + ('_C' if entry['srgb'] else '_D') + ('_Point' if entry['nearest'] else '')
    expected = material_folder.rsplit('/Materials/', 1)[0] + '/Textures/' + name + '.' + name
    if texture.get_path_name() != expected or bool(texture.get_editor_property('srgb')) != entry['srgb']:
        raise RuntimeError('landclass texture asset/gamma differs: ' + expected)
    compression = unreal.TextureCompressionSettings.TC_DEFAULT if entry['srgb'] else unreal.TextureCompressionSettings.TC_VECTOR_DISPLACEMENTMAP
    address = unreal.TextureAddress.TA_CLAMP if entry['address']=='clamp' else unreal.TextureAddress.TA_WRAP
    if (texture.get_editor_property('compression_settings') != compression
            or texture.get_editor_property('address_x') != address
            or texture.get_editor_property('address_y') != address):
        raise RuntimeError('landclass texture compression/address differs: ' + expected)
    if entry['no_mips']:
        expected_filter = unreal.TextureFilter.TF_NEAREST if entry['nearest'] else unreal.TextureFilter.TF_BILINEAR
        if (texture.get_editor_property('mip_gen_settings') != unreal.TextureMipGenSettings.TMGS_NO_MIPMAPS
                or texture.get_editor_property('filter') != expected_filter
                or not texture.get_editor_property('never_stream')
                or texture.get_editor_property('lod_bias') != 0
                or texture.get_editor_property('max_texture_size') != 0):
            raise RuntimeError('source weight/cell texture sampling differs: ' + expected)
        size = completed_texture_size(texture)
        if size != entry['size']:
            raise RuntimeError('native source weight/cell texture size differs: ' +
                json.dumps(dict(asset=expected, actual=size, expected=entry['size'])))
    return dict(asset=texture.get_path_name(), asset_file_sha256=digest(asset_file(texture.get_path_name())),
                source_png_sha256=entry['sha256'], source_size=entry['size'],
                srgb=entry['srgb'], nearest=entry['nearest'], no_mips=entry['no_mips'], address=entry['address'])


def graph_record(material, leaves=False, landclass=None):
    edit = unreal.MaterialEditingLibrary
    expressions = list(edit.get_material_expressions(material))
    custom_inputs = []
    parameters = {}
    parameter_types = (unreal.MaterialExpressionScalarParameter,
                       unreal.MaterialExpressionVectorParameter,
                       unreal.MaterialExpressionTextureObjectParameter)
    for expression in expressions:
        if isinstance(expression, parameter_types):
            name = str(expression.get_editor_property('parameter_name'))
            if name in parameters:
                raise RuntimeError('saved parent contains duplicate parameter: ' + name)
            parameters[name] = expression.get_path_name()
    for expression in expressions:
        if not isinstance(expression, unreal.MaterialExpressionCustom):
            continue
        names = [str(name) for name in edit.get_material_expression_input_names(expression)]
        nodes = list(edit.get_inputs_for_material_expression(material, expression))
        if len(names) != len(nodes) or any(node is None for node in nodes):
            raise RuntimeError('saved Custom input is disconnected: ' + expression.get_path_name() +
                               ': ' + json.dumps(dict(names=names, nodes=[node.get_path_name() if node else None for node in nodes])))
        custom_inputs.append(dict(expression=expression.get_path_name(),
                                  inputs={name: node.get_path_name() for name, node in zip(names, nodes)}))
    base = edit.get_material_property_input_node(material, unreal.MaterialProperty.MP_BASE_COLOR)
    if not base:
        raise RuntimeError('saved material has no BaseColor graph: ' + material.get_path_name())
    result = dict(expression_count=len(expressions), base_color=base.get_path_name(),
                  custom_inputs=custom_inputs, parameters=parameters)
    name = material.get_name()
    if name not in ('WT_Parent_HeightDecal', 'WT_Terrain', 'WT_Parent_Land', 'WT_Parent_Water'):
        if not any(isinstance(node, unreal.MaterialExpressionCustom) and str(node.get_editor_property('code')) == OBJECT_CODE for node in expressions):
            raise RuntimeError('saved source surface graph differs from its current alpha/texture adapter')
    if name == 'WT_Parent_Water':
        if (len(expressions) != 4 or not isinstance(base, unreal.MaterialExpressionVectorParameter)
                or str(base.get_editor_property('parameter_name')) != 'SourceWaterColor'
                or material.get_editor_property('blend_mode') != unreal.BlendMode.BLEND_OPAQUE
                or not material.get_editor_property('two_sided')
                or not material.get_editor_property('used_with_instanced_static_meshes')):
            raise RuntimeError('saved water base-color graph or HISM usage differs')
        value = base.get_editor_property('default_value')
        rgba = [float(value.get_editor_property(c)) for c in ('r','g','b','a')]
        if any(abs(a-b)>1e-7 for a,b in zip(rgba, WATER_COLOR_RGBA)):
            raise RuntimeError('saved water parent RGB differs from original level globals')
        for prop, expected in [(unreal.MaterialProperty.MP_ROUGHNESS, WATER_ROUGHNESS),
                               (unreal.MaterialProperty.MP_METALLIC, WATER_METALLIC),
                               (unreal.MaterialProperty.MP_SPECULAR, WATER_SPECULAR)]:
            node = edit.get_material_property_input_node(material, prop)
            if not isinstance(node, unreal.MaterialExpressionConstant) or abs(float(node.get_editor_property('r'))-expected)>1e-7:
                raise RuntimeError('water neutral UE surface constant differs: ' + str(prop))
        if edit.get_material_property_input_node(material, unreal.MaterialProperty.MP_WORLD_POSITION_OFFSET):
            raise RuntimeError('original pond vertex height must remain unchanged')
        result['source_water_rgb'] = rgba[:3]
        result['renderer_coverage'] = 'source_base_color_only'
    if name == 'WT_Parent_Land':
        if landclass is None:
            raise RuntimeError('saved Land graph has no source landclass proof')
        source_nodes = [node for node in expressions if isinstance(node, unreal.MaterialExpressionCustom)
                        and str(node.get_editor_property('code')) == LANDCLASS_CODE]
        if len(source_nodes) != 1:
            raise RuntimeError('saved Land graph differs from original packed-atlas adapter')
        source_node = source_nodes[0]
        fields = [str(field) for field in edit.get_material_expression_input_names(source_node)]
        upstream = dict(zip(fields, edit.get_inputs_for_material_expression(material, source_node)))
        if not isinstance(upstream.get('World'), unreal.MaterialExpressionWorldPosition):
            raise RuntimeError('Land source mapping is missing absolute World Position')
        binding = {}
        folder = material.get_path_name().rsplit('/', 1)[0] + '/'
        for parameter, entry in landclass['textures'].items():
            node = upstream.get(parameter)
            if not isinstance(node, unreal.MaterialExpressionTextureObjectParameter):
                raise RuntimeError('Land source texture parameter is disconnected: ' + parameter)
            binding[parameter] = landclass_texture_record(node.get_editor_property('texture'), entry, folder)
        for parameter, expected in shader_vectors(landclass).items():
            node = upstream.get(parameter)
            if not isinstance(node, unreal.MaterialExpressionVectorParameter):
                raise RuntimeError('Land mapping parameter is disconnected: ' + parameter)
            value = node.get_editor_property('default_value')
            actual = [float(value.get_editor_property(c)) for c in ('r','g','b','a')]
            if any(abs(a-b)>1e-7 for a,b in zip(actual, expected)):
                raise RuntimeError('Land original scale/offset differs: ' + parameter)
        result['source_landclass_surface'] = dict(code_sha256=hashlib.sha256(LANDCLASS_CODE.encode()).hexdigest(),
            verification_sha256=landclass['verification_sha256'], texture_bindings=binding,
            shader_vectors=shader_vectors(landclass))
    if name == 'WT_Terrain':
        codes = [str(node.get_editor_property('code')) for node in expressions if isinstance(node, unreal.MaterialExpressionCustom)]
        if not any('float2 mapUV=float2(World.x,-World.y)/204800.0;' in code
                   and 'float2 detailUV=World.xy/100.0/11.12;' in code for code in codes):
            raise RuntimeError('terrain source macro/index negative-V or detail positive-XZ mapping differs')
        result['terrain_source_uv'] = dict(macro_index='(x,-z)/2048', detail='(x,z)/11.12')
    if name == 'WT_Parent_HeightDecal':
        opacity = edit.get_material_property_input_node(material, unreal.MaterialProperty.MP_OPACITY)
        if opacity != base or str(base.get_editor_property('code')) != HEIGHT_DECAL_CODE:
            raise RuntimeError('height-decal opacity must use the exact source height-threshold formula')
        fields = [str(field) for field in edit.get_material_expression_input_names(base)]
        upstream = dict(zip(fields, edit.get_inputs_for_material_expression(material, base)))
        opacity_uv = upstream.get('OpacityUV')
        if (not isinstance(opacity_uv, unreal.MaterialExpressionTextureCoordinate)
                or opacity_uv.get_editor_property('coordinate_index') != 1
                or material.get_editor_property('blend_mode') != unreal.BlendMode.BLEND_TRANSLUCENT
                or not material.get_editor_property('used_with_instanced_static_meshes')):
            raise RuntimeError('height-decal source UV1, translucency or HISM contract differs')
        result['height_opacity'] = dict(uv_channel=1, component='x',
            code_sha256=hashlib.sha256(HEIGHT_DECAL_CODE.encode()).hexdigest())
    if name in ('WT_Parent_Decal', 'WT_Parent_HeightDecal'):
        writers = [node for node in expressions if isinstance(node, unreal.MaterialExpressionRuntimeVirtualTextureOutput)]
        if len(writers) != 1:
            raise RuntimeError('ground decal parent must have one RVT output')
        writer = writers[0]
        fields = [str(field) for field in edit.get_material_expression_input_names(writer)]
        upstream = dict(zip(fields, edit.get_inputs_for_material_expression(material, writer)))
        required = ('BaseColor', 'Normal', 'Roughness', 'Specular', 'Opacity', 'Mask')
        if any(upstream.get(field) is None for field in required):
            raise RuntimeError('ground decal RVT output is disconnected: ' + str(fields))
        mask = upstream['Mask']
        if not isinstance(mask, unreal.MaterialExpressionConstant) or mask.get_editor_property('r') != 1.0:
            raise RuntimeError('ground decal RVT coverage mask must write one before opacity blending')
        result['rvt_writer'] = {field: upstream[field].get_path_name() for field in required}
    if name in ('WT_Parent_Land', 'WT_Terrain'):
        samples = [node for node in expressions if isinstance(node, unreal.MaterialExpressionRuntimeVirtualTextureSample)]
        if len(samples) != 1 or str(base.get_editor_property('code')) != RVT_COMPOSITE_CODE:
            raise RuntimeError('ground receiver must use one RVT and the premultiplied source-over composition')
        sample = samples[0]
        fields = [str(field) for field in edit.get_material_expression_input_names(sample)]
        upstream = dict(zip(fields, edit.get_inputs_for_material_expression(material, sample)))
        position = upstream.get('World Position')
        if not isinstance(position, unreal.MaterialExpressionWorldPosition) or upstream.get('UVs') is not None:
            raise RuntimeError('ground RVT receiver must use the native absolute World Position input: ' + str(fields))
        texture = sample.get_editor_property('virtual_texture')
        expected = material.get_path_name().rsplit('/', 1)[0] + '/WT_GroundDecalRVT.WT_GroundDecalRVT'
        if not texture or texture.get_path_name() != expected:
            raise RuntimeError('ground receiver RVT asset differs')
        if (sample.get_editor_property('material_type') != texture.get_editor_property('material_type')
                or sample.get_editor_property('single_physical_space') != texture.get_editor_property('single_physical_space')
                or sample.get_editor_property('adaptive') != texture.get_editor_property('adaptive')
                or not sample.get_editor_property('enable_feedback')
                or material.get_editor_property('tangent_space_normal')):
            raise RuntimeError('RVT sampling layout, feedback or world-normal contract differs')
        result['rvt_receiver'] = dict(asset=texture.get_path_name(), world_position_input=position.get_path_name(),
            input_names=fields, composition_code_sha256=hashlib.sha256(RVT_COMPOSITE_CODE.encode()).hexdigest())
    if leaves:
        wpo = edit.get_material_property_input_node(material, unreal.MaterialProperty.MP_WORLD_POSITION_OFFSET)
        if not wpo:
            raise RuntimeError('saved Leaves material has no WPO')
        pending, visited, nodes = [wpo], set(), []
        while pending:
            node = pending.pop()
            if node.get_path_name() in visited:
                continue
            visited.add(node.get_path_name())
            nodes.append(node)
            pending.extend(item for item in edit.get_inputs_for_material_expression(material, node) if item)
        uv_channels = sorted({int(node.get_editor_property('coordinate_index')) for node in nodes
                              if isinstance(node, unreal.MaterialExpressionTextureCoordinate)})
        transforms = [node.get_editor_property('transform_source_type') for node in nodes
                      if isinstance(node, unreal.MaterialExpressionTransform)]
        spaces = unreal.MaterialVectorCoordTransformSource
        if (uv_channels != [3, 4] or transforms.count(spaces.TRANSFORMSOURCE_CAMERA) != 1
                or transforms.count(spaces.TRANSFORMSOURCE_INSTANCE) != 2):
            raise RuntimeError('Leaves WPO must use UV3/4, one CAMERA and two INSTANCE transforms')
        if (material.get_editor_property('blend_mode') != unreal.BlendMode.BLEND_MASKED
                or not material.get_editor_property('two_sided')
                or not material.get_editor_property('used_with_instanced_static_meshes')):
            raise RuntimeError('Leaves masking, sidedness or HISM usage differs')
        codes = [str(node.get_editor_property('code')) for node in nodes
                 if isinstance(node, unreal.MaterialExpressionCustom)]
        result.update(wpo=wpo.get_path_name(), wpo_uv_channels=uv_channels,
                      transform_sources=[str(value) for value in transforms], wpo_custom_code=codes)
    return result


def statistics(material, texture_free=False):
    # MaterialEditingLibrary.cpp:GetStatistics uses this material's resource,
    # SubmitCompileJobs_GameThread if incomplete, and FinishCompilation.
    native = unreal.MaterialEditingLibrary.get_statistics(material)
    result = {name: int(native.get_editor_property(name)) for name in STAT_FIELDS}
    # Texture-sample fields are estimates from material translation; Custom
    # HLSL sampling is not a reliable per-stage nonzero gate. Retain both
    # estimates as evidence and require the completed compiled instructions
    # and actual sampler usage, together with recompile's zero errors.
    if (result['num_vertex_shader_instructions'] <= 0
            or result['num_pixel_shader_instructions'] <= 0
            or result['num_samplers'] < 0
            or (result['num_samplers'] == 0 and not texture_free)):
        raise RuntimeError('target material has no completed executable shader statistics: ' + json.dumps(result))
    return result


def _float32(value):
    return struct.unpack('<f', struct.pack('<f', float(value)))[0]


def _grass_vector(value, expected, label):
    actual = [float(value.get_editor_property(c)) for c in ('r', 'g', 'b', 'a')]
    if actual != [_float32(v) for v in expected]:
        raise RuntimeError('grass source color differs: ' + label)
    return actual


def grass_texture_bindings(checked, model_id):
    model = checked['models'][model_id]
    material = model['material']
    return {
        'Masks': dict(name=model_id+'_Masks', path=model['mask_path'],
                      sha256=material['texture_sha256'], srgb=False),
        'Alpha': dict(name=model_id+'_Alpha', path=model['alpha_path'],
                      sha256=material['alpha_texture_sha256'], srgb=False),
        'Land': dict(name='WT_GrassLandColor', path=checked['land_path'],
                     sha256=checked['definition']['land_color']['texture_sha256'], srgb=True),
    }


def grass_texture_record(texture, expected, root):
    from wt_grass import _png_dimensions
    path = root + '/Textures/' + expected['name'] + '.' + expected['name']
    if not isinstance(texture, unreal.Texture2D) or texture.get_path_name() != path:
        raise RuntimeError('grass original texture binding differs: ' + path)
    before = digest(asset_file(path))
    actual_sha = unreal.EditorAssetLibrary.get_metadata_tag(texture, 'RMSimGrassSourceSHA256')
    compression = (unreal.TextureCompressionSettings.TC_DEFAULT if expected['srgb']
                   else unreal.TextureCompressionSettings.TC_VECTOR_DISPLACEMENTMAP)
    if (actual_sha != expected['sha256'] or bool(texture.get_editor_property('srgb')) != expected['srgb']
            or texture.get_editor_property('compression_settings') != compression
            or texture.get_editor_property('virtual_texture_streaming')
            or texture.get_editor_property('lod_bias') != 0 or texture.get_editor_property('max_texture_size') != 0):
        raise RuntimeError('grass texture provenance/gamma/compression differs: ' + path)
    dimensions = completed_texture_size(texture)
    if dimensions != _png_dimensions(expected['path'])[0]:
        raise RuntimeError('grass texture native/source dimensions differ: ' + path)
    if expected['srgb'] and (texture.get_editor_property('address_x') != unreal.TextureAddress.TA_WRAP
                            or texture.get_editor_property('address_y') != unreal.TextureAddress.TA_WRAP):
        raise RuntimeError('grass macro land color must retain original wrapped addressing')
    after = digest(asset_file(path))
    if after != before:
        raise RuntimeError('grass texture audit modified a saved asset')
    return dict(asset=path, source_png_sha256=expected['sha256'], file_sha256=before,
                file_sha256_after=after, srgb=expected['srgb'], native_dimensions=dimensions)


def grass_surface_properties(material, checked, instance=False):
    choices = [getattr(unreal.MaterialShadingModel, name) for name in dir(unreal.MaterialShadingModel)
               if name.endswith('_FOLIAGE')]
    if len(choices) != 1:
        raise RuntimeError('native enum must expose the exact foliage shading model')
    expected = dict(blend_mode=unreal.BlendMode.BLEND_MASKED, two_sided=True, shading_model=choices[0],
                    opacity_mask_clip_value=_float32(checked['definition']['alpha_clip_value']))
    parent = material.get_editor_property('parent') if instance else material
    overrides = material.get_editor_property('base_property_overrides') if instance else None
    result = {}
    for key, value in expected.items():
        enabled = bool(overrides.get_editor_property('override_'+key)) if instance else False
        actual = (overrides if enabled else parent).get_editor_property(key)
        if actual != value:
            raise RuntimeError('grass masked/two-sided/foliage property differs: ' + key)
        result[key] = str(actual) if key in ('blend_mode', 'shading_model') else actual
        if instance:
            result['override_'+key] = enabled
    if not parent.get_editor_property('used_with_instanced_static_meshes'):
        raise RuntimeError('grass parent has no instanced-static-mesh shader usage')
    if instance and int(overrides.get_editor_property('override_usage_flags')) != 0:
        raise RuntimeError('grass instance must inherit its parent HISM usage')
    return result


def grass_graph_record(material, checked, root):
    from wt_grass import GRASS_CODE
    edit, prop = unreal.MaterialEditingLibrary, unreal.MaterialProperty
    expressions = list(edit.get_material_expressions(material))
    custom = [node for node in expressions if isinstance(node, unreal.MaterialExpressionCustom)]
    if len(custom) != 1 or str(custom[0].get_editor_property('code')) != GRASS_CODE:
        raise RuntimeError('saved grass surface code differs from source RGB masks and separate alpha')
    surface = custom[0]
    output_names = {}
    for property_name, expected in ((prop.MP_BASE_COLOR, 'return'), (prop.MP_OPACITY_MASK, 'OpacityOut')):
        output_name = str(edit.get_material_property_input_node_output_name(material, property_name))
        if edit.get_material_property_input_node(material, property_name) != surface or output_name != expected:
            raise RuntimeError('grass source color/alpha output pin differs: ' + str(property_name))
        output_names[str(property_name)] = output_name
    fields = [str(name) for name in edit.get_material_expression_input_names(surface)]
    nodes = list(edit.get_inputs_for_material_expression(material, surface))
    if len(fields) != len(nodes) or len(set(fields)) != len(fields) or any(node is None for node in nodes):
        raise RuntimeError('grass Custom input is disconnected or duplicated')
    upstream = dict(zip(fields, nodes))
    expected_names = {'Masks', 'Alpha', 'Land', 'UV', 'World', 'Random', 'Fade', 'Correction',
                      'RMin', 'RMax', 'GMin', 'GMax', 'BMin', 'BMax'}
    if set(upstream) != expected_names:
        raise RuntimeError('grass Custom inputs differ from its source contract')
    classes = {'UV': unreal.MaterialExpressionTextureCoordinate, 'World': unreal.MaterialExpressionWorldPosition,
               'Random': unreal.MaterialExpressionPerInstanceRandom, 'Fade': unreal.MaterialExpressionPerInstanceFadeAmount}
    for name, cls in classes.items():
        if not isinstance(upstream[name], cls):
            raise RuntimeError('grass source coordinate/instance input differs: ' + name)
    if upstream['UV'].get_editor_property('coordinate_index') != 0:
        raise RuntimeError('grass must sample its original UV0')
    bindings = {}
    first_model = next(iter(checked['models']))
    for parameter, expected in grass_texture_bindings(checked, first_model).items():
        node = upstream[parameter]
        sampler = unreal.MaterialSamplerType.SAMPLERTYPE_COLOR if expected['srgb'] else unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR
        if (not isinstance(node, unreal.MaterialExpressionTextureObjectParameter)
                or str(node.get_editor_property('parameter_name')) != parameter
                or node.get_editor_property('sampler_type') != sampler):
            raise RuntimeError('grass texture parameter/sampler differs: ' + parameter)
        bindings[parameter] = grass_texture_record(node.get_editor_property('texture'), expected, root)
    colors = {}
    first_layer = next(iter(checked['layers'].values()))
    for channel in ('r', 'g', 'b'):
        for endpoint in ('min', 'max'):
            name = channel.upper()+endpoint.title()
            node = upstream[name]
            if (not isinstance(node, unreal.MaterialExpressionVectorParameter)
                    or str(node.get_editor_property('parameter_name')) != name):
                raise RuntimeError('grass color endpoint is disconnected: ' + name)
            colors[name] = _grass_vector(node.get_editor_property('default_value'), first_layer['color_masks'][channel][endpoint], name)
    correction = upstream['Correction']
    if (not isinstance(correction, unreal.MaterialExpressionScalarParameter)
            or str(correction.get_editor_property('parameter_name')) != 'Correction'
            or float(correction.get_editor_property('default_value')) != _float32(checked['definition']['color_correction_multiplier'])):
        raise RuntimeError('grass original color correction differs')
    for property_name, name, expected in ((prop.MP_ROUGHNESS, 'Roughness', .9), (prop.MP_SPECULAR, 'Specular', .15)):
        node = edit.get_material_property_input_node(material, property_name)
        if (not isinstance(node, unreal.MaterialExpressionScalarParameter)
                or str(node.get_editor_property('parameter_name')) != name
                or float(node.get_editor_property('default_value')) != _float32(expected)):
            raise RuntimeError('grass recorded UE surface parameter differs: ' + name)
    if edit.get_material_property_input_node(material, prop.MP_WORLD_POSITION_OFFSET):
        raise RuntimeError('source grass no-wind adapter has unexpected vertex displacement')
    return dict(expression_count=len(expressions), code_sha256=hashlib.sha256(GRASS_CODE.encode()).hexdigest(),
                inputs={name:node.get_path_name() for name,node in upstream.items()}, output_pins=output_names,
                properties=grass_surface_properties(material, checked), texture_bindings=bindings,
                source_colors=colors, color_correction=float(correction.get_editor_property('default_value')))


def prepare_grass_materials(source_file, report):
    foliage_file = source_file.parent / 'foliage' / 'foliage.json'
    report['grass_materials'] = dict(status='not_present', definition_present=foliage_file.is_file(),
                                     parent_count=0, instance_count=0, native_compilation_verified=False)
    if not foliage_file.is_file():
        return None
    from wt_grass import validate_foliage
    checked = validate_foliage(source_file.parent)
    if checked['map_id'] != 'wt_vlaanderen':
        raise ValueError('grass material gate requires the original Flanders map')
    root = '/Game/RMSim/Maps/' + checked['map_id'] + '/Foliage'
    paths = [root+'/Materials/WT_Grass'] + [root+'/Materials/'+name for name in checked['layers']]
    report['grass_materials'].update(status='authored_pending_compile',
        definition_sha256=checked['definition_sha256'], target_count=len(paths), target_paths=paths,
        missing_assets=[])
    missing = [path for path in paths if not unreal.EditorAssetLibrary.does_asset_exist(path)]
    if missing:
        report['grass_materials'].update(status='not_authored' if len(missing)==len(paths) else 'incomplete_authoring',
                                         missing_assets=missing)
        raise RuntimeError('source foliage exists but saved grass material targets are missing: ' + json.dumps(missing))
    checked['ue_root'] = root
    return checked


def audit_grass_materials(checked, report, evidence):
    if checked is None:
        return
    edit = unreal.MaterialEditingLibrary
    root = checked['ue_root']
    path = root+'/Materials/WT_Grass'
    before = digest(asset_file(path))
    parent = unreal.load_asset(path)
    if not isinstance(parent, unreal.Material):
        raise RuntimeError('saved grass parent is absent: ' + path)
    item = dict(asset=parent.get_path_name(), file_sha256=before, status='running')
    report['grass_parents'].append(item)
    item['graph'] = grass_graph_record(parent, checked, root)
    errors = edit.recompile_material(parent)
    if errors is None or isinstance(errors, (str, bool)):
        raise RuntimeError('UE 5.8 RecompileMaterial must return Array[str]')
    item['recompile_errors'] = [str(error) for error in errors]
    if item['recompile_errors']:
        raise RuntimeError('saved grass parent compilation failed: ' + json.dumps(item['recompile_errors']))
    item['completed_shader_statistics'] = statistics(parent)
    item['file_sha256_after'] = digest(asset_file(path))
    if item['file_sha256_after'] != before:
        report['saved_assets_modified'] = True
        raise RuntimeError('native grass gate rewrote its saved parent')
    item['status'] = 'passed'
    save(evidence/'verification.json', report)
    for name, layer in checked['layers'].items():
        path = root+'/Materials/'+name
        before = digest(asset_file(path))
        instance = unreal.load_asset(path)
        if not isinstance(instance, unreal.MaterialInstanceConstant) or instance.get_editor_property('parent') != parent:
            raise RuntimeError('grass layer instance or parent differs: ' + path)
        item = dict(asset=instance.get_path_name(), source_layer_id=name, source_model_id=layer['model_id'],
                    parent=parent.get_path_name(), file_sha256=before, status='running')
        report['grass_instances'].append(item)
        item['properties'] = grass_surface_properties(instance, checked, instance=True)
        item['texture_bindings'] = {}
        for parameter, expected in grass_texture_bindings(checked, layer['model_id']).items():
            texture = edit.get_material_instance_texture_parameter_value(instance, parameter)
            item['texture_bindings'][parameter] = grass_texture_record(texture, expected, root)
        item['source_colors'] = {}
        for channel in ('r', 'g', 'b'):
            for endpoint in ('min', 'max'):
                parameter = channel.upper()+endpoint.title()
                value = edit.get_material_instance_vector_parameter_value(instance, parameter)
                item['source_colors'][parameter] = _grass_vector(value, layer['color_masks'][channel][endpoint], name+'.'+parameter)
        item['scalar_parameters'] = {}
        for parameter, expected in (('Correction', checked['definition']['color_correction_multiplier']),
                                    ('Roughness', .9), ('Specular', .15)):
            value = float(edit.get_material_instance_scalar_parameter_value(instance, parameter))
            if value != _float32(expected):
                raise RuntimeError('grass layer scalar differs: '+name+'.'+parameter)
            item['scalar_parameters'][parameter] = value
        item['completed_shader_statistics'] = statistics(instance)
        item['compiler_errors_checked_on_parent'] = parent.get_path_name()
        item['file_sha256_after'] = digest(asset_file(path))
        if item['file_sha256_after'] != before:
            report['saved_assets_modified'] = True
            raise RuntimeError('native grass gate rewrote a saved layer instance')
        item['status'] = 'passed'
        save(evidence/'verification.json', report)
    report['grass_materials'].update(status='passed', parent_count=len(report['grass_parents']),
        instance_count=len(report['grass_instances']), native_compilation_verified=True)
    unreal.log('WT_MATERIAL_AUDIT_GRASS_PASSED '+str(report['grass_materials']['target_count']))


def verify(source_file, output):
    source_file = Path(source_file).resolve(strict=True)
    source = json.loads(source_file.read_text(encoding='utf-8-sig'))
    if source.get('id') != 'wt_vlaanderen' or source.get('schema') != 'rm_scene_map_source_v1':
        raise ValueError('expected original wt_vlaanderen scene source')
    evidence = Path(output).resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    report = dict(schema='wt_native_material_audit_v1', status='running', complete=False,
        started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        engine=unreal.SystemLibrary.get_engine_version(),
        source_manifest_sha256=digest(source_file), audit_script_sha256=digest(Path(__file__)),
        scope='saved_parent_graphs_leaf_tree_ground_water_and_grass_native_shader_compilation',
        rendering_visual_quality_verified=False, in_memory_recompile=True,
        saved_assets_modified=False, parents=[], leaf_instances=[], height_decal_instances=[],
        land_instances=[], ground_other_instances=[], tree_instances=[], water_instances=[],
        grass_parents=[], grass_instances=[],
        supporting_scripts_sha256={name:digest(Path(__file__).with_name(name)) for name in ('wt_materials.py','wt_vertex_opacity.py','wt_tree_alpha.py','wt_runtime_virtual_texture.py','wt_landclass.py','wt_water.py','wt_grass.py')},
        api_evidence={
            'recompile_material': 'UE 5.8 MaterialEditingLibrary.cpp:1035-1059; TArray<FString> compiler errors',
            'get_statistics': 'UE 5.8 MaterialEditingLibrary.cpp:2116-2159; target resource FinishCompilation and actual shader statistics',
            'texture_dimensions': 'UE 5.8 Texture2D.cpp:349-377 returns placeholders while compiling; Editor.AsyncTextureCompilationFinishAll invokes native FinishAllCompilation before size readback'},
        limitations=['Compilation target is the active RHI shader platform; platform coverage is recorded in the accompanying Unreal log.',
                     'This gate does not assess rendered appearance or wind animation.'])
    save(evidence/'verification.json', report)
    try:
        command = unreal.SystemLibrary.get_command_line()
        if re.search(r'(?:^|\s)-(?:nullrhi|noshadercompile)\b', command, re.I):
            raise ValueError('material audit requires a real RHI with shader compilation enabled')
        if not re.search(r'(?:^|\s)-AllowCommandletRendering\b', command, re.I):
            raise ValueError('pass -AllowCommandletRendering for native material validation')
        folder = '/Game/RMSim/Maps/' + source['id'] + '/Materials/'
        report['source_vertex_opacity'] = validate_scene_vertex_opacity(source_file.parent, source)
        landclass = validate_landclass_surface(source_file.parent, source)
        report['source_landclass_surface'] = source.get('source_landclass_surface')
        report['source_water_surface'] = water_surface_contract(source, source_file.parent)
        grass = prepare_grass_materials(source_file, report)
        save(evidence/'verification.json', report)
        flavors = {material_flavor(item['shader']) for item in source['materials']}
        if 'Leaves' not in flavors:
            raise ValueError('source has no facing leaf materials')
        parent_names = ['WT_Parent_' + flavor for flavor in sorted(flavors)] + ['WT_Terrain']
        parent_paths = set()
        for name in parent_names:
            path = folder + name
            filename = asset_file(path)
            before = digest(filename)
            material = unreal.load_asset(path)
            if not isinstance(material, unreal.Material):
                raise RuntimeError('saved parent is absent: ' + path)
            item = dict(asset=material.get_path_name(), file_sha256=before, status='running')
            report['parents'].append(item)
            item['graph'] = graph_record(material, leaves=name == 'WT_Parent_Leaves', landclass=landclass)
            # The sole native return is an array, not bool+out. Empty = no errors.
            errors = unreal.MaterialEditingLibrary.recompile_material(material)
            if errors is None or isinstance(errors, (str, bool)):
                raise RuntimeError('UE 5.8 RecompileMaterial must return Array[str]')
            item['recompile_errors'] = [str(error) for error in errors]
            if item['recompile_errors']:
                raise RuntimeError('final saved graph compilation failed: ' + json.dumps(item['recompile_errors']))
            item['completed_shader_statistics'] = statistics(material, texture_free=name == 'WT_Parent_Water')
            item['file_sha256_after'] = digest(filename)
            if item['file_sha256_after'] != before:
                report['saved_assets_modified'] = True
                raise RuntimeError('native material gate rewrote a saved asset')
            item['status'] = 'passed'
            parent_paths.add(material.get_path_name())
            save(evidence/'verification.json', report)
            unreal.log('WT_MATERIAL_AUDIT_PARENT_PASSED ' + path)
        instance_groups = {'rendinst_facing_leaves': 'leaf_instances', HEIGHT_DECAL_SHADER: 'height_decal_instances',
                           'land_mesh': 'land_instances', 'land_mesh_combined_detailed_decal': 'ground_other_instances',
                           'water_aces': 'water_instances',
                           'rendinst_tree': 'tree_instances', 'rendinst_tree_colored': 'tree_instances'}
        for spec in source['materials']:
            if spec['shader'] not in instance_groups:
                continue
            if material_id(spec) != spec['id']:
                raise ValueError('source instance identity differs from its shader/texture contract')
            group = instance_groups[spec['shader']]
            path = folder + spec['id']
            filename = asset_file(path)
            before = digest(filename)
            material = unreal.load_asset(path)
            if not isinstance(material, unreal.MaterialInstanceConstant):
                raise RuntimeError('saved source instance is absent: ' + path)
            parent = material.get_editor_property('parent')
            parent_name = 'WT_Parent_' + material_flavor(spec['shader'])
            expected_parent = folder + parent_name + '.' + parent_name
            if not parent or parent.get_path_name() != expected_parent:
                raise RuntimeError('saved source instance parent differs: ' + path)
            item = dict(asset=material.get_path_name(), parent=parent.get_path_name(),
                        source_shader=spec['shader'], file_sha256=before, completed_shader_statistics=statistics(material, texture_free=group == 'water_instances'))
            if group == 'water_instances':
                vector = unreal.MaterialEditingLibrary.get_material_instance_vector_parameter_value(material, 'SourceWaterColor')
                rgba = [float(vector.get_editor_property(c)) for c in ('r','g','b','a')]
                if any(abs(a-b)>1e-7 for a,b in zip(rgba, WATER_COLOR_RGBA)):
                    raise RuntimeError('saved water instance RGB differs from original level globals')
                item['source_water_surface'] = report['source_water_surface']
                item['actual_color_rgba'] = rgba
            if group == 'land_instances':
                if landclass is None:
                    raise RuntimeError('Land instance is missing its source proof')
                item['texture_bindings'] = {}
                for parameter, entry in landclass['textures'].items():
                    texture = unreal.MaterialEditingLibrary.get_material_instance_texture_parameter_value(material, parameter)
                    item['texture_bindings'][parameter] = landclass_texture_record(texture, entry, folder)
            if group == 'tree_instances':
                contract = tree_alpha_contract(spec)
                actual_source = unreal.MaterialEditingLibrary.get_material_instance_scalar_parameter_value(material, 'AlphaFromT1')
                if actual_source != float(contract is not None):
                    raise RuntimeError('ordinary tree alpha source differs from its audited texture content')
                item['alpha_from_t1'] = bool(contract)
                if contract:
                    texture = unreal.MaterialEditingLibrary.get_material_instance_texture_parameter_value(material, 'T1')
                    texture_name = contract['alpha'] + '_D'
                    expected_texture = folder.rsplit('/Materials/', 1)[0] + '/Textures/' + texture_name + '.' + texture_name
                    if not texture or texture.get_path_name() != expected_texture or texture.get_editor_property('srgb'):
                        raise RuntimeError('source tree coverage must use its linear T1 mask')
                    overrides = material.get_editor_property('base_property_overrides')
                    if (not overrides.get_editor_property('override_opacity_mask_clip_value')
                            or abs(overrides.get_editor_property('opacity_mask_clip_value')-contract['atest']/255)>1e-7):
                        raise RuntimeError('source tree alpha-test threshold differs')
                    for key in ('color','alpha'):
                        if digest(source_file.parent/'textures'/(contract[key]+'.png')) != contract[key+'_sha256']:
                            raise RuntimeError('audited tree source PNG bytes differ')
                    item['source_split_alpha'] = contract
                    item['alpha_texture'] = texture.get_path_name()
            if spec['shader'] == HEIGHT_DECAL_SHADER:
                vector = unreal.MaterialEditingLibrary.get_material_instance_vector_parameter_value(material, 'HeightBlend')
                actual = [float(vector.get_editor_property(channel)) for channel in ('r','g','b','a')]
                expected = height_blend_parameters(spec)
                if any(abs(a-b)>1e-7 for a,b in zip(actual, expected)):
                    raise RuntimeError('source height-blend parameters differ: ' + path)
                item['height_blend_parameters'] = actual
                item['texture_bindings'] = {}
                for index, color, fallback in [(0, True, 'White'), (2, False, 'NormalAG')]:
                    texture = unreal.MaterialEditingLibrary.get_material_instance_texture_parameter_value(material, 'T'+str(index))
                    raw_name = spec['texture_slots'][index]
                    source_name = raw_name.split('*')[0].split('$')[0] if raw_name else '_Default'+fallback
                    name = re.sub('[^A-Za-z0-9_]', '_', source_name) + ('_C' if color else '_D')
                    expected_texture = folder.rsplit('/Materials/', 1)[0] + '/Textures/' + name + '.' + name
                    if not texture or texture.get_path_name() != expected_texture:
                        raise RuntimeError('source height-decal texture binding differs: ' + path)
                    item['texture_bindings']['T'+str(index)] = texture.get_path_name()
            item['file_sha256_after'] = digest(filename)
            if item['file_sha256_after'] != before:
                report['saved_assets_modified'] = True
                raise RuntimeError('native material gate rewrote a saved source instance')
            item['status'] = 'passed'
            report[group].append(item)
            save(evidence/'verification.json', report)
        audit_grass_materials(grass, report, evidence)
        groups = ('parents','leaf_instances','height_decal_instances','land_instances','ground_other_instances',
                  'tree_instances','water_instances','grass_parents','grass_instances')
        targets = [item for group in groups for item in report[group]]
        if any(item['status'] != 'passed' for item in targets) or len({item['asset'] for item in targets}) != len(targets):
            raise RuntimeError('material target list contains incomplete or duplicate shader assets')
        report['actual_asset_counts'] = {group:len(report[group]) for group in groups}
        report['actual_asset_counts']['total'] = len(targets)
        report.update(status='passed', complete=True,
                      finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
        unreal.log('WT_MATERIAL_AUDIT_COMPLETE ' + str(evidence/'verification.json'))
    except Exception as error:
        report.update(status='failed', complete=True, error=str(error), traceback=traceback.format_exc(),
                      finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
        unreal.log_error('WT_MATERIAL_AUDIT_FAILED ' + str(error))
        raise
    finally:
        save(evidence/'verification.json', report)
    return report


if __name__ == '__main__':
    command = unreal.SystemLibrary.get_command_line()
    source_arg = re.search(r'-SceneSource=(?:"([^"]+)"|(\S+))', command)
    output_arg = re.search(r'-MaterialAuditOutput=(?:"([^"]+)"|(\S+))', command)
    if not source_arg or not output_arg:
        raise ValueError('pass -SceneSource=<scene-source.json> -MaterialAuditOutput=<fresh directory>')
    verify(source_arg.group(1) or source_arg.group(2), output_arg.group(1) or output_arg.group(2))