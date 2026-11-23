"""UE Editor: source-backed War Thunder material adapters for the Flanders scene.

create_materials(package_directory, ue_folder) -> (id_to_material, terrain_material).
The source shader is preserved in the report. These adapters preserve textures, UV
channels and PBR channels, but do not claim parity with Dagor's renderer/lighting.
"""
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import zlib

import unreal
from import_vehicle_assets import import_asset
from wt_vertex_opacity import HEIGHT_DECAL_SHADER, validate_scene_vertex_opacity
from wt_height_decals import (HEIGHT_DECAL_CODE, HEIGHT_SOURCE, HEIGHT_NORMAL_STRENGTH,
                              HEIGHT_NORMAL_METADATA_TAG, normal_adapter_metadata,
                              contract as height_surface_contract,
                              HEIGHT_REFLECTION_METADATA_TAG, reflection_metadata,
                              HEIGHT_POLARITY_METADATA_TAG, height_polarity_contract, height_polarity_metadata)
import wt_ground_microdetails as ground_microdetails
from wt_tree_alpha import tree_alpha_contract, SOURCE_AUDIT_SHA256 as TREE_ALPHA_AUDIT_SHA256
from wt_landclass import LANDCLASS_CODE, validate_landclass_surface, shader_vectors
from wt_rendinst_decals import flavor as rendinst_decal_flavor, contract as rendinst_decal_contract, FLAVORS as RENDINST_DECAL_FLAVORS, author_parent as author_rendinst_decal_parent, author_instance as author_rendinst_decal_instance
from wt_water import water_surface_contract, COLOR_RGBA as WATER_COLOR_RGBA, UE_ROUGHNESS as WATER_ROUGHNESS, UE_METALLIC as WATER_METALLIC, UE_SPECULAR as WATER_SPECULAR

EDIT = unreal.MaterialEditingLibrary
P = unreal.MaterialProperty
VERSION = 9


def _load_existing(path):
    # A missing optional asset is a creation path; LoadAsset would log an
    # engine error and make an otherwise successful commandlet exit nonzero.
    return unreal.load_asset(path) if unreal.EditorAssetLibrary.does_asset_exist(path) else None


def material_id(spec):
    value = {key: spec[key] for key in ('shader', 'parameters', 'texture_slots')}
    return 'WT_' + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:20]


def material_flavor(shader):
    decal = rendinst_decal_flavor(shader)
    if decal is not None:
        return decal
    if shader == 'water_aces':
        return 'Water'
    if shader == HEIGHT_DECAL_SHADER:
        return 'HeightDecal'
    if shader == 'land_mesh':
        return 'Land'
    return 'Leaves' if shader == 'rendinst_facing_leaves' else 'Glass' if 'glass' in shader else 'Decal' if 'decal' in shader else 'Surface'


def parameters(text):
    result = {}
    for line in text.splitlines():
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        try:
            result[key.strip()] = [float(item.strip()) for item in value.split(',')]
        except ValueError:
            continue
    return result


def height_blend_parameters(spec):
    params = parameters(spec['parameters'])
    def value(name, default):
        return params.get(name, [default])[0]
    if value('attenuation_from_heightmap_height', 0) != 0:
        raise ValueError('height-decal ground-height attenuation requires its original heightmap binding')
    threshold, gamma, above = (value('diffuse_threshold', .25), value('vertex_gamma', 1), height_polarity_contract(spec)['value'])
    if not (threshold > 0 and gamma > 0 and above in (0, 1)):
        raise ValueError('unsupported source height-decal blend parameters')
    return (threshold, gamma, above, 0)


def _png(path, rgba):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>2I5B', 1, 1, 8, 6, 0, 0, 0)) +
                     chunk(b'IDAT', zlib.compress(bytes([0, *rgba]))) + chunk(b'IEND', b''))


class Textures:
    def __init__(self, package, extraction, folder, landclass=None, water_source=None):
        self.package, self.folder = package, folder
        self.landclass = landclass
        self.water_source = water_source
        self.landclass_settings = {}
        paths = sorted(extraction.glob('textures-*/result.json'))
        if not paths:
            raise ValueError('no extracted texture inventory')
        report = json.loads(paths[-1].read_text(encoding='utf-8'))
        self.source = {item['name']: item for item in report['exported']}
        if landclass is not None:
            for entry in landclass['textures'].values():
                name = entry['name']
                if name in self.source and self.source[name]['sha256'] != entry['sha256']:
                    raise ValueError('landclass color differs from the original texture inventory: ' + name)
                self.source[name] = dict(entry, file=str(package/entry['file']))
                self.landclass_settings[name] = entry
        self.cache, self.imported = {}, []
        self.directory = package / 'textures'
        self.directory.mkdir(exist_ok=True)
        self.default_paths = {}
        for name, rgba in [('White', (255,255,255,255)), ('NormalAG', (0,128,0,128)), ('Black', (0,0,0,255)), ('MidGray', (128,128,128,255))]:
            path = self.directory / ('WT_Default' + name + '.png')
            if not path.exists():
                _png(path, rgba)
            self.default_paths[name] = path

    def get(self, name, color=True, default='White', nearest=False):
        name = name.split('*')[0].split('$')[0] if name else ''
        key = (name or '_Default' + default, color, nearest)
        if key in self.cache:
            return self.cache[key]
        if name:
            if name not in self.source:
                raise ValueError('required original texture absent: ' + name)
            entry = self.source[name]
            source = Path(entry['file'])
            if source.suffix.lower() != '.png':
                raise ValueError('float data texture cannot silently become color: ' + name)
            if hashlib.sha256(source.read_bytes()).hexdigest() != entry['sha256']:
                raise ValueError('texture hash mismatch: ' + name)
            path = self.directory / source.name
            if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
                shutil.copyfile(source, path)
        else:
            path = self.default_paths[default]
        asset_name = re.sub('[^A-Za-z0-9_]', '_', key[0]) + ('_C' if color else '_D') + ('_Point' if nearest else '')
        asset_path = f'{self.folder}/Textures/{asset_name}'
        texture = _load_existing(asset_path)
        if texture is None:
            imported = import_asset(path, self.folder + '/Textures', asset_name)
            texture = next((obj for obj in imported if isinstance(obj, unreal.Texture2D)), None)
        if texture is None:
            raise RuntimeError('texture import failed: ' + str(path))
        texture.set_editor_property('srgb', color)
        texture.set_editor_property('compression_settings', unreal.TextureCompressionSettings.TC_DEFAULT if color else unreal.TextureCompressionSettings.TC_VECTOR_DISPLACEMENTMAP)
        if nearest:
            texture.set_editor_property('filter', unreal.TextureFilter.TF_NEAREST)
        if name in self.landclass_settings:
            settings = self.landclass_settings[name]
            if settings['srgb'] != color or settings['nearest'] != nearest:
                raise ValueError('landclass texture sampling contract differs: ' + name)
            address = unreal.TextureAddress.TA_CLAMP if settings['address']=='clamp' else unreal.TextureAddress.TA_WRAP
            texture.set_editor_property('address_x', address)
            texture.set_editor_property('address_y', address)
            if settings['no_mips']:
                texture.set_editor_property('mip_gen_settings', unreal.TextureMipGenSettings.TMGS_NO_MIPMAPS)
                texture.set_editor_property('never_stream', True)
                texture.set_editor_property('lod_bias', 0)
                texture.set_editor_property('max_texture_size', 0)
                texture.set_editor_property('filter', unreal.TextureFilter.TF_NEAREST if nearest else unreal.TextureFilter.TF_BILINEAR)
        unreal.EditorAssetLibrary.save_loaded_asset(texture)
        self.cache[key] = texture
        self.imported.append(dict(name=name or default, asset=texture.get_path_name(), file=str(path.relative_to(self.package)), srgb=color, nearest=nearest))
        return texture


