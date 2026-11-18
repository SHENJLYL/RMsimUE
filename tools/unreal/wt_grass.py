"""Author source grass meshes, masked materials and streamed Landscape Grass.

The independent foliage contract binds every local source file by SHA256.
Geometry remains in source metres. Native Landscape Grass owns view-dependent
placement and culling; these decorative instances have no vehicle collision.
"""
import hashlib
import json
import math
from pathlib import Path
import re
import struct

VERSION = 3
NODE_PREFIX = 'RMSim source grass:'
GRASS_CODE = """
float4 masks=Texture2DSample(Masks,MasksSampler,UV);
float alpha=Texture2DSample(Alpha,AlphaSampler,UV).r;
float3 land=Texture2DSample(Land,LandSampler,float2(World.x,-World.y)/204800.0).rgb;
float3 red=lerp(RMin.rgb,RMax.rgb,Random);
float3 blue=lerp(BMin.rgb,BMax.rgb,Random);
float3 green=lerp(GMin.rgb,GMax.rgb,masks.g);
float3 color=lerp(land,red,masks.r);
color=lerp(color,blue,masks.b);
OpacityOut=alpha*Fade;
float4 packed=Texture2DSample(PackedNormal,PackedNormalSampler,UV);
float2 normalXY=packed.ag*2.0-1.0;
float3 sourceNormal=float3(normalXY,sqrt(saturate(1.0-dot(normalXY,normalXY))));
NormalOut=normalize(lerp(float3(0,0,1),sourceNormal,UseSourceNormal));
TransmissionOut=lerp(DefaultTransmission,packed.b,UseSourceNormal);
RoughnessOut=lerp(DefaultRoughness,saturate(1.0-packed.r),UseSourceNormal);
return color*green*Correction;
"""

GRASS_NORMAL_CODE = """
float3 n=normalize(NormalWorld);
float3 up=normalize(InstanceUp);
float elevation=dot(n,up);
float3 horizontal=n-up*elevation;
float h2=dot(horizontal,horizontal);
float3 adapted=n;
if(elevation<MinimumUp)
    adapted=h2>1e-8 ? horizontal*rsqrt(h2)*sqrt(saturate(1-MinimumUp*MinimumUp))+up*MinimumUp : up;
// World-space normals are not automatically flipped by UE's two-sided path.
// Original meshes retain the standard face-sign behavior; source grass cards
// keep their terrain-up roots and source normal details in the upper hemisphere.
return normalize(lerp(n*Side,adapted,UseSourceNormal));
"""


def _sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def _require(value, message):
    if not value:
        raise ValueError(message)


def _file(base, relative, expected):
    relative = Path(relative)
    _require(not relative.is_absolute() and '..' not in relative.parts, 'foliage file must be local')
    target = (base / relative).resolve(strict=True)
    _require(target.is_relative_to(base) and target.is_file(), 'foliage file escapes its package')
    _require(re.fullmatch('[0-9a-f]{64}', expected) and _sha(target) == expected,
             'foliage source hash differs: ' + str(relative))
    return target


def _png_dimensions(path):
    header = path.read_bytes()[:33]
    _require(len(header) == 33 and header[:8] == b'\x89PNG\r\n\x1a\n', 'foliage texture is not PNG')
    _require(header[24] == 8, 'foliage texture must have eight-bit source channels')
    return list(struct.unpack('>II', header[16:24])), header[25]


def _finite(values, count, message):
    _require(isinstance(values, list) and len(values) == count and
             all(isinstance(v, (int, float)) and math.isfinite(v) for v in values), message)


def source_color_linear(values):
    """gpuGrass.cpp srgb_color4 / fast_grass.cpp: preserve alpha, pow RGB by 2.2."""
    return [float(v)**2.2 for v in values[:3]] + [float(values[3])]


