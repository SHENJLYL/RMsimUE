"""Original Flanders microdetails as a source-backed UE post-RVT adaptation.

LC and decal identities are sampled independently, then their effects are blended.
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
POST_ADAPTER_TAG = 'WT_GroundMicrodetailPostAdapter'
POST_ADAPTER_SCHEMA = 'wt_ground_microdetails_post_v2'
POST_INPUTS = ('BaseColor', 'BaseNormalWorld', 'BaseRoughness', 'BaseMetallic',
               'BaseMicrodetailId', 'BaseMicrodetailValid', 'DecalMask',
               'DecalIdPayload', 'World', 'BoundsMin', 'BoundsMax', 'Microdetails')


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
    scale = format(data['uv_scale_per_m'], '.17g')
    id_scale = format(data['id_count_scale'], '.17g')
    count = int(data['slot_count'])
    return ('const float slotCount=' + str(count) + '.0;\n'
            'const float sourceUvScale=' + scale + ';\n'
            'const float sourceIdCountScale=' + id_scale + ';\n') + r"""
const float clearValue=128.0/255.0;
float coverage=saturate((DecalMask-clearValue)/(1-clearValue));
float inside=step(BoundsMin.x,World.x)*step(BoundsMin.y,World.y)*step(World.x,BoundsMax.x)*step(World.y,BoundsMax.y);
coverage*=inside;
float3 baseNormal=normalize(BaseNormalWorld);
NormalOut=baseNormal;RoughnessOut=BaseRoughness;
MetallicOut=BaseMetallic;OpacityOut=1;
// Derivatives belong to the continuous world UV, before all validity branches.
float2 uv=World.xy*0.01*sourceUvScale;
float2 uvDdx=ddx(uv),uvDdy=ddy(uv);
float baseValid=saturate(BaseMicrodetailValid);
float baseIndex=clamp(BaseMicrodetailId*sourceIdCountScale,0,slotCount-1);
float decalValid=0,decalIndex=0;
if(coverage>0)
{
    // RVT writes attributes * Opacity and blends ONE, INVSRCALPHA.
    // Remove the clear contribution once; zero payload denotes no source ID.
    float payload=max((DecalIdPayload-clearValue*(1-coverage))/max(coverage,1.e-8),0);
    decalValid=saturate(payload*(slotCount+1));
    decalIndex=clamp(payload*(slotCount+1)-1,0,slotCount-1);
}
// The two surface identities remain separate throughout sampling.
float surfaceIndices[2]={baseIndex,decalIndex};
float surfaceStrengths[2]={(1-coverage)*baseValid,coverage*decalValid};
float totalStrength=surfaceStrengths[0]+surfaceStrengths[1];
if(totalStrength<=0) return BaseColor;
float neutralStrength=max(1-totalStrength,0);
float diffuseFactor=neutralStrength;
float roughnessResult=BaseRoughness*neutralStrength;
float3 normalResult=baseNormal*neutralStrength;
// At this shared world UV and gradient, equal integer slices are identical
// sample requests. Cache only texels, never the two surfaces' resulting IDs.
int cacheSlices[4]={-1,-1,-1,-1};
float4 cacheValues[4]={float4(0,0,0,0),float4(0,0,0,0),float4(0,0,0,0),float4(0,0,0,0)};
int cacheCount=0;
[unroll] for(int surface=0;surface<2;++surface)
{
    float strength=surfaceStrengths[surface];
    if(strength>0)
    {
        float index=surfaceIndices[surface];
        int lower=(int)floor(index),upper=min(lower+1,(int)slotCount-1);
        float part=frac(index);
        int slices[2]={lower,upper};
        float sliceWeights[2]={1-part,part};
        float4 micro=float4(0,0,0,0);
        [unroll] for(int endpoint=0;endpoint<2;++endpoint)
        {
            float sliceWeight=sliceWeights[endpoint];
            if(sliceWeight>0)
            {
                int slice=slices[endpoint];
                float4 value=float4(0,0,0,0);
                bool found=false;
                [unroll] for(int cached=0;cached<4;++cached)
                {
                    if(cached<cacheCount && cacheSlices[cached]==slice)
                    {
                        value=cacheValues[cached];found=true;
                    }
                }
                if(!found)
                {
                    value=Texture2DArraySampleGrad(Microdetails,MicrodetailsSampler,float3(uv,(float)slice),uvDdx,uvDdy);
                    cacheSlices[cacheCount]=slice;cacheValues[cacheCount]=value;++cacheCount;
                }
                micro+=value*sliceWeight;
            }
        }
        // Original dry helper: R changes diffuse/smoothness; AG is the normal.
        // Apply each source's floor/next result to the composed surface first.
        diffuseFactor+=strength*((micro.r*2)*(micro.r*2));
        float modifiedRoughness=1-saturate((1-BaseRoughness)*micro.r*2);
        roughnessResult+=strength*modifiedRoughness;
        float2 xy=micro.ag*2-1;
        float3 detailNormal=normalize(float3(xy,sqrt(saturate(1-dot(xy,xy)))));
        float3 t=baseNormal+float3(0,0,1),u=detailNormal*float3(-1,-1,1);
        float3 combined=normalize(t*(dot(t,u)/max(t.z,1.e-5))-u);
        normalResult+=strength*combined;
    }
}
// Blend effects with coverage, not the identities of different surfaces.
NormalOut=normalize(normalResult);
RoughnessOut=clamp(roughnessResult,0.04,1);
return max(BaseColor*diffuseFactor,0);
"""


def post_adapter_metadata(data, source_connected=True):
    """Stable contract for native graph/property readback, not a render verdict."""
    return json.dumps({
        'schema': POST_ADAPTER_SCHEMA,
        'source_definition_sha256': DEFINITION_SHA256,
        'source_connected': bool(source_connected),
        'hlsl_sha256': hashlib.sha256(shader_code(data).encode('utf-8')).hexdigest(),
        'inputs': list(POST_INPUTS),
        'source_id': 'cooked_A_unorm_times_original_id_count_scale',
        'id_count_scale': data['id_count_scale'],
        'uv_scale_per_m': data['uv_scale_per_m'],
        'interpolation': 'independent_floor_next_within_each_surface',
        'composition': 'effects_on_composed_surface_then_coverage_validity_blend',
        'source_weight': '(1-coverage)*saturate(BaseMicrodetailValid)',
        'decal_weight': 'coverage*decoded_decal_valid',
        'neutral_weight': '1-source_weight-decal_weight',
        'sample_cache_key': 'integer_slice_at_shared_world_uv_and_explicit_gradients',
        'texture_reads': {'maximum': 4, 'minimum_active': 1, 'inactive': 0,
                          'scope': 'logical_array_sample_requests_before_hardware_filter_taps'},
        'samplers': 1,
        'pbr_channels': 'R_diffuse_and_smoothness_AG_normal',
        'native_render_validation': 'required_separately',
    }, sort_keys=True, separators=(',', ':'))


def _source_microdetail_sockets(value):
    if value is None:
        return None
    _require(isinstance(value, dict) and set(value) == {'id', 'valid'},
             'source_microdetail requires both id and valid sockets')
    for key in ('id', 'valid'):
        socket = value[key]
        _require(isinstance(socket, (tuple, list)) and len(socket) == 2
                 and socket[0] is not None and isinstance(socket[1], str) and bool(socket[1]),
                 'source_microdetail ' + key + ' must name an actual expression output')
    return value


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


def author_post_layer(material,textures,surface,rvt,world,bounds_inputs,source_microdetail=None):
    source_microdetail = _source_microdetail_sockets(source_microdetail)
    import unreal
    import wt_materials as w
    data = definition(textures)
    array = ensure_array(textures)
    # Separate reroutes make each serialized output pin unambiguous to the native gate.
    def wire_output(source,pin):
        reroute=w._expr(material,unreal.MaterialExpressionReroute)
        w._wire(source,pin,reroute,'')
        return (reroute,'')
    if source_microdetail is None:
        zero = w._expr(material,unreal.MaterialExpressionConstant,r=0.0)
        source_id = source_valid = (zero,'')
    else:
        # _wire raises on an absent native output; real callers never fall back.
        source_id = wire_output(*source_microdetail['id'])
        source_valid = wire_output(*source_microdetail['valid'])
    inputs = {
        'BaseColor':(surface,''),'BaseNormalWorld':wire_output(surface,'NormalOut'),
        'BaseRoughness':wire_output(surface,'RoughnessOut'),'BaseMetallic':wire_output(surface,'MetallicOut'),
        'BaseMicrodetailId':source_id,'BaseMicrodetailValid':source_valid,
        'DecalMask':wire_output(rvt,'Mask'),'DecalIdPayload':wire_output(rvt,'Specular'),'World':(world,''),
        'BoundsMin':bounds_inputs['BoundsMin'],'BoundsMax':bounds_inputs['BoundsMax'],
        'Microdetails':(w._texture(material,'SourceGroundMicrodetails',array,False),'')}
    _require(tuple(inputs) == POST_INPUTS, 'post-layer input contract differs')
    node=w._custom(material,shader_code(data),inputs)
    unreal.EditorAssetLibrary.set_metadata_tag(
        material,POST_ADAPTER_TAG,post_adapter_metadata(data,source_microdetail is not None))
    material.set_editor_property('tangent_space_normal',False)
    return node