def _clear_graph(material, keep=()):
    # UE 5.8 DeleteAllMaterialExpressions iterates the live expression array
    # while DeleteMaterialExpression removes from it. Snapshot first so a
    # rebuild cannot retain disconnected old nodes or duplicate parameters.
    keep_paths={node.get_path_name() for node in keep}
    for expression in list(EDIT.get_material_expressions(material)):
        if expression.get_path_name() not in keep_paths:
            EDIT.delete_material_expression(material, expression)
    if EDIT.get_num_material_expressions(material) != len(keep_paths):
        raise RuntimeError('material graph was not fully cleared: ' + material.get_path_name())


def _save_parent(material):
    errors = EDIT.recompile_material(material)
    if errors is None or isinstance(errors, (str, bool)):
        raise RuntimeError('UE 5.8 RecompileMaterial must return Array[str]')
    if errors:
        raise RuntimeError('completed material graph compilation failed: ' +
                           material.get_path_name() + ': ' + '; '.join(str(error) for error in errors))
    if not unreal.EditorAssetLibrary.save_loaded_asset(material):
        raise RuntimeError('failed to save material parent: ' + material.get_path_name())


def _expr(material, cls, **values):
    node = EDIT.create_material_expression(material, cls)
    for name, value in values.items():
        node.set_editor_property(name, value)
    return node


def _wire(source, output, target, socket):
    if not EDIT.connect_material_expressions(source, output, target, socket):
        names = [str(name) for name in EDIT.get_material_expression_input_names(target)]
        raise RuntimeError(f'cannot connect {source.get_name()}.{output} to {target.get_name()}.{socket}; target inputs={names}')


def _prop(node, output, prop):
    if not EDIT.connect_material_property(node, output, prop):
        raise RuntimeError('cannot bind material output ' + output)


def _scalar(material, name, value):
    return _expr(material, unreal.MaterialExpressionScalarParameter, parameter_name=name, default_value=value)


def _vector(material, name, value):
    return _expr(material, unreal.MaterialExpressionVectorParameter, parameter_name=name, default_value=unreal.LinearColor(*value))


def _texture(material, name, texture, color):
    return _expr(material, unreal.MaterialExpressionTextureObjectParameter, parameter_name=name, texture=texture,
                 sampler_type=unreal.MaterialSamplerType.SAMPLERTYPE_COLOR if color else unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR)


def _custom(material, code, inputs, terrain=False, bind_base_color=True, additional_outputs=()):
    outputs = [('NormalOut', unreal.CustomMaterialOutputType.CMOT_FLOAT3),
               ('RoughnessOut', unreal.CustomMaterialOutputType.CMOT_FLOAT1),
               ('MetallicOut', unreal.CustomMaterialOutputType.CMOT_FLOAT1),
               ('OpacityOut', unreal.CustomMaterialOutputType.CMOT_FLOAT1)]
    outputs.extend(additional_outputs)
    extra = []
    for name, kind in outputs:
        output = unreal.CustomOutput()
        output.set_editor_property('output_name', name)
        output.set_editor_property('output_type', kind)
        extra.append(output)
    descriptors = []
    for name in inputs:
        field = unreal.CustomInput()
        field.set_editor_property('input_name', name)
        descriptors.append(field)
    node = _expr(material, unreal.MaterialExpressionCustom, code=code,
                 output_type=unreal.CustomMaterialOutputType.CMOT_FLOAT3,
                 inputs=descriptors, additional_outputs=extra,
                 description='Source texture adapter; renderer parity limitations in material-import-report.json')
    for name, (expression, output) in inputs.items():
        _wire(expression, output, node, name)
    if bind_base_color:
        _prop(node, '', P.MP_BASE_COLOR)
    _prop(node, 'NormalOut', P.MP_NORMAL)
    _prop(node, 'RoughnessOut', P.MP_ROUGHNESS)
    _prop(node, 'MetallicOut', P.MP_METALLIC)
    return node