def validate_foliage(package, foliage_base=None):
    package = Path(package).resolve(strict=True)
    base = (Path(foliage_base).resolve(strict=True) if foliage_base is not None else
            package / ('foliage-enhanced' if (package/'foliage-enhanced/foliage.json').is_file() else 'foliage'))
    path = base / 'foliage.json'
    definition = _read(path)
    source, descriptor = _read(package/'scene-source.json'), _read(package/'map.json')
    enhanced = definition.get('schema') == 'wt_enhanced_foliage_v1'
    _require(enhanced or definition.get('schema') == 'wt_source_foliage_v1', 'unsupported foliage schema')
    _require(definition.get('coordinate_system') == 'ros_rh_m', 'foliage must use ROS metres')
    _require(definition['source_level_sha256'] == source['source_sha256'] == descriptor['source_sha256'],
             'foliage source level identity differs')
    _require(definition.get('bounds_m') == {'min': [0, -2048], 'max': [2048, 0]},
             'foliage coverage differs from the calibrated source core')
    transform = definition['weight_world_uv']
    for name in ('u', 'v'):
        _finite(transform[name], 3, 'invalid grass world-to-weight UV')
    _require(transform == {'u': [1.0/204800, 0, 0], 'v': [0, -1.0/204800, 1]},
             'grass weight UV differs from the source negative-V core')
    weights, models, layers = {}, {}, {}
    for item in definition['weight_textures']:
        identity = item['id']
        _require(re.fullmatch('[A-Za-z0-9_]+', identity) and identity not in weights, 'invalid grass weight identity')
        filename = _file(base, item['file'], item['sha256'])
        dimensions, color_type = _png_dimensions(filename)
        _require(dimensions == [item['width'], item['height']] == [4096, 4096] and color_type == 6 and
                 item['channels'] == 4 and item['srgb'] is False and item['address'] == 'clamp',
                 'grass weights require unscaled linear 4096-square RGBA')
        weights[identity] = dict(item, path=filename)
    for item in definition['models']:
        identity = item['id']
        _require(re.fullmatch('[A-Za-z0-9_]+', identity) and identity not in models, 'invalid grass mesh identity')
        filename = _file(base, item['file'], item['sha256'])
        _require(filename.suffix.lower() == '.fbx' and isinstance(item['triangles'], int) and item['triangles'] > 0,
                 'grass model needs a source FBX and triangle count')
        _require(item.get('fbx_import_scale', 1) == 1, 'grass FBX carries metre units; import scale must be one')
        material = item['material']
        _require(material['shader'] == 'random_grass' or (enhanced and
                 material['shader'] == 'ue_small_grass_card' and
                 item.get('geometry_adapter', {}).get('schema') == 'wt_ue_grass_crossed_card_v1'),
                 'unsupported source grass shader or unbound card adaptation')
        mask = _file(base, material['texture'], material['texture_sha256'])
        alpha = _file(base, material['alpha_texture'], material['alpha_texture_sha256'])
        _require(_png_dimensions(mask)[0] == _png_dimensions(alpha)[0], 'grass masks/alpha dimensions differ')
        bounds = item['bounds_m']
        for edge in ('lo', 'hi'):
            _finite(bounds[edge], 3, 'invalid source grass bounds')
        _require(all(bounds['lo'][i] <= bounds['hi'][i] for i in range(3)), 'inverted source grass bounds')
        normal = None
        if material.get('normal_texture'):
            normal = _file(base, material['normal_texture'], material['normal_texture_sha256'])
            dimensions, color_type = _png_dimensions(normal)
            _require(dimensions == material['normal_size'] == [512, 512] and color_type == 6 and
                     material.get('source_gamma_equal_one') is True and
                     material['normal_semantics']['x'] == 'A' and material['normal_semantics']['y'] == 'G' and
                     material['normal_semantics']['translucency'] == 'B' and
                     material['normal_semantics']['smoothness'] == 'R',
                     'source small-grass normal/illumination channels differ')
            _require(item.get('geometry_adapter', {}).get('version') == 2 and
                     material.get('texture_address') == 'clamp', 'source-lit grass requires the varied card adapter')
        models[identity] = dict(item, path=filename, mask_path=mask, alpha_path=alpha, normal_path=normal)
    for item in definition['layers']:
        identity = item['id']
        _require(re.fullmatch('[A-Za-z0-9_]+', identity) and identity not in layers, 'invalid grass layer identity')
        _require(item['model_id'] in models and item['weight_texture'] in weights and
                 item['weight_channel'] in range(4), 'grass layer references a missing source resource')
        density = item['density_per_100m2']
        _require(isinstance(density, (int, float)) and math.isfinite(density) and 0 < density <= 100000,
                 'invalid source grass density')
        for axis in ('x', 'y', 'z'):
            values = item['scale_'+axis]
            _finite(values, 2, 'invalid grass scale range')
            _require(0 < values[0] <= values[1] <= 100, 'invalid grass scale range')
        _require(item['collision'] is False and 0 <= item['start_cull_distance_cm'] < item['end_cull_distance_cm'] <= 100000,
                 'invalid grass cull distance or collision contract')
        for channel in ('r', 'g', 'b'):
            for endpoint in ('min', 'max'):
                values = item['color_masks'][channel][endpoint]
                _finite(values, 4, 'invalid original grass colour')
                _require(all(0 <= v <= 1 for v in values), 'original grass colour exceeds normalized channel range')
        layers[identity] = item
    _require(0 < len(weights) <= 4 and 0 < len(models) <= 32 and 0 < len(layers) <= 32,
             'grass resource count exceeds the native output contract')
    correction = definition['color_correction_multiplier']
    threshold = definition['alpha_clip_value']
    _require(isinstance(correction, (int, float)) and math.isfinite(correction) and 0 < correction <= 16,
             'invalid source grass colour correction')
    _require(isinstance(threshold, (int, float)) and math.isfinite(threshold) and 0 < threshold < 1,
             'invalid source grass alpha threshold')
    land = definition['land_color']
    land_path = _file(base, land['texture'], land['texture_sha256'])
    visual = definition.get('visual_adapter')
    if visual:
        _require(visual['schema'] == 'wt_grass_visual_adapter_v2' and
                 visual['source_gamma_conversion_count'] == 1 and
                 visual['minimum_normal_up_component'] == .20 and
                 sum(model['normal_path'] is not None for model in models.values()) == 3,
                 'source-lit grass visual adapter differs')
    proof = None
    if enhanced:
        spec = definition['enhancement_proof']
        proof_path = _file(base, spec['file'], spec['sha256'])
        proof = _read(proof_path)
        _require(proof.get('schema') == 'wt_foliage_enhancement_proof_v1' and proof.get('status') == 'passed',
                 'enhancement requires passed source-bound preparation')
        _require(proof['source_level_sha256'] == definition['source_level_sha256'] and
                 proof['original_foliage_sha256'] == _sha(package/'foliage/foliage.json'),
                 'original foliage source changed after enhancement preparation')
        _require((proof['total_models'], proof['total_layers'], proof['weight_texture_count']) ==
                 (len(models), len(layers), len(weights)), 'enhancement resource inventory differs')
        _require(proof['weight_textures'] == definition['weight_textures'] and
                 {x['id'] for x in proof['layers']} == set(layers) and
                 all(x['hard_overlap_pixels'] == 0 for x in proof['layers']),
                 'enhancement weights or hard footprint exclusion proof differs')
        for mask in proof['masks'].values():
            _file(base, mask['file'], mask['sha256'])
        repo = package.parents[2]
        for relative, digest in proof['source_inputs'].items():
            _file(repo, relative, digest)
    return dict(package=package, base=base, definition=definition, definition_path=path,
                definition_sha256=_sha(path), weights=weights, models=models, layers=layers,
                land_path=land_path, map_id=descriptor['id'], enhancement_proof=proof)


