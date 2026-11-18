"""Original Flanders microdetails as a source-backed UE post-RVT adaptation.

The original 12-slot array, UV scale and scalar ID mapping are retained. UE RVT
Specular transports a coverage-weighted payload instead of physical specular.
The public land helper supplies color/normal/smoothness math; the shipped game's
land consumer is not present in the pinned public checkout.
"""
import hashlib
import json
import math
from pathlib import Path
import struct

SCHEMA = 'wt_ground_microdetails_v1'
DEFINITION_FILE = 'ground-microdetails/definition.json'
DEFINITION_SHA256 = '2312ee57fba11d5bca259cfe3dd5888f83524fef358e0181499023f19e037d54'
SOURCE_LEVEL_SHA256 = '427c9b06b62af0635fa08f2d6238211b9850caf7717a0773f186e1835be63b8f'
ARRAY_NAME = 'WT_GroundMicrodetails'
CLEAR_VALUE = 128 / 255


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _require(value, message):
    if not value:
        raise ValueError('ground microdetails: ' + message)


def _path(base, relative):
    path = (base / relative).resolve(strict=True)
    _require(path.is_relative_to(base), 'source proof path escaped its package')
    return path


def validate(package):
    package = Path(package).resolve(strict=True)
    filename = package / DEFINITION_FILE
    _require(digest(filename) == DEFINITION_SHA256, 'original source definition bytes differ')
    data = json.loads(filename.read_text(encoding='utf-8-sig'))
    base = filename.parent
    _require(data['schema'] == SCHEMA and data['source_level_sha256'] == SOURCE_LEVEL_SHA256,
             'source map identity differs')
    _require(data['slot_count'] == 12 and data['unique_texture_count'] == 11
             and data['slice_size'] == [512,512] and data['texture_srgb'] is False,
             'source slot count, dimensions or channel policy differs')
    _require(digest(_path(base,data['source_config_file'])) == data['source_config_sha256']
             and digest(_path(base,data['source_binding_file'])) == data['source_binding_sha256'],
             'original level configuration or archive binding differs')
    proof_file = _path(base,data['proof_file'])
    _require(digest(proof_file) == data['proof_sha256'], 'source proof bytes differ')
    proof = json.loads(proof_file.read_text(encoding='utf-8-sig'))
    _require(proof['schema'] == 'wt_ground_microdetails_preparation_v1'
             and proof['status'] == 'passed' and all(x['passed'] for x in proof['checks']),
             'source preparation proof did not pass')
    scale = struct.unpack('<f',struct.pack('<f',255/(255//11)))[0]
    _require(data['id_count_scale'] == scale and data['id_count_scale_exact_ratio'] == [255,23],
             'source CPU integer-division ID mapping differs')
    _require(data['uv_scale_per_m'] == struct.unpack('<f',struct.pack('<f',.51))[0]
             and data['source_uv_rotation_degrees'] == 0, 'source world UV differs')
    checked = set()
    for i,slot in enumerate(data['slots']):
        _require(slot['index'] == i and slot['source_parameter_index'] == i
                 and slot['size'] == [512,512] and len(slot['params']) == 4
                 and all(math.isfinite(v) for v in slot['params']), 'source slot order/parameters differ')
        path = _path(base,slot['texture_file'])
        if path not in checked:
            _require(digest(path) == slot['texture_sha256'], 'original microdetail PNG bytes differ')
            with path.open('rb') as stream:
                header=stream.read(29)
            _require(header[:8] == b'\x89PNG\r\n\x1a\n'
                     and struct.unpack('>II',header[16:24]) == (512,512)
                     and header[24:26] == bytes((8,6)), 'original microdetail must remain RGBA8 512-square')
            checked.add(path)
    _require(len(data['slots']) == 12 and len(checked) == 11
             and data['slots'][4]['texture_sha256'] == data['slots'][11]['texture_sha256'],
             'the original duplicate final slot must be retained')
    return data


def definition(textures):
    if not hasattr(textures,'ground_microdetail_definition'):
        textures.ground_microdetail_definition = validate(textures.package)
    return textures.ground_microdetail_definition


def shader_code(data):
    scale = format(data['uv_scale_per_m'],'.17g')
    count = int(data['slot_count'])
    return ('const float slotCount='+str(count)+'.0;\n'
            'const float sourceUvScale='+scale+';\n') + r'''
const float clearValue=128.0/255.0;
float coverage=saturate((DecalMask-clearValue)/(1-clearValue));
float inside=step(BoundsMin.x,World.x)*step(BoundsMin.y,World.y)*step(World.x,BoundsMax.x)*step(World.y,BoundsMax.y);
coverage*=inside;
NormalOut=normalize(BaseNormalWorld);RoughnessOut=BaseRoughness;
MetallicOut=BaseMetallic;OpacityOut=1;
// Evaluate world gradients outside the coverage branch for stable array mips.
float2 uv=World.xy*0.01*sourceUvScale;
float2 uvDdx=ddx(uv),uvDdy=ddy(uv);
if(coverage<=0.0001) return BaseColor;
// UE writes raw attributes * Opacity and blends ONE, INVSRCALPHA.
// Remove clear exactly once. The payload is never pre-multiplied in our graph.
float payload=max((DecalIdPayload-clearValue*(1-coverage))/max(coverage,0.0001),0);
float valid=saturate(payload*(slotCount+1));
float index=clamp(payload*(slotCount+1)-1,0,slotCount-1);
float lower=floor(index),upper=min(lower+1,slotCount-1),part=frac(index);
// Source non-correct-bilinear branch: continuously blend floor/next slices.
// Integer array indices are stable; no rounded-ID threshold after RVT filtering.
float4 md0=Texture2DArraySampleGrad(Microdetails,MicrodetailsSampler,float3(uv,lower),uvDdx,uvDdy);
float4 md1=Texture2DArraySampleGrad(Microdetails,MicrodetailsSampler,float3(uv,upper),uvDdx,uvDdy);
float4 micro=lerp(md0,md1,part);
float strength=coverage*valid;
float diffuseFactor=(micro.r*2)*(micro.r*2);
float modifiedRoughness=1-saturate((1-BaseRoughness)*micro.r*2);
RoughnessOut=clamp(lerp(BaseRoughness,modifiedRoughness,strength),0.04,1);
float2 xy=micro.ag*2-1;
float3 detailNormal=normalize(float3(xy,sqrt(saturate(1-dot(xy,xy)))));
float3 t=NormalOut+float3(0,0,1),u=detailNormal*float3(-1,-1,1);
float3 combined=normalize(t*(dot(t,u)/max(t.z,1.e-5))-u);
NormalOut=normalize(lerp(NormalOut,combined,strength));
return max(BaseColor*lerp(1,diffuseFactor,strength),0);
'''


def ensure_array(textures):
    import unreal
    import wt_materials as w
    if hasattr(textures,'ground_microdetail_array'):
        return textures.ground_microdetail_array
    data = definition(textures)
    base = textures.package / Path(DEFINITION_FILE).parent
    for slot in data['slots']:
        name = slot['name']
        if name in textures.source and textures.source[name]['sha256'] != slot['texture_sha256']:
            raise ValueError('microdetail texture differs from existing source inventory')
        textures.source[name] = dict(file=str(base/slot['texture_file']),sha256=slot['texture_sha256'])
    slices = [textures.get(slot['name'],False) for slot in data['slots']]
    for texture,slot in zip(slices,data['slots']):
        unreal.EditorAssetLibrary.set_metadata_tag(texture,'WT_GroundMicrodetailPNG_SHA256',slot['texture_sha256'])
        if not unreal.EditorAssetLibrary.save_loaded_asset(texture):
            raise RuntimeError('failed to save source microdetail texture metadata')
    path = textures.folder+'/Textures/'+ARRAY_NAME
    array = w._load_existing(path)
    if array is None:
        unreal.SystemLibrary.execute_console_command(None,'r.AllowTexture2DArrayCreation 1')
        array = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            ARRAY_NAME,textures.folder+'/Textures',unreal.Texture2DArray,unreal.Texture2DArrayFactory())
    if not isinstance(array,unreal.Texture2DArray):
        raise RuntimeError('ground microdetail array creation/class differs')
    expected = [x.get_path_name() for x in slices]
    existing = [x.get_path_name() if x else None for x in array.get_editor_property('source_textures')]
    if existing != expected:
        array.set_editor_property('source_textures',slices)
    for name,value in (
        ('srgb',False),('compression_settings',unreal.TextureCompressionSettings.TC_VECTOR_DISPLACEMENTMAP),
        ('address_x',unreal.TextureAddress.TA_WRAP),('address_y',unreal.TextureAddress.TA_WRAP),
        ('address_z',unreal.TextureAddress.TA_CLAMP),
        ('mip_gen_settings',unreal.TextureMipGenSettings.TMGS_SIMPLE_AVERAGE),
        ('lod_bias',0),('max_texture_size',0),('never_stream',True)):
        if array.get_editor_property(name) != value:
            array.set_editor_property(name,value)
    unreal.EditorAssetLibrary.set_metadata_tag(array,'WT_SourceDefinitionSHA256',DEFINITION_SHA256)
    unreal.SystemLibrary.execute_console_command(None,'Editor.AsyncTextureCompilationFinishAll')
    native=json.loads(unreal.MapImportLibrary.describe_map_texture_array(array))
    if ((native.get('width'),native.get('height'),native.get('slices')) != (512,512,12)
            or native.get('source_textures') != expected or native.get('mips',0)<2):
        raise RuntimeError('native original microdetail dimensions/slices differ: '+json.dumps(native))
    if not unreal.EditorAssetLibrary.save_loaded_asset(array):
        raise RuntimeError('failed to save original microdetail array')
    textures.ground_microdetail_array = array
    return array


def author_post_layer(material,textures,surface,rvt,world,bounds_inputs):
    import unreal
    import wt_materials as w
    data = definition(textures)
    array = ensure_array(textures)
    # Separate reroutes make each serialized output pin unambiguous to the native gate.
    def wire_output(source,pin):
        reroute=w._expr(material,unreal.MaterialExpressionReroute)
        w._wire(source,pin,reroute,'')
        return (reroute,'')
    inputs = {
        'BaseColor':(surface,''),'BaseNormalWorld':wire_output(surface,'NormalOut'),
        'BaseRoughness':wire_output(surface,'RoughnessOut'),'BaseMetallic':wire_output(surface,'MetallicOut'),
        'DecalMask':wire_output(rvt,'Mask'),'DecalIdPayload':wire_output(rvt,'Specular'),'World':(world,''),
        'BoundsMin':bounds_inputs['BoundsMin'],'BoundsMax':bounds_inputs['BoundsMax'],
        'Microdetails':(w._texture(material,'SourceGroundMicrodetails',array,False),'')}
    node=w._custom(material,shader_code(data),inputs)
    material.set_editor_property('tangent_space_normal',False)
    return node