OBJECT_CODE = r'''
struct WTUtil {
 float3 normalAG(float4 v) { float2 xy=v.ag*2-1; return normalize(float3(xy,sqrt(saturate(1-dot(xy,xy))))); }
 float mixWeight(float a,float b,float mask) {
   float2 w=float2(a*(1-mask),b*mask+0.000001);w*=w;w*=w;
   float t=w.y/max(w.x+w.y,0.000001);return saturate(-2.666666667*t*t+4*t*t-0.333333333*t);
 }
};
WTUtil util;
float4 c=Texture2DSample(T0,T0Sampler,UV0);
float4 n=Texture2DSample(T2,T2Sampler,UV0);
float3 result=c.rgb;
float3 normal=util.normalAG(n);
float rough=saturate(1-n.r),metal=n.b;
if (Family>0.5 && Family<1.5) {
 float2 maskuv=UV1*Mask.xy;
 float4 overlay=Texture2DSample(T0,T0Sampler,UV1);
 float4 masktex=Texture2DSample(T1,T1Sampler,maskuv);
 float4 d1=Texture2DSample(T3,T3Sampler,UV2*Tiles.xy);
 float4 d2=Texture2DSample(T5,T5Sampler,UV2*Tiles.zw);
 float4 n1=Texture2DSample(T4,T4Sampler,UV2*Tiles.xy);
 float4 n2=Texture2DSample(T6,T6Sampler,UV2*Tiles.zw);
 float weight=util.mixWeight(d1.a,d2.a,pow(saturate(masktex.g),max(Mask.z,0.001))*overlay.a);
 result=overlay.rgb*lerp(d1.rgb,d2.rgb,weight)*4.59479342;
 normal=normalize(float3(normal.xy+lerp(util.normalAG(n1).xy,util.normalAG(n2).xy,weight),normal.z));
 rough=saturate(1-lerp(n1.r,n2.r,weight));metal=lerp(n1.b,n2.b,weight);
} else if (Family>1.5 && Family<2.5) {
 float4 d0=Texture2DSample(T0,T0Sampler,UV0*Tiles.x);
 float4 d1=Texture2DSample(T3,T3Sampler,UV0*Tiles.y);
 float4 d2=Texture2DSample(T5,T5Sampler,UV0*Tiles.z);
 float4 n0=Texture2DSample(T2,T2Sampler,UV0*Tiles.x);
 float4 n1=Texture2DSample(T4,T4Sampler,UV0*Tiles.y);
 float4 n2=Texture2DSample(T6,T6Sampler,UV0*Tiles.z);
 // Retain source layers and height weights; Dagor spatial Perlin modulation is not reproduced.
 float3 weights=pow(max(float3(d0.a,d1.a,d2.a)*LayerWeights.rgb,0.0001),4);
 weights/=dot(weights,1);
 result=d0.rgb*weights.x+d1.rgb*weights.y+d2.rgb*weights.z;
 normal=normalize(util.normalAG(n0)*weights.x+util.normalAG(n1)*weights.y+util.normalAG(n2)*weights.z);
 rough=saturate(1-dot(float3(n0.r,n1.r,n2.r),weights));metal=dot(float3(n0.b,n1.b,n2.b),weights);
} else if (Family>2.5 && Family<3.5) {
 float4 detail=Texture2DSample(T3,T3Sampler,UV2*Tiles.xy);
 result=c.rgb*detail.rgb*4.59479342;
} else if (Family>3.5) {
 // Decal proxy retains original authored surface and alpha, using surface translucency.
 result=c.rgb;
}
NormalOut=normal;RoughnessOut=clamp(rough,0.04,1);MetallicOut=saturate(metal);
float sourceAlpha=AlphaFromT1>0.5 ? Texture2DSample(T1,T1Sampler,UV0).r : c.a;
OpacityOut=AlphaEnabled>0.5 ? sourceAlpha : 1;
return max(result,0);
'''


# Height-decal code and source layout contract live in wt_height_decals.py.


def _leaf_custom(material, code, inputs, additional_outputs=()):
    """Vector expressions for the source half4 leaf data; no PBR output binding."""
    descriptors = []
    for name in inputs:
        field = unreal.CustomInput()
        field.set_editor_property('input_name', name)
        descriptors.append(field)
    extra = []
    for name in additional_outputs:
        output = unreal.CustomOutput()
        output.set_editor_property('output_name', name)
        output.set_editor_property('output_type', unreal.CustomMaterialOutputType.CMOT_FLOAT3)
        extra.append(output)
    node = _expr(material, unreal.MaterialExpressionCustom, code=code,
                 output_type=unreal.CustomMaterialOutputType.CMOT_FLOAT3,
                 inputs=descriptors, additional_outputs=extra,
                 description='Dagor facing leaves: original half4 offsets, camera-facing, no wind')
    for name, (expression, output) in inputs.items():
        _wire(expression, output, node, name)
    return node


def facing_leaves_wpo(material):
    """Replace the real reference card offset with the source no-wind basis.

    Only rendinst_facing_leaves uses this graph. UV3 = raw tc2.xy and UV4 =
    raw tc2.zw; offsets are metres and w is the retained wind group. Reference
    FBX positions have already expanded source pivot+(dx,dy,-dz), which becomes
    UE-local pivot+(dx,-dz,dy)*100. All affected source instances have positive,
    uniform scales; the instance X vector supplies that scale, including HISM.
    Dagor view_itm columns are right/up/forward (coreUtil/flyMode.cpp). Mapping
    camera forward/up through (x,z,y) reflects the right axis, so UE camera
    coordinates are (-dx,dy,-dz). Source identity camera becomes UE yaw+90;
    this graph produces exactly zero WPO for an unrotated reference card there.

    Dagor 9639942de5a8e5b01d91977f60ed82cda22d5f41:
      prog/engine/libFx/leavesWind.cpp: setShaderVars / setNoAnimShaderVars
      prog/gameLibs/bvh/shaders/bvh_process_leaves_vertices.dshl: tc2/basis
    The BVH-only sun-direction shadow displacement is excluded.
    """
    coordinates = _leaf_custom(material, r'''
ReferenceLocalCm = float3(DeltaXY.x, -DeltaZWind.x, DeltaXY.y) * 100.0;
// Dagor->UE (x,z,y) reflects handedness: UE camera-right = -C*source-right.
return float3(-DeltaXY.x, DeltaXY.y, -DeltaZWind.x) * 100.0;
''', {
        'DeltaXY': (_expr(material, unreal.MaterialExpressionTextureCoordinate, coordinate_index=3), ''),
        'DeltaZWind': (_expr(material, unreal.MaterialExpressionTextureCoordinate, coordinate_index=4), ''),
    }, additional_outputs=('ReferenceLocalCm',))

    def to_world(node, output, source):
        transform = _expr(material, unreal.MaterialExpressionTransform,
                          transform_source_type=source,
                          transform_type=unreal.MaterialVectorCoordTransform.TRANSFORM_WORLD)
        _wire(node, output, transform, '')
        return transform

    source_space = unreal.MaterialVectorCoordTransformSource
    desired_world = to_world(coordinates, '', source_space.TRANSFORMSOURCE_CAMERA)
    reference_world = to_world(coordinates, 'ReferenceLocalCm', source_space.TRANSFORMSOURCE_INSTANCE)
    unit_x = _expr(material, unreal.MaterialExpressionConstant3Vector,
                   constant=unreal.LinearColor(1.0, 0.0, 0.0, 0.0))
    instance_unit_x = to_world(unit_x, '', source_space.TRANSFORMSOURCE_INSTANCE)
    displacement = _leaf_custom(material,
        'return DesiredWorld * length(InstanceUnitX) - ReferenceWorld;', {
            'DesiredWorld': (desired_world, ''),
            'InstanceUnitX': (instance_unit_x, ''),
            'ReferenceWorld': (reference_world, ''),
        })
    _prop(displacement, '', P.MP_WORLD_POSITION_OFFSET)