def _asset(unreal, path):
    return unreal.load_asset(path) if unreal.EditorAssetLibrary.does_asset_exist(path) else None


def _texture_asset(unreal, filename, folder, name, srgb, weights=False, clamp=False):
    from import_vehicle_assets import import_asset
    asset_path = folder + '/' + name
    texture = _asset(unreal, asset_path)
    digest = _sha(filename)
    if texture is None or unreal.EditorAssetLibrary.get_metadata_tag(texture, 'RMSimGrassSourceSHA256') != digest:
        results = import_asset(filename, folder, name)
        texture = next((obj for obj in results if isinstance(obj, unreal.Texture2D)), None)
    _require(isinstance(texture, unreal.Texture2D), 'grass texture import failed')
    settings = dict(srgb=srgb, virtual_texture_streaming=False, lod_bias=0, max_texture_size=0,
                    compression_settings=unreal.TextureCompressionSettings.TC_DEFAULT if srgb else
                                         unreal.TextureCompressionSettings.TC_VECTOR_DISPLACEMENTMAP)
    if weights:
        settings.update(mip_gen_settings=unreal.TextureMipGenSettings.TMGS_NO_MIPMAPS, never_stream=True,
                        address_x=unreal.TextureAddress.TA_CLAMP, address_y=unreal.TextureAddress.TA_CLAMP,
                        filter=unreal.TextureFilter.TF_BILINEAR)
    else:
        settings.update(address_x=unreal.TextureAddress.TA_CLAMP if clamp else unreal.TextureAddress.TA_WRAP,
                        address_y=unreal.TextureAddress.TA_CLAMP if clamp else unreal.TextureAddress.TA_WRAP)
    for key, value in settings.items():
        texture.set_editor_property(key, value)
    unreal.EditorAssetLibrary.set_metadata_tag(texture, 'RMSimGrassSourceSHA256', digest)
    _require(unreal.EditorAssetLibrary.save_loaded_asset(texture), 'grass texture save failed')
    return texture


