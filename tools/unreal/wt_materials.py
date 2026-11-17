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

EDIT = unreal.MaterialEditingLibrary
P = unreal.MaterialProperty
VERSION = 1


def material_id(spec):
    value = {key: spec[key] for key in ('shader', 'parameters', 'texture_slots')}
    return 'WT_' + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:20]


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


def _png(path, rgba):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>2I5B', 1, 1, 8, 6, 0, 0, 0)) +
                     chunk(b'IDAT', zlib.compress(bytes([0, *rgba]))) + chunk(b'IEND', b''))


class Textures:
    def __init__(self, package, extraction, folder):
        self.package, self.folder = package, folder
        paths = sorted(extraction.glob('textures-*/result.json'))
        if not paths:
            raise ValueError('no extracted texture inventory')
        report = json.loads(paths[-1].read_text(encoding='utf-8'))
        self.source = {item['name']: item for item in report['exported']}
        self.cache, self.imported = {}, []
        self.directory = package / 'textures'
        self.directory.mkdir(exist_ok=True)
        self.default_paths = {}
        for name, rgba in [('White', (255,255,255,255)), ('NormalAG', (0,128,0,128)), ('Black', (0,0,0,255))]:
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
        texture = unreal.load_asset(asset_path)
        if texture is None:
            imported = import_asset(path, self.folder + '/Textures', asset_name)
            texture = next((obj for obj in imported if isinstance(obj, unreal.Texture2D)), None)
        if texture is None:
            raise RuntimeError('texture import failed: ' + str(path))
        texture.set_editor_property('srgb', color)
        texture.set_editor_property('compression_settings', unreal.TextureCompressionSettings.TC_DEFAULT if color else unreal.TextureCompressionSettings.TC_VECTOR_DISPLACEMENTMAP)
        if nearest:
            texture.set_editor_property('filter', unreal.TextureFilter.TF_NEAREST)
        unreal.EditorAssetLibrary.save_loaded_asset(texture)
        self.cache[key] = texture
        self.imported.append(dict(name=name or default, asset=texture.get_path_name(), file=str(path.relative_to(self.package)), srgb=color, nearest=nearest))
        return texture


def _expr(material, cls, **values):
    node = EDIT.create_material_expression(material, cls)
    for name, value in values.items():
        node.set_editor_property(name, value)
    return node


def _wire(source, output, target, socket):
    if not EDIT.connect_material_expressions(source, output, target, socket):
        raise RuntimeError(f'cannot connect {source.get_name()}.{output} to {target.get_name()}.{socket}')


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


def _custom(material, code, inputs, terrain=False):
    outputs = [('NormalOut', unreal.CustomMaterialOutputType.CMOT_FLOAT3),
               ('RoughnessOut', unreal.CustomMaterialOutputType.CMOT_FLOAT1),
               ('MetallicOut', unreal.CustomMaterialOutputType.CMOT_FLOAT1),
               ('OpacityOut', unreal.CustomMaterialOutputType.CMOT_FLOAT1)]
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
OpacityOut=AlphaEnabled>0.5 ? c.a : 1;
return max(result,0);
'''


def parent_material(folder, flavor, textures):
    name = 'WT_Parent_' + flavor
    material = unreal.load_asset(f'{folder}/Materials/{name}') or unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder+'/Materials', unreal.Material, unreal.MaterialFactoryNew())
    EDIT.delete_all_material_expressions(material)
    translucent = flavor in ('Decal', 'Glass')
    material.set_editor_property('blend_mode', unreal.BlendMode.BLEND_TRANSLUCENT if translucent else unreal.BlendMode.BLEND_MASKED)
    material.set_editor_property('two_sided', True)
    material.set_editor_property('opacity_mask_clip_value', 0.333)
    EDIT.set_material_usage(material, unreal.MaterialUsage.MATUSAGE_INSTANCED_STATIC_MESHES)
    inputs = {}
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
    node = _custom(material, OBJECT_CODE, inputs)
    _prop(node,'OpacityOut',P.MP_OPACITY if translucent else P.MP_OPACITY_MASK)
    EDIT.recompile_material(material)
    unreal.EditorAssetLibrary.save_loaded_asset(material)
    return material


def make_instance(folder, spec, parent, textures):
    identity = material_id(spec)
    if spec.get('id',identity) != identity:
        raise ValueError('material identifier does not match source data')
    instance = unreal.load_asset(f'{folder}/Materials/{identity}') or unreal.AssetToolsHelpers.get_asset_tools().create_asset(identity, folder+'/Materials', unreal.MaterialInstanceConstant, unreal.MaterialInstanceConstantFactoryNew())
    EDIT.set_material_instance_parent(instance,parent)
    shader = spec['shader']; params=parameters(spec['parameters']); slots=list(spec['texture_slots'])
    def values(name,default):
        v=params.get(name,default)
        return (v+list(default[len(v):]))[:len(default)]
    def value(name,default):return values(name,[default])[0]
    layered='layered' in shader and 'perlin' not in shader
    family=2 if 'perlin_layered' in shader else 1 if layered else 3 if 'single_layer_overlay' in shader else 4 if 'decal' in shader else 0
    EDIT.set_material_instance_scalar_parameter_value(instance,'Family',family)
    alpha='tree' in shader or 'leaves' in shader or 'decal' in shader or 'glass' in shader or value('atest',0)>0
    EDIT.set_material_instance_scalar_parameter_value(instance,'AlphaEnabled',float(alpha))
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


TERRAIN_DETAILS = [
    'biome_detail_grass_b_tex_d', 'biome_detail_moss_a_tex_d', 'biome_detail_sand_soil_a_tex_d',
    'biome_detail_rock_boulders_a_tex_d', 'biome_detail_asphalt_c_tex_d', 'biome_detail_slope_soil_d_tex_d',
    'biome_detail_mud_b_tex_d', 'biome_detail_sett_small_old_tex_d', 'biome_detail_burnt_grass_a_tex_d',
]


def terrain_material(folder,textures):
    material=unreal.load_asset(folder+'/Materials/WT_Terrain') or unreal.AssetToolsHelpers.get_asset_tools().create_asset('WT_Terrain',folder+'/Materials',unreal.Material,unreal.MaterialFactoryNew())
    EDIT.delete_all_material_expressions(material)
    material.set_editor_property('blend_mode',unreal.BlendMode.BLEND_MASKED)
    material.set_editor_property('two_sided',True)
    inputs={'World':(_expr(material,unreal.MaterialExpressionWorldPosition),'')}
    inputs['Macro']=(_texture(material,'Macro',textures.get('avg_vlaanderen_detailed_biome_tex_d',True),True),'')
    inputs['Index']=(_texture(material,'Index',textures.get('avg_vlaanderen_detailed_biome_tex_b',False,nearest=True),False),'')
    for i,name in enumerate(TERRAIN_DETAILS):
        inputs['D'+str(i)]=(_texture(material,'D'+str(i),textures.get(name,True),True),'')
    samples='\n'.join(f'float3 c{i}=Texture2DSample(D{i},D{i}Sampler,detailUV).rgb;' for i in range(len(TERRAIN_DETAILS)))
    code='''float2 mapUV=World.xy/204800.0;