def _ground_decal_rvt(textures):
    if not hasattr(textures, 'ground_decal_rvt'):
        from wt_runtime_virtual_texture import ensure_rvt
        textures.ground_decal_rvt = ensure_rvt(textures.folder)
    return textures.ground_decal_rvt


def _write_ground_decal_rvt(material, surface, normal_is_world=False, microdetail_pin=None):
    output = _expr(material, unreal.MaterialExpressionRuntimeVirtualTextureOutput)
    if normal_is_world:
        normal = _expr(material, unreal.MaterialExpressionReroute)
    else:
        normal = _expr(material, unreal.MaterialExpressionTransform,
            transform_source_type=unreal.MaterialVectorCoordTransformSource.TRANSFORMSOURCE_TANGENT,
            transform_type=unreal.MaterialVectorCoordTransform.TRANSFORM_WORLD)
    _wire(surface, 'NormalOut', normal, '')
    one = _expr(material, unreal.MaterialExpressionConstant, r=1.0)
    if microdetail_pin:
        payload = _expr(material, unreal.MaterialExpressionReroute)
        _wire(surface, microdetail_pin, payload, '')
    else:
        payload = _expr(material, unreal.MaterialExpressionConstant, r=0.0)
    # UE's RVT shader multiplies all raw attributes by Opacity exactly once.
    for node, pin, socket in [(surface, '', 'BaseColor'), (surface, 'RoughnessOut', 'Roughness'),
                              (normal, '', 'Normal'), (surface, 'OpacityOut', 'Opacity'),
                              (one, '', 'Mask'), (payload, '', 'Specular')]:
        if node == surface:
            value = _expr(material, unreal.MaterialExpressionReroute)
            _wire(node, pin, value, '')
            node, pin = value, ''
        _wire(node, pin, output, socket)
    return output


# UE5.8 VirtualTextureMaterial.usf writes premultiplied attributes. The YCoCg
# material layout clears its Mask/Normal/Roughness targets to 128/255, as defined
# by RuntimeVirtualTextureRender.cpp and FClearValueBinding::DefaultNormal8Bit.
RVT_COMPOSITE_CODE = r'''
const float clearValue=128.0/255.0;
float coverage=saturate((DecalMask-clearValue)/(1-clearValue));
float inside=step(BoundsMin.x,World.x)*step(BoundsMin.y,World.y)*step(World.x,BoundsMax.x)*step(World.y,BoundsMax.y);
coverage*=inside;
MetallicOut=BaseMetallic;OpacityOut=1;
NormalOut=BaseNormalWorld;RoughnessOut=BaseRoughness;
if(coverage<=0.0001) return BaseColor;
float invAlpha=1/max(coverage,0.0001);
float2 decalXY=(DecalNormalWorld.xy-(1-coverage)/127.0)*invAlpha;
// MaskYCoCg compression rounds packed Z to one sign bit; restore its length
// from the recovered XY, retaining the sign returned by the native sample.
float decalZ=sqrt(saturate(1-dot(decalXY,decalXY)))*(DecalNormalWorld.z<0 ? -1.0 : 1.0);
float3 decalNormal=normalize(float3(decalXY,decalZ));
// Dagor land_aces combines the heightfield normal with the clipmap normal by RNM.
float3 t=normalize(BaseGeometricNormal)+float3(0,0,1);
float3 u=decalNormal*float3(-1,-1,1);
float3 projectedNormal=normalize(t*(dot(t,u)/max(t.z,1.e-5))-u);
NormalOut=normalize(lerp(BaseNormalWorld,projectedNormal,coverage));
RoughnessOut=clamp(DecalRoughness+(BaseRoughness-clearValue)*(1-coverage),0.04,1);
return max(DecalColor+BaseColor*(1-coverage),0);
'''


def _read_ground_decal_rvt(material, surface, textures, base_normal_is_world=False):
    source = json.loads((textures.package/'scene-source.json').read_text(encoding='utf-8-sig'))
    decals = [mesh for mesh in source.get('meshes', []) if mesh['id']=='world_decal_000']
    if not decals:
        return surface  # The small material API fixture has no world geometry.
    bounds = decals[0]['bounds_m']
    rvt = _ground_decal_rvt(textures)
    sample = _expr(material, unreal.MaterialExpressionRuntimeVirtualTextureSample,
        virtual_texture=rvt, material_type=rvt.get_editor_property('material_type'),
        single_physical_space=rvt.get_editor_property('single_physical_space'),
        adaptive=rvt.get_editor_property('adaptive'), enable_feedback=True)
    world = _expr(material, unreal.MaterialExpressionWorldPosition)
    # UE 5.8 overrides GetInputName(1) with the spaced absolute-position pin.
    _wire(world, '', sample, 'World Position')
    if base_normal_is_world:
        normal_input=(surface, 'NormalOut')
    else:
        normal = _expr(material, unreal.MaterialExpressionTransform,
            transform_source_type=unreal.MaterialVectorCoordTransformSource.TRANSFORMSOURCE_TANGENT,
            transform_type=unreal.MaterialVectorCoordTransform.TRANSFORM_WORLD)
        _wire(surface, 'NormalOut', normal, '')
        normal_input=(normal, '')
    low = (bounds['lo'][0]*100, -bounds['hi'][1]*100, bounds['lo'][2]*100, 0)
    high = (bounds['hi'][0]*100, -bounds['lo'][1]*100, bounds['hi'][2]*100, 0)
    inputs = {'BaseColor': (surface, ''), 'BaseNormalWorld': normal_input,
              'BaseGeometricNormal': (_expr(material, unreal.MaterialExpressionVertexNormalWS), ''),
              'BaseRoughness': (surface, 'RoughnessOut'), 'BaseMetallic': (surface, 'MetallicOut'),
              'DecalColor': (sample, 'BaseColor'), 'DecalNormalWorld': (sample, 'Normal'),
              'DecalRoughness': (sample, 'Roughness'), 'DecalMask': (sample, 'Mask'),
              'World': (world, ''),
              'BoundsMin': (_expr(material, unreal.MaterialExpressionConstant3Vector, constant=unreal.LinearColor(*low)), ''),
              'BoundsMax': (_expr(material, unreal.MaterialExpressionConstant3Vector, constant=unreal.LinearColor(*high)), '')}
    for name in ('BaseNormalWorld','BaseRoughness','BaseMetallic',
                 'DecalColor','DecalNormalWorld','DecalRoughness','DecalMask'):
        value = _expr(material, unreal.MaterialExpressionReroute)
        _wire(inputs[name][0], inputs[name][1], value, '')
        inputs[name] = (value, '')
    node = _custom(material, RVT_COMPOSITE_CODE, inputs)
    material.set_editor_property('tangent_space_normal', False)
    return ground_microdetails.author_post_layer(material, textures, node, sample, world, inputs,
        source_microdetail={'id': (surface, 'MicrodetailIdOut'),
                            'valid': (surface, 'MicrodetailValidOut')})