def _custom(unreal, material, code, inputs, output_type, extra=(), defer_notifications=False):
    edit = unreal.MaterialEditingLibrary
    # Weight outputs have no extra socket cache. Build their graph without
    # compiling every intermediate property; _save_parent compiles the final graph.
    # Additional output sockets require one real RebuildOutputs notification.
    # Other edits are quiet; the complete material is compiled by _save_parent.
    notify = {'notify_mode': unreal.PropertyAccessChangeNotifyMode.NEVER} if defer_notifications else {}
    node = edit.create_material_expression(material, unreal.MaterialExpressionCustom)
    node.set_editor_property('desc', NODE_PREFIX+'weight or surface', **notify)
    node.set_editor_property('code', code, **notify)
    node.set_editor_property('output_type', output_type, **notify)
    descriptors = []
    for name in inputs:
        descriptor = unreal.CustomInput()
        descriptor.set_editor_property('input_name', name)
        descriptors.append(descriptor)
    node.set_editor_property('inputs', descriptors, **notify)
    outputs = []
    for name, kind in extra:
        descriptor = unreal.CustomOutput()
        descriptor.set_editor_property('output_name', name)
        descriptor.set_editor_property('output_type', kind)
        outputs.append(descriptor)
    if outputs:
        node.set_editor_property('additional_outputs', outputs)
    for name, (source, output) in inputs.items():
        _require(edit.connect_material_expressions(source, output, node, name), 'cannot bind grass shader input '+name)
    return node