float2 detailUV=World.xy/100.0/11.12;
float4 macro=Texture2DSample(Macro,MacroSampler,mapUV);
int index=(int)round(Texture2DSample(Index,IndexSampler,mapUV).r*255);
'''+samples+'''
float3 detail=c0;
if(index==3 || index==4)detail=c1;
else if(index==5 || index==25)detail=c2;
else if(index==6 || index==7)detail=c3;
else if(index==8)detail=c4;
else if(index==28)detail=c5;
else if(index==34)detail=c6;
else if(index==35)detail=c7;
else if(index==24)detail=c8;
NormalOut=float3(0,0,1);RoughnessOut=0.9;MetallicOut=0;OpacityOut=1;
// Original macro color and mapped source microdetail. Full Dagor dual-layer flowmap blending is pending.
return macro.rgb*lerp(float3(1,1,1),detail*4.59479342,0.45);
'''
    _custom(material,code,inputs,terrain=True)
    mask=_expr(material,unreal.MaterialExpressionLandscapeVisibilityMask)
    _prop(mask,'',P.MP_OPACITY_MASK)
    EDIT.recompile_material(material);unreal.EditorAssetLibrary.save_loaded_asset(material)
    return material


def create_materials(source_directory, ue_folder):
    package=Path(source_directory).resolve()
    manifest=json.loads((package/'scene-source.json').read_text(encoding='utf-8'))
    extraction=Path(manifest.get('extraction_root',package.parents[2]/'artifacts/maps/wt_existing/avg_vlaanderen'))
    if not extraction.is_absolute():extraction=(package/extraction).resolve()
    if not ue_folder.startswith('/Game/RMSim/Maps/') or '..' in ue_folder:
        raise ValueError('map material assets must be inside /Game/RMSim/Maps')
    textures=Textures(package,extraction,ue_folder)
    parents={};mapping={};records=[]
    for i,spec in enumerate(manifest['materials']):
        flavor='Glass' if 'glass' in spec['shader'] else 'Decal' if 'decal' in spec['shader'] else 'Surface'
        if flavor not in parents:parents[flavor]=parent_material(ue_folder,flavor,textures)
        identity,material=make_instance(ue_folder,spec,parents[flavor],textures)
        mapping[identity]=material
        external=spec['shader'] in ('land_mesh','water_aces')
        records.append(dict(id=identity,source_shader=spec['shader'],asset=material.get_path_name(),adapter=flavor,
            status='unresolved_external_scene_binding' if external else 'authored_renderer_adapter',
            unresolved='Dagor supplies terrain/water data outside the material texture slots; neutral default is only a visible diagnostic' if external else ''))
        if (i+1)%100==0:unreal.log(f'WT_MATERIALS {i+1}/{len(manifest["materials"])}')
    terrain=terrain_material(ue_folder,textures)
    report=dict(schema='wt_material_import_report_v1',version=VERSION,materials=records,textures=textures.imported,
        native_render_parity=False,
        unresolved_shader_bindings=[r['id'] for r in records if r['status']=='unresolved_external_scene_binding'],
        terrain_material=terrain.get_path_name(),limitations=[
            'PBR adapter from original source textures and UVs; not native Dagor shader parity',
            'layered source UV1 overlay, UV2 detail tiling and AG normals retained; random UV/palette paint/microdetail wetness omitted',
            'perlin family uses original height-weighted layers, without original spatial Perlin modulation',
            'mesh decals use surface translucency rather than Dagor deferred G-buffer composition',
            'vegetation wind/pivot animation and old billboard leaf expansion are not implemented',
            'terrain uses original macro and index map with grouped source microdetail; original dual-layer flowmaps and physical material mapping pending',
            'glass/interior mapping and emissive shader-specific effects are not equivalent to source renderer'])
    (package/'material-import-report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return mapping,terrain