def _landclass_surface(material, textures):
    if textures.landclass is None:
        raise ValueError('land_mesh requires its verified original global landclass data')
    from wt_landdetail import author_surface
    detail, _ = author_surface(material, textures)
    inputs = {'World': (_expr(material, unreal.MaterialExpressionWorldPosition), ''),
              'DetailColor': (detail, ''),
              'VertexNormalWorld': (_expr(material, unreal.MaterialExpressionVertexNormalWS), '')}
    for name, pin in (('DetailNormalWorld', 'NormalOut'),
                      ('DetailRoughness', 'RoughnessOut'),
                      ('DetailMicrodetailId', 'MicrodetailIdOut'),
                      ('DetailMicrodetailValid', 'MicrodetailValidOut')):
        value = _expr(material, unreal.MaterialExpressionReroute)
        _wire(detail, pin, value, '')
        inputs[name] = (value, '')
    for parameter, entry in textures.landclass['textures'].items():
        texture = textures.get(entry['name'], entry['srgb'], nearest=entry['nearest'])
        inputs[parameter] = (_texture(material, parameter, texture, entry['srgb']), '')
    for name, values in shader_vectors(textures.landclass).items():
        inputs[name] = (_vector(material, name, values), 'RGBA')
    return _custom(material, LANDCLASS_CODE, inputs, additional_outputs=[
        ('MicrodetailIdOut', unreal.CustomMaterialOutputType.CMOT_FLOAT1),
        ('MicrodetailValidOut', unreal.CustomMaterialOutputType.CMOT_FLOAT1)])


def parent_material(folder, flavor, textures, save_asset=True):
    if not save_asset and flavor != 'Surface':
        raise ValueError('deferred parent save is supported only for the ObjectOnly Surface gate')
    name = 'WT_Parent_' + flavor
    material = _load_existing(f'{folder}/Materials/{name}') or unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder+'/Materials', unreal.Material, unreal.MaterialFactoryNew())
    _clear_graph(material)
    if flavor in RENDINST_DECAL_FLAVORS:
        return author_rendinst_decal_parent(material, textures, flavor)
    translucent = flavor in ('Decal', 'Glass', 'HeightDecal')
    material.set_editor_property('blend_mode', unreal.BlendMode.BLEND_TRANSLUCENT if translucent else unreal.BlendMode.BLEND_MASKED)
    material.set_editor_property('two_sided', True)
    material.set_editor_property('opacity_mask_clip_value', 0.333)
    EDIT.set_material_usage(material, unreal.MaterialUsage.MATUSAGE_INSTANCED_STATIC_MESHES)
    inputs = {}
    if flavor == 'Water':
        if not getattr(textures, 'water_source', None):
            raise ValueError('water color requires the original map/material/mesh contract')
        material.set_editor_property('blend_mode', unreal.BlendMode.BLEND_OPAQUE)
        _prop(_vector(material, 'SourceWaterColor', WATER_COLOR_RGBA), '', P.MP_BASE_COLOR)
        for prop, value in [(P.MP_ROUGHNESS, WATER_ROUGHNESS), (P.MP_METALLIC, WATER_METALLIC),
                            (P.MP_SPECULAR, WATER_SPECULAR)]:
            _prop(_expr(material, unreal.MaterialExpressionConstant, r=value), '', prop)
        _save_parent(material)
        return material
    if flavor == 'HeightDecal':
        ground_microdetails.definition(textures)
        inputs['T0'] = (_texture(material, 'T0', textures.get(None, True, 'White'), True), '')
        inputs['T2'] = (_texture(material, 'T2', textures.get(None, False, 'Black'), False), '')
        inputs['UV0'] = (_expr(material, unreal.MaterialExpressionTextureCoordinate, coordinate_index=0), '')
        inputs['OpacityUV'] = (_expr(material, unreal.MaterialExpressionTextureCoordinate, coordinate_index=1), '')
        inputs['HeightBlend'] = (_vector(material, 'HeightBlend', (.25, 1, 0, 0)), 'RGBA')
        inputs['HeightSource'] = (_vector(material, 'HeightSource', HEIGHT_SOURCE), 'RGBA')
        inputs['HeightNormalStrength'] = (_scalar(material, 'HeightNormalStrength', HEIGHT_NORMAL_STRENGTH), '')
        node = _custom(material, HEIGHT_DECAL_CODE, inputs, additional_outputs=[
            ('MicrodetailPayloadOut', unreal.CustomMaterialOutputType.CMOT_FLOAT1)])
        material.set_editor_property('tangent_space_normal', False)
        _prop(node, 'OpacityOut', P.MP_OPACITY)
        _write_ground_decal_rvt(material, node, normal_is_world=True, microdetail_pin='MicrodetailPayloadOut')
        unreal.EditorAssetLibrary.set_metadata_tag(material, HEIGHT_NORMAL_METADATA_TAG, normal_adapter_metadata())
        unreal.EditorAssetLibrary.set_metadata_tag(material, HEIGHT_REFLECTION_METADATA_TAG, reflection_metadata())
        _save_parent(material)
        return material
    if flavor == 'Land' and textures.landclass is not None:
        node = _landclass_surface(material, textures)
        _prop(node, 'OpacityOut', P.MP_OPACITY_MASK)
        _read_ground_decal_rvt(material, node, textures, base_normal_is_world=True)
        _save_parent(material)
        return material
    for i in range(7):
        color = i in (0,3,5)
        default = 'White' if i in (0,1,3,5) else 'NormalAG'
        inputs['T'+str(i)] = (_texture(material, 'T'+str(i), textures.get(None,color,default),color), '')
    for i in range(3):
        inputs['UV'+str(i)] = (_expr(material, unreal.MaterialExpressionTextureCoordinate, coordinate_index=i), '')
    for key, value in [('Tiles',(1,1,1,1)), ('Mask',(1,1,1,1)), ('LayerWeights',(1,1,1,0))]:
        inputs[key] = (_vector(material,key,value),'RGBA')
    inputs['Family'] = (_scalar(material,'Family',0),'')
    inputs['AlphaEnabled'] = (_scalar(material,'AlphaEnabled',0),'')
    inputs['AlphaFromT1'] = (_scalar(material,'AlphaFromT1',0),'')
    code = OBJECT_CODE
    if flavor == 'Surface':
        import wt_object_normals
        inputs['World'] = (_expr(material, unreal.MaterialExpressionWorldPosition), '')
        inputs['GeometricNormal'] = (_expr(material, unreal.MaterialExpressionVertexNormalWS), '')
        code = wt_object_normals.surface_code(OBJECT_CODE)
        material.set_editor_property('tangent_space_normal', True)
    node = _custom(material, code, inputs)
    if flavor == 'Surface':
        normal_output = _expr(material, unreal.MaterialExpressionReroute)
        _wire(node, 'NormalOut', normal_output, '')
        _prop(normal_output, '', P.MP_NORMAL)
        unreal.EditorAssetLibrary.set_metadata_tag(
            material, wt_object_normals.METADATA_TAG, wt_object_normals.metadata(OBJECT_CODE))
    _prop(node,'OpacityOut',P.MP_OPACITY if translucent else P.MP_OPACITY_MASK)
    if flavor == 'Leaves':
        facing_leaves_wpo(material)
    if flavor == 'Decal':
        _write_ground_decal_rvt(material, node)
    if flavor == 'Land':
        _read_ground_decal_rvt(material, node, textures)
    if save_asset:
        _save_parent(material)
    return material