def install_grass(package, folder, terrain=None):
    checked = validate_foliage(package)
    import unreal
    from import_vehicle_assets import import_asset
    from import_wt_scene import fbx_options, bounds_record
    from wt_materials import _clear_graph, _save_parent, _wire
    edit, prop = unreal.MaterialEditingLibrary, unreal.MaterialProperty
    quiet = {'notify_mode': unreal.PropertyAccessChangeNotifyMode.NEVER}
    def _expr(material, cls, **values):
        expression = edit.create_material_expression(material, cls)
        for key, value in values.items():
            expression.set_editor_property(key, value, **quiet)
        return expression
    def _texture(material, name, texture, color):
        return _expr(material, unreal.MaterialExpressionTextureObjectParameter, parameter_name=name,
            texture=texture, sampler_type=unreal.MaterialSamplerType.SAMPLERTYPE_COLOR if color else
            unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR)
    def _vector(material, name, value):
        return _expr(material, unreal.MaterialExpressionVectorParameter, parameter_name=name,
            default_value=unreal.LinearColor(*source_color_linear(value)))
    def _scalar(material, name, value):
        return _expr(material, unreal.MaterialExpressionScalarParameter, parameter_name=name, default_value=value)
    _require(folder == '/Game/RMSim/Maps/'+checked['map_id'], 'grass destination differs from source map')
    root = folder + '/Foliage'
    definition = checked['definition']
    terrain = terrain or _asset(unreal, folder+'/Materials/WT_Terrain')
    _require(isinstance(terrain, unreal.Material), 'grass output needs the original terrain parent')
    mask_textures, alpha_textures, normal_textures, weight_textures = {}, {}, {}, {}
    for name, model in checked['models'].items():
        clamp = model['material'].get('texture_address') == 'clamp'
        mask_textures[name] = _texture_asset(unreal, model['mask_path'], root+'/Textures', name+'_Masks', False, clamp=clamp)
        alpha_textures[name] = _texture_asset(unreal, model['alpha_path'], root+'/Textures', name+'_Alpha', False, clamp=clamp)
        if model['normal_path'] is not None:
            normal_textures[name] = _texture_asset(unreal, model['normal_path'], root+'/Textures', name+'_PackedNormal', False, clamp=True)
    for name, weight in checked['weights'].items():
        weight_textures[name] = _texture_asset(unreal, weight['path'], root+'/Textures', name, False, True)
    land_texture = _texture_asset(unreal, checked['land_path'], root+'/Textures', 'WT_GrassLandColor', True)
    parent = _asset(unreal, root+'/Materials/WT_Grass') or unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        'WT_Grass', root+'/Materials', unreal.Material, unreal.MaterialFactoryNew())
    _clear_graph(parent)
    parent.set_editor_property('blend_mode', unreal.BlendMode.BLEND_MASKED, **quiet)
    parent.set_editor_property('two_sided', True, **quiet)
    foliage_models = [getattr(unreal.MaterialShadingModel, name) for name in dir(unreal.MaterialShadingModel)
                      if name.endswith('_FOLIAGE')]
    _require(len(foliage_models) == 1, 'native enum must expose one two-sided foliage shading model')
    parent.set_editor_property('shading_model', foliage_models[0], **quiet)
    parent.set_editor_property('opacity_mask_clip_value', definition['alpha_clip_value'], **quiet)
    parent.set_editor_property('tangent_space_normal', False, **quiet)
    first = next(iter(checked['models']))
    normal_default = next(iter(normal_textures.values())) if normal_textures else mask_textures[first]
    inputs = {
        'Masks': (_texture(parent, 'Masks', mask_textures[first], False), ''),
        'Alpha': (_texture(parent, 'Alpha', alpha_textures[first], False), ''),
        'Land': (_texture(parent, 'Land', land_texture, True), ''),
        'UV': (_expr(parent, unreal.MaterialExpressionTextureCoordinate), ''),
        'World': (_expr(parent, unreal.MaterialExpressionWorldPosition), ''),
        'Random': (_expr(parent, unreal.MaterialExpressionPerInstanceRandom), ''),
        'Fade': (_expr(parent, unreal.MaterialExpressionPerInstanceFadeAmount), ''),
        'Correction': (_scalar(parent, 'Correction', definition['color_correction_multiplier']), ''),
        'PackedNormal': (_texture(parent, 'PackedNormal', normal_default, False), ''),
        'UseSourceNormal': (_scalar(parent, 'UseSourceNormal', 0), ''),
        'DefaultRoughness': (_scalar(parent, 'Roughness', .9), ''),
        'DefaultTransmission': (_scalar(parent, 'Transmission', .35), ''),
    }
    first_layer = next(iter(checked['layers'].values()))
    for channel in ('r', 'g', 'b'):
        for endpoint in ('min', 'max'):
            name = channel.upper()+endpoint.title()
            inputs[name] = (_vector(parent, name, first_layer['color_masks'][channel][endpoint]), '')
    surface = _custom(unreal, parent, GRASS_CODE, inputs, unreal.CustomMaterialOutputType.CMOT_FLOAT3,
                      [('OpacityOut', unreal.CustomMaterialOutputType.CMOT_FLOAT1),
                       ('NormalOut', unreal.CustomMaterialOutputType.CMOT_FLOAT3),
                       ('TransmissionOut', unreal.CustomMaterialOutputType.CMOT_FLOAT1),
                       ('RoughnessOut', unreal.CustomMaterialOutputType.CMOT_FLOAT1)], defer_notifications=True)
    _require(edit.connect_material_property(surface, '', prop.MP_BASE_COLOR), 'grass base colour connection failed')
    _require(edit.connect_material_property(surface, 'OpacityOut', prop.MP_OPACITY_MASK), 'grass opacity connection failed')
    subsurface = _expr(parent, unreal.MaterialExpressionMultiply)
    _wire(surface, '', subsurface, 'A')
    _wire(surface, 'TransmissionOut', subsurface, 'B')
    edit.connect_material_property(subsurface, '', prop.MP_SUBSURFACE_COLOR)
    edit.connect_material_property(surface, 'RoughnessOut', prop.MP_ROUGHNESS)
    edit.connect_material_property(_scalar(parent, 'Specular', .15), '', prop.MP_SPECULAR)
    normal_world = _expr(parent, unreal.MaterialExpressionTransform,
        transform_source_type=unreal.MaterialVectorCoordTransformSource.TRANSFORMSOURCE_TANGENT,
        transform_type=unreal.MaterialVectorCoordTransform.TRANSFORM_WORLD)
    _wire(surface, 'NormalOut', normal_world, '')
    up = _expr(parent, unreal.MaterialExpressionConstant3Vector, constant=unreal.LinearColor(0,0,1,0))
    instance_up = _expr(parent, unreal.MaterialExpressionTransform,
        transform_source_type=unreal.MaterialVectorCoordTransformSource.TRANSFORMSOURCE_INSTANCE,
        transform_type=unreal.MaterialVectorCoordTransform.TRANSFORM_WORLD)
    _wire(up, '', instance_up, '')
    normal = _custom(unreal, parent, GRASS_NORMAL_CODE, {
        'NormalWorld': (normal_world, ''), 'InstanceUp': (instance_up, ''),
        'Side': (_expr(parent, unreal.MaterialExpressionTwoSidedSign), ''),
        'UseSourceNormal': inputs['UseSourceNormal'],
        'MinimumUp': (_scalar(parent, 'MinimumNormalUp', .20), '')},
        unreal.CustomMaterialOutputType.CMOT_FLOAT3, defer_notifications=True)
    _require(edit.connect_material_property(normal, '', prop.MP_NORMAL), 'grass world normal connection failed')
    edit.set_material_usage(parent, unreal.MaterialUsage.MATUSAGE_INSTANCED_STATIC_MESHES)
    _save_parent(parent)
    materials = {}
    for name, layer in checked['layers'].items():
        material = _asset(unreal, root+'/Materials/'+name) or unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            name, root+'/Materials', unreal.MaterialInstanceConstant, unreal.MaterialInstanceConstantFactoryNew())
        edit.set_material_instance_parent(material, parent)
        edit.set_material_instance_texture_parameter_value(material, 'Masks', mask_textures[layer['model_id']])
        edit.set_material_instance_texture_parameter_value(material, 'Alpha', alpha_textures[layer['model_id']])
        has_source_normal = layer['model_id'] in normal_textures
        edit.set_material_instance_scalar_parameter_value(material, 'UseSourceNormal', 1 if has_source_normal else 0)
        edit.set_material_instance_texture_parameter_value(material, 'PackedNormal',
            normal_textures[layer['model_id']] if has_source_normal else normal_default)
        for channel in ('r', 'g', 'b'):
            for endpoint in ('min', 'max'):
                edit.set_material_instance_vector_parameter_value(material, channel.upper()+endpoint.title(),
                    unreal.LinearColor(*source_color_linear(layer['color_masks'][channel][endpoint])))
        edit.update_material_instance(material)
        _require(unreal.EditorAssetLibrary.save_loaded_asset(material), 'grass layer material save failed')
        materials[name] = material
    meshes, mesh_reports = {}, []
    for name, model in checked['models'].items():
        mesh = _asset(unreal, root+'/Meshes/'+name)
        if mesh is None or unreal.EditorAssetLibrary.get_metadata_tag(mesh, 'RMSimGrassSourceSHA256') != model['sha256']:
            imported = import_asset(model['path'], root+'/Meshes', name, fbx_options())
            mesh = next((obj for obj in imported if isinstance(obj, unreal.StaticMesh)), None)
        _require(isinstance(mesh, unreal.StaticMesh), 'source grass mesh import failed: '+name)
        _require(len(mesh.get_editor_property('static_materials')) == 1, 'grass mesh must retain one source material slot')
        first_material = next(materials[key] for key, layer in checked['layers'].items() if layer['model_id'] == name)
        mesh.set_material(0, first_material)
        unreal.EditorAssetLibrary.set_metadata_tag(mesh, 'RMSimGrassSourceSHA256', model['sha256'])
        _require(unreal.EditorAssetLibrary.save_loaded_asset(mesh), 'grass mesh save failed')
        native = json.loads(unreal.MapImportLibrary.describe_map_mesh(mesh))
        _require(int(native['triangles']) == model['triangles'], 'grass source triangle count differs: '+name)
        bounds = bounds_record(mesh, model)
        mesh_reports.append(dict(id=name, asset=mesh.get_path_name(), source_sha256=model['sha256'],
                                 triangles=native['triangles'], bounds=bounds))
        meshes[name] = mesh
    grass_types, type_reports = {}, []
    for name, layer in checked['layers'].items():
        asset_name = 'WT_'+name
        grass = _asset(unreal, root+'/Types/'+asset_name) or unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            asset_name, root+'/Types', unreal.LandscapeGrassType, unreal.LandscapeGrassTypeFactory())
        variety = unreal.GrassVariety()
        values = dict(grass_mesh=meshes[layer['model_id']], override_materials=[materials[name]],
            grass_density=unreal.PerPlatformFloat(default=layer['density_per_100m2']),
            grass_density_quality=unreal.PerQualityLevelFloat(default=layer['density_per_100m2']),
            start_cull_distance=unreal.PerPlatformInt(default=int(round(layer['start_cull_distance_cm']))),
            end_cull_distance=unreal.PerPlatformInt(default=int(round(layer['end_cull_distance_cm']))),
            start_cull_distance_quality=unreal.PerQualityLevelInt(default=int(round(layer['start_cull_distance_cm']))),
            end_cull_distance_quality=unreal.PerQualityLevelInt(default=int(round(layer['end_cull_distance_cm']))),
            scaling=unreal.GrassScaling.LOCK_XY, use_grid=False, random_rotation=True, align_to_surface=True,
            cast_dynamic_shadow=False, cast_contact_shadow=True, receives_decals=False,
            affect_distance_field_lighting=False, keep_instance_buffer_cpu_copy=False)
        for axis in ('x', 'y', 'z'):
            values['scale_'+axis] = unreal.FloatInterval(min=layer['scale_'+axis][0], max=layer['scale_'+axis][1])
        for key, value in values.items():
            variety.set_editor_property(key, value)
        grass.set_editor_property('grass_varieties', [variety])
        grass.set_editor_property('enable_density_scaling', True)
        _require(unreal.EditorAssetLibrary.save_loaded_asset(grass), 'grass type save failed')
        grass_types[name] = grass
        type_reports.append(dict(id=name, asset=grass.get_path_name(), model_id=layer['model_id'],
            density_per_100m2=layer['density_per_100m2'], weight_texture=layer['weight_texture'],
            weight_channel=layer['weight_channel'], start_cull_distance_cm=layer['start_cull_distance_cm'],
            end_cull_distance_cm=layer['end_cull_distance_cm'], collision=False))
    # Delete only this adapter's nodes; the source terrain and RVT graph stay intact.
    for expression in list(edit.get_material_expressions(terrain)):
        if str(expression.get_editor_property('desc')).startswith(NODE_PREFIX):
            edit.delete_material_expression(terrain, expression)
    _require(not any(isinstance(node, unreal.MaterialExpressionLandscapeGrassOutput)
                     for node in edit.get_material_expressions(terrain)), 'unexpected pre-existing landscape grass output')
    def node(cls, **values):
        expression = edit.create_material_expression(terrain, cls)
        for key, value in values.items():
            expression.set_editor_property(key, value, notify_mode=unreal.PropertyAccessChangeNotifyMode.NEVER)
        expression.set_editor_property('desc', NODE_PREFIX+cls.__name__,
                                       notify_mode=unreal.PropertyAccessChangeNotifyMode.NEVER)
        return expression
    world = node(unreal.MaterialExpressionWorldPosition)
    output = node(unreal.MaterialExpressionLandscapeGrassOutput)
    grass_inputs = []
    for name in checked['layers']:
        grass_input = unreal.GrassInput()
        grass_input.set_editor_property('name', name)
        grass_input.set_editor_property('grass_type', grass_types[name])
        grass_inputs.append(grass_input)
    output.set_editor_property('grass_types', grass_inputs, notify_mode=unreal.PropertyAccessChangeNotifyMode.NEVER)
    weight_nodes = {}
    uv = definition['weight_world_uv']
    for name, texture in weight_textures.items():
        texture_node = node(unreal.MaterialExpressionTextureObjectParameter, parameter_name='WT_GrassWeight_'+name,
            texture=texture, sampler_type=unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR)
        code = ('float2 uv=float2('+f'{uv["u"][0]:.16g}*World.x+{uv["u"][1]:.16g}*World.y+{uv["u"][2]:.16g},'+
                f'{uv["v"][0]:.16g}*World.x+{uv["v"][1]:.16g}*World.y+{uv["v"][2]:.16g});'+
                '\nfloat inside=(uv.x>=0&&uv.x<=1&&uv.y>=0&&uv.y<=1)?1:0;'+
                '\nreturn Texture2DSampleLevel(Weight,WeightSampler,uv,0)*inside;')
        weight_nodes[name] = _custom(unreal, terrain, code, {'Weight': (texture_node, ''), 'World': (world, '')},
                                    unreal.CustomMaterialOutputType.CMOT_FLOAT4, defer_notifications=True)
    for name, layer in checked['layers'].items():
        channel = layer['weight_channel']
        mask = node(unreal.MaterialExpressionComponentMask, r=channel == 0, g=channel == 1, b=channel == 2, a=channel == 3)
        _wire(weight_nodes[layer['weight_texture']], '', mask, '')
        _wire(mask, '', output, name)
    _save_parent(terrain)
    report = dict(schema='wt_grass_native_import_v1', status='authored_pending_weight_bake', version=VERSION,
        source_level_sha256=definition['source_level_sha256'], definition_sha256=checked['definition_sha256'],
        definition_file=checked['definition_path'].relative_to(checked['package']).as_posix(),
        enhancement_proof_sha256=definition.get('enhancement_proof', {}).get('sha256'),
        importer_sha256=_sha(__file__), source_models=mesh_reports, grass_types=type_reports,
        weight_assets={name: texture.get_path_name() for name, texture in weight_textures.items()},
        terrain=terrain.get_path_name(), grass_parent=parent.get_path_name(),
        allocation='native Landscape Grass streams clusters around rendered views',
        cull_distance_conversion='source distance rounded to nearest UE centimetre',
        source_collision=False, wind_animation=False, instance_distribution='UE Halton with deterministic per-instance variation',
        layer_colour_formula='source RGB masks and T0_a.r alpha; pow(source RGB endpoints,2.2), original alpha retained',
        visual_adapter=definition.get('visual_adapter'),
        source_normal_assets={name: texture.get_path_name() for name, texture in normal_textures.items()},
        source_normal_channels='AG normal XY, B translucency, R smoothness; source linear DDSx GAMMA_EQ_1',
        source_colour_contract='gpuGrass.cpp srgb_color4; grass_generate.dshl:817-819; fast_grass.cpp:44-51/140-145',
        limitations=definition.get('limitations', []))
    target = checked['package']/'ue-grass-import-report.json'
    target.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    unreal.log('WT_GRASS_AUTHORED '+str(target))
    return report