def make_instance(folder, spec, parent, textures):
    identity = material_id(spec)
    if spec.get('id',identity) != identity:
        raise ValueError('material identifier does not match source data')
    tree_alpha = tree_alpha_contract(spec, textures.source)
    if spec['shader'] == 'water_aces' and not getattr(textures, 'water_source', None):
        raise ValueError('water instance requires its original source contract')
    if spec['shader'] == HEIGHT_DECAL_SHADER and not getattr(textures, 'height_decal_source_verified', False):
        source = json.loads((textures.package / 'scene-source.json').read_text(encoding='utf-8-sig'))
        validate_scene_vertex_opacity(textures.package, source)
        textures.height_decal_source_verified = True
    instance = _load_existing(f'{folder}/Materials/{identity}') or unreal.AssetToolsHelpers.get_asset_tools().create_asset(identity, folder+'/Materials', unreal.MaterialInstanceConstant, unreal.MaterialInstanceConstantFactoryNew())
    EDIT.set_material_instance_parent(instance,parent)
    shader = spec['shader']; params=parameters(spec['parameters']); slots=list(spec['texture_slots'])
    if rendinst_decal_flavor(shader) is not None:
        if parent.get_name() != 'WT_Parent_' + rendinst_decal_flavor(shader):
            raise ValueError('source mesh decal requires its dedicated parent')
        author_rendinst_decal_instance(instance, spec, textures)
        return identity, instance
    def values(name,default):
        v=params.get(name,default)
        return (v+list(default[len(v):]))[:len(default)]
    def value(name,default):return values(name,[default])[0]
    if shader == 'water_aces':
        if parent.get_name() != 'WT_Parent_Water' or identity != textures.water_source['material_id']:
            raise ValueError('source water color requires its dedicated parent and original material identity')
        EDIT.clear_all_material_instance_parameters(instance)
        instance.set_editor_property('base_property_overrides', unreal.MaterialInstanceBasePropertyOverrides())
        # UE5.8 MaterialEditingLibrary.cpp:1573-1581 leaves its setter result
        # false after updating the value. Validate the actual override and RGB.
        EDIT.set_material_instance_vector_parameter_value(instance, 'SourceWaterColor', unreal.LinearColor(*WATER_COLOR_RGBA))
        EDIT.update_material_instance(instance)
        if not EDIT.is_material_instance_parameter_overridden(instance, 'SourceWaterColor'):
            raise RuntimeError('water source RGB override was not installed')
        color = EDIT.get_material_instance_vector_parameter_value(instance, 'SourceWaterColor')
        actual = [float(color.get_editor_property(channel)) for channel in ('r','g','b','a')]
        if not all(abs(got-wanted) <= 1.e-7 for got, wanted in zip(actual, WATER_COLOR_RGBA)):
            raise RuntimeError('water instance RGB differs from the original level source')
        if not unreal.EditorAssetLibrary.save_loaded_asset(instance):
            raise RuntimeError('failed to save original water instance: ' + identity)
        return identity, instance
    if shader == HEIGHT_DECAL_SHADER:
        if parent.get_name() != 'WT_Parent_HeightDecal':
            raise ValueError('source height-decal requires its dedicated parent')
        height_surface_contract(spec, ground_microdetails.definition(textures))
        EDIT.set_material_instance_vector_parameter_value(instance, 'HeightBlend', unreal.LinearColor(*height_blend_parameters(spec)))
        EDIT.set_material_instance_vector_parameter_value(instance, 'HeightSource', unreal.LinearColor(*HEIGHT_SOURCE))
        EDIT.set_material_instance_scalar_parameter_value(instance, 'HeightNormalStrength', HEIGHT_NORMAL_STRENGTH)
        unreal.EditorAssetLibrary.set_metadata_tag(instance, HEIGHT_NORMAL_METADATA_TAG, normal_adapter_metadata())
        unreal.EditorAssetLibrary.set_metadata_tag(instance, HEIGHT_REFLECTION_METADATA_TAG, reflection_metadata())
        unreal.EditorAssetLibrary.set_metadata_tag(instance, HEIGHT_POLARITY_METADATA_TAG, height_polarity_metadata(spec))
        for index, color, fallback in [(0, True, 'White'), (2, False, 'Black')]:
            texture = textures.get(slots[index] if index < len(slots) else None, color, fallback)
            EDIT.set_material_instance_texture_parameter_value(instance, 'T' + str(index), texture)
        EDIT.update_material_instance(instance)
        if not EDIT.is_material_instance_parameter_overridden(instance, 'HeightNormalStrength'):
            raise RuntimeError('height-decal UE normal-strength override was not installed: ' + identity)
        actual_strength = EDIT.get_material_instance_scalar_parameter_value(instance, 'HeightNormalStrength')
        if abs(float(actual_strength)-HEIGHT_NORMAL_STRENGTH) > 1.e-7:
            raise RuntimeError('height-decal UE normal-strength value differs: ' + identity)
        if not unreal.EditorAssetLibrary.save_loaded_asset(instance):
            raise RuntimeError('failed to save height-decal instance: ' + identity)
        return identity, instance
    if shader == 'land_mesh' and textures.landclass is not None:
        if parent.get_name() != 'WT_Parent_Land':
            raise ValueError('source global landclass colors require the Land parent')
        EDIT.clear_all_material_instance_parameters(instance)
        EDIT.update_material_instance(instance)
        if not unreal.EditorAssetLibrary.save_loaded_asset(instance):
            raise RuntimeError('failed to save original land_mesh instance: ' + identity)
        return identity, instance
    layered='layered' in shader and 'perlin' not in shader
    family=2 if 'perlin_layered' in shader else 1 if layered else 3 if 'single_layer_overlay' in shader else 4 if 'decal' in shader else 0
    EDIT.set_material_instance_scalar_parameter_value(instance,'Family',family)
    alpha='tree' in shader or 'leaves' in shader or 'decal' in shader or 'glass' in shader or value('atest',0)>0
    EDIT.set_material_instance_scalar_parameter_value(instance,'AlphaEnabled',float(alpha))
    if tree_alpha is not None and value('atest', 0) != tree_alpha['atest']:
        raise ValueError('tree split-alpha threshold differs from the audited source')
    EDIT.set_material_instance_scalar_parameter_value(instance,'AlphaFromT1',float(tree_alpha is not None))
    if value('atest',0)>0:
        overrides=unreal.MaterialInstanceBasePropertyOverrides()
        overrides.set_editor_property('override_opacity_mask_clip_value',True)
        overrides.set_editor_property('opacity_mask_clip_value',min(1.0,value('atest',0)/255.0))
        instance.set_editor_property('base_property_overrides',overrides)
    tiles=values('details_tile',[1,1,1,0]) if family==2 else [value('detail1_tile_u',1),value('detail1_tile_v',1),value('detail2_tile_u',1),value('detail2_tile_v',1)]
    EDIT.set_material_instance_vector_parameter_value(instance,'Tiles',unreal.LinearColor(*tiles))
    EDIT.set_material_instance_vector_parameter_value(instance,'Mask',unreal.LinearColor(value('mask_tile_u',1),value('mask_tile_v',1),(value('mask_gamma_start',.5)+value('mask_gamma_end',2))/2,0))
    weights=[1,float(bool(slots[3])),float(bool(slots[5])),0]
    EDIT.set_material_instance_vector_parameter_value(instance,'LayerWeights',unreal.LinearColor(*weights))
    # Missing detail slots use the original base layer; missing normal is neutral AG.
    for i in range(7):
        name=slots[i] if i<len(slots) else None
        if i in (3,5) and not name:
            name=slots[0] if family==2 else None
        texture=textures.get(name,i in (0,3,5),'White' if i in (0,1,3,5) else 'NormalAG')
        EDIT.set_material_instance_texture_parameter_value(instance,'T'+str(i),texture)
    EDIT.update_material_instance(instance)
    unreal.EditorAssetLibrary.save_loaded_asset(instance)
    return identity,instance


def terrain_material(folder,textures,preserve_grass=False):
    from wt_landdetail import author_surface
    material=_load_existing(folder+'/Materials/WT_Terrain') or unreal.AssetToolsHelpers.get_asset_tools().create_asset('WT_Terrain',folder+'/Materials',unreal.Material,unreal.MaterialFactoryNew())
    keep=[]
    if preserve_grass:
        pending=[node for node in EDIT.get_material_expressions(material) if isinstance(node,unreal.MaterialExpressionLandscapeGrassOutput)]
        seen=set()
        while pending:
            node=pending.pop()
            if node.get_path_name() in seen:continue
            seen.add(node.get_path_name());keep.append(node)
            pending.extend(x for x in EDIT.get_inputs_for_material_expression(material,node) if x)
    _clear_graph(material,keep=keep)
    material.set_editor_property('blend_mode',unreal.BlendMode.BLEND_MASKED)
    material.set_editor_property('two_sided',True)
    surface,_=author_surface(material,textures)
    _read_ground_decal_rvt(material,surface,textures,base_normal_is_world=True)
    mask=_expr(material,unreal.MaterialExpressionLandscapeVisibilityMask)
    _prop(mask,'',P.MP_OPACITY_MASK)
    _save_parent(material)
    return material


def create_materials(source_directory, ue_folder):
    package=Path(source_directory).resolve()
    manifest=json.loads((package/'scene-source.json').read_text(encoding='utf-8'))
    extraction=Path(manifest.get('extraction_root',package.parents[2]/'artifacts/maps/wt_existing/avg_vlaanderen'))
    if not extraction.is_absolute():extraction=(package/extraction).resolve()
    if not ue_folder.startswith('/Game/RMSim/Maps/') or '..' in ue_folder:
        raise ValueError('map material assets must be inside /Game/RMSim/Maps')
    opacity_records=validate_scene_vertex_opacity(package,manifest)
    landclass=validate_landclass_surface(package,manifest)
    water_source=water_surface_contract(manifest,package)
    textures=Textures(package,extraction,ue_folder,landclass,water_source)
    textures.height_decal_source_verified=True
    rvt=_ground_decal_rvt(textures) if any(mesh['id']=='world_decal_000' for mesh in manifest.get('meshes',[])) else None
    parents={};mapping={};records=[]
    for i,spec in enumerate(manifest['materials']):
        flavor=material_flavor(spec['shader'])
        if flavor not in parents:parents[flavor]=parent_material(ue_folder,flavor,textures)
        identity,material=make_instance(ue_folder,spec,parents[flavor],textures)
        mapping[identity]=material
        external=spec['shader']=='land_mesh' and landclass is None
        record=dict(id=identity,source_shader=spec['shader'],asset=material.get_path_name(),adapter=flavor,
            status='unresolved_external_scene_binding' if external else 'authored_renderer_adapter',
            unresolved='Dagor supplies terrain/water base data outside the image slots; neutral base remains under projected ground decals' if external else '')
        split_alpha=tree_alpha_contract(spec,textures.source)
        if split_alpha is not None:record['source_split_alpha']=split_alpha
        source_decal=rendinst_decal_contract(spec)
        if source_decal is not None:record['source_rendinst_decal']=source_decal
        if spec['shader']=='water_aces':record['source_water_surface']=water_source
        if spec['shader']==HEIGHT_DECAL_SHADER:
            record['source_height_surface']=height_surface_contract(spec,ground_microdetails.definition(textures))
        if spec['shader']=='land_mesh' and landclass is not None:
            record['source_landclass_surface']=dict(manifest_sha256=manifest['source_landclass_surface']['manifest_sha256'],
                verification_sha256=landclass['verification_sha256'], class_count=len(landclass['classes']),
                original_packed_weight_atlas=True, color_uv='(DagorX/size,-DagorZ/size) with original offsets')
        records.append(record)
        if (i+1)%100==0:unreal.log(f'WT_MATERIALS {i+1}/{len(manifest["materials"])}')
    terrain=terrain_material(ue_folder,textures)
    report=dict(schema='wt_material_import_report_v1',version=VERSION,materials=records,textures=textures.imported,
        native_render_parity=False,
        source_landclass_surface=manifest.get('source_landclass_surface'),
        source_water_surface=water_source,
        source_ground_microdetails=dict(definition_sha256=ground_microdetails.DEFINITION_SHA256,
            slot_count=12, original_texture_count=11, post_rvt=True,
            adaptation='source-backed UE post-RVT; original public land consumer is not available'),
        terrain_macro_index_mapping='float2(World.x,-World.y)/204800; categorical index mip0; source detail scales 11.12/11.23m on positive World.xy',
        ordinary_tree_split_alpha=dict(source_audit_sha256=TREE_ALPHA_AUDIT_SHA256,
            material_ids=[record['id'] for record in records if 'source_split_alpha' in record],
            sampling='T1.r for audited opaque-color/separate-gray-alpha source pairs with original atest',
            public_shader_variant='rendinst_tree_colored_alpha_split; source materials retain their original plain shader names'),
        height_decals=dict(source_shader=HEIGHT_DECAL_SHADER, vertex_opacity_uv=1,
            vertex_opacity_component='x', source_attribute='short4n_position_w',
            formula='saturate((pow(vertexOpacity,vertex_gamma)-((1-2*height_above)*diffuse.a+height_above))/(0.0001+diffuse_threshold))',
            source_mesh_proofs=opacity_records, dagor_commit='9639942de5a8e5b01d91977f60ed82cda22d5f41',
            source_shader_file='prog/daNetGame/shaders/make_clipmap.dshl',
            composition='UE runtime virtual texture projected onto Landscape and outer landmesh',
            runtime_virtual_texture=rvt.get_path_name() if rvt else None,
            receiver_parent_flavors=['Land','Terrain'],writer_parent_flavors=['HeightDecal','Decal'],
            writer_meshes=['world_decal_000'],main_pass='Never',
            coverage='saturate((Mask-128/255)/(1-128/255))',
            color_composition='RVTColor+BaseColor*(1-coverage)',
            normal='alpha-height UV gradient and receiver geometric-normal RNM',
            reflection_channels='T2 _r: A smoothness, G AO, R reflectance, B microdetail ID; no AG normal decoding',
            microdetail_transport='RVT Specular raw payload (1+clampedID)/13; native opacity multiplication; continuous floor/next layer blend'),
        facing_leaves=dict(source_shader='rendinst_facing_leaves', mode='camera_no_wind',
            delta_xy_uv=3, delta_z_wind_uv=4, uv_units='original metres and wind group',
            reference_local_ue_cm='100*(dx,-dz,dy)', camera_ue_cm='100*(-dx,dy,-dz)',
            world_position_offset='length(InstanceToWorld(1,0,0))*CameraToWorld(camera_ue_cm)-InstanceToWorld(reference_local_ue_cm)',
            instance_scope='positive uniform source scales', source_normals='retained',
            wind_animation=False, bvh_shadow_push=False,
            dagor_commit='9639942de5a8e5b01d91977f60ed82cda22d5f41',
            source_basis='prog/engine/libFx/leavesWind.cpp:setNoAnimShaderVars',
            source_camera='prog/engine/coreUtil/flyMode.cpp:keys.fwd/right/cameraUp',
            source_offsets='prog/gameLibs/bvh/shaders/bvh_process_leaves_vertices.dshl:tc2/delta_w'),
        unresolved_shader_bindings=[r['id'] for r in records if r['status']=='unresolved_external_scene_binding'],
        terrain_material=terrain.get_path_name(),limitations=[
            'PBR adapter from original source textures and UVs; not native Dagor shader parity',
            'layered source UV1 overlay, UV2 detail tiling and AG normals retained; random UV/palette paint/microdetail wetness omitted',
            'perlin family uses original height-weighted layers, without original spatial Perlin modulation',
            'ground decal geometry writes source color/opacity into RVT; rendinst diffuse/normal decals use native mesh DeferredDecal, and modulate2x uses linear Surface/Unlit/Modulate color multiplication',
            'modulate2x multiplies lit scene color; original smoothness/metalness multiplication, independent diffuse normal opacity, source per-section draw_order and palette painting remain unadapted',
            'rendinst_facing_leaves uses recovered source half4 offsets and dynamic no-wind camera-facing cards; wind/rustling animation remains pending',
            'terrain and core LC1 use original 65-biome dual-layer color/height rules, 23 original detail textures, source flow rotation and categorical mip0 sampling; normals use an explicit UE alpha-height surface-gradient adapter',
            'outer land_mesh retains five macro classes/packed source weights and the core LC1 detail adapter; other landclass details, wetness and vertical-cliff shading remain separate',
            'source height decals use reflection A for roughness and B for the original 12-slot microdetail array; reflectance R/AO G and self-color/wetness are retained as source scope without independent UE PBR transmission',
            'post-RVT microdetail application is a UE adaptation using public color/AG-normal/smoothness helpers and original level textures; RVT BC compression/filtering can alter ID mixing weights',
            'water_aces binds verified level RGB to the original three pond surfaces; density/params, waves, reflection and refraction remain unbound source semantics',
            'glass/interior mapping and emissive shader-specific effects are not equivalent to source renderer'])
    (package/'material-import-report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return mapping,terrain