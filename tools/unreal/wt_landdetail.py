"""Source-bound Flanders indexed landclass detail data and UE graph adapter.

The original 65-biome table selects two of 23 1024-square albedo/height textures.
Source scales, four-neighbor height blending, noise and flow rotation are retained.
The source resource has no normal texture fields. UE shading normals are an
explicit local alpha-gradient adaptation: each fixed slice/rotation is sampled
before final source weights blend the resulting normals. Source height_scale and
normal_scale remain adapter multipliers; they do not establish metres of height.
Original cooked reflectance, per-neighbor self-color and winning microdetail IDs
are restored from a separately bound source companion. AO/reflectance outputs and
wetness remain outside the current UE material connection.
"""
import hashlib
import json
import math
from pathlib import Path
import struct

SCHEMA = 'wt_indexed_landdetail_v1'
DAGOR_COMMIT = '9639942de5a8e5b01d91977f60ed82cda22d5f41'
SOURCE_SHA256 = '427c9b06b62af0635fa08f2d6238211b9850caf7717a0773f186e1835be63b8f'
RESOURCE_SHA256 = '8cf1aa8f23f927e07c5034bc91d485c35102bc9d46655d458126da30ecbb6b80'
SOURCE_NAME = 'avg_vlaanderen_detailed_biome'
DEFINITION_FILE = 'terrain-details/definition.json'
ARRAY_NAME = 'WT_LandDetailAlbedo'
INDEX_NAME = 'avg_vlaanderen_detailed_biome_tex_b'
MACRO_NAME = 'avg_vlaanderen_detailed_biome_tex_d'
FLOW_NAME = 'avg_vlaanderen_detailed_biome_tex_f'
NORMAL_ADAPTER_TAG = 'WT_LandDetailNormalAdapter'
NORMAL_ADAPTER = {
    'schema': 'wt_lc1_local_alpha_normal_v3',
    'kind': 'ue_fixed_slice_rotation_alpha_gradient',
    'derivative_basis': 'world_position_before_categorical_selection',
    'sample_offsets': 'same_slice_rotation_explicit_gradients_plus_two_continuous_uv_offsets',
    'filtering': 'array_sample_grad_rotated_continuous_gradients',
    'normal_blending': 'unit_normals_then_source_final_pow4_weights_both_levels',
    'weight_derivatives': False,
    'source_normal_binding': None,
    'height_scale_domain': 'ue_adapter_multiplier_not_source_metres',
    'sample_reuse_key': 'exact_slice_and_all_four_rotation_components_within_one_layer',
    'cached_values': 'rgba_and_two_raw_alpha_differences_before_per_neighbour_strength',
    'max_base_array_reads': 8,
    'max_local_alpha_array_reads': 16,
    'max_array_reads_per_pixel': 24,
    'min_array_reads_per_pixel': 6,
    'array_reads_per_distinct_key_per_layer': 3,
    'dynamic_reads': 'three_times_sum_of_distinct_slice_rotation_keys_in_two_layers',
    'additional_samplers': 0,
}


def normal_adapter_metadata():
    return json.dumps(NORMAL_ADAPTER,sort_keys=True,separators=(',',':'))


def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def _require(value, message):
    if not value:
        raise ValueError('land detail: ' + message)


def _params(block):
    # Dagor DataBlock lookup returns the first matching parameter.
    result = {}
    for item in block['params']:
        result.setdefault(item['name'], item['value'])
    return result


def source_table(raw):
    _require(raw.get('schema') == 'wt_landclass_rodata_v1', 'unsupported decoded resource')
    identity = raw['source']
    _require(identity['name'] == SOURCE_NAME and identity['resource_sha256'] == RESOURCE_SHA256,
             'original Flanders GRP resource identity differs')
    detail = next(x for x in raw['root']['blocks'] if x['name'] == 'detail')
    _require(_params(detail) == {'texture': MACRO_NAME, 'shader': 'landmesh_indexed_landclass', 'size': [2048.0,2048.0]},
             'unreviewed source class scale/shader')
    source_params = _params(next(x for x in detail['blocks'] if x['name'] == 'landClassParams'))
    source_textures = _params(next(x for x in detail['blocks'] if x['name'] == 'landClassTextures'))
    _require(source_textures['indices'] == INDEX_NAME and source_textures['flowmap'] == FLOW_NAME,
             'original index/flow resource differs')
    entries = next(x for x in detail['blocks'] if x['name'] == 'details')['blocks']
    scheme = _params(next(x for x in entries if x['name'] == 'scheme'))
    definitions = {x['name']: _params(x) for x in entries if x['name'] not in ('scheme','detail')}
    _require(all(not x.get('normal') for x in definitions.values()), 'normal texture requires a source channel adapter')
    names = sorted({x.get('albedo') for x in definitions.values()} - {None, ''})
    _require(len(names) == 23, 'original 23 albedo/height textures differ')
    biomes = []
    for block in entries:
        if block['name'] != 'detail':
            continue
        values = dict(scheme, **_params(block))
        layers = []
        for i, key in enumerate(('texture','second')):
            definition = definitions[values[key]]
            _require(definition['albedo'] in names, 'empty source detail layer')
            layers.append(dict(name=values[key], slice=names.index(definition['albedo']),
                               albedo=definition['albedo'], reflectance=definition.get('reflectance'),
                               height_scale=float(values['height_scale'][i]),
                               invert_height=float(values['invert_height'][i]),
                               normal_scale=float(values['normal_scale'][i]),
                               weight_mul=float(values['detail_weights_mul'][i])))
        biomes.append(dict(id=len(biomes), layers=layers, random_flowmap=values['random_flowmap'],
                           grass_microdetail_removal_bits=values['grass_microdetail_removal_bits']))
    _require(len(biomes) == 65, 'original 65 biome records differ')
    for item in biomes:
        for layer in item['layers']:
            _require(all(math.isfinite(layer[k]) for k in ('height_scale','invert_height','normal_scale','weight_mul')),
                     'nonfinite detail parameter')
            _require(layer['invert_height'] in (0,1) and layer['weight_mul'] >= 0, 'unsupported source weights')
    return dict(source_parameters=source_params, source_textures=source_textures,
                definitions=definitions, albedo_names=names, biomes=biomes)


def validate(package, source):
    package = Path(package).resolve(strict=True)
    filename = package / DEFINITION_FILE
    _require(source.get('source_sha256') == SOURCE_SHA256, 'original level identity differs')
    _require(filename.is_file(), 'prepare the original indexed landclass details first')
    data = json.loads(filename.read_text(encoding='utf-8-sig'))
    _require(data.get('schema') == SCHEMA and data.get('source_sha256') == SOURCE_SHA256
             and data.get('dagor_commit') == DAGOR_COMMIT, 'source definition identity differs')
    original = (package/data['source_definition']['file']).resolve(strict=True)
    _require(original.is_relative_to(package) and digest(original) == data['source_definition']['sha256'],
             'source resource JSON bytes differ')
    table = source_table(json.loads(original.read_text(encoding='utf-8-sig')))
    _require(data['table'] == table, 'prepared biome table differs from original resource')
    entries = data['textures']
    _require(set(entries) == set(table['albedo_names']) | {INDEX_NAME,MACRO_NAME,FLOW_NAME},
             'source texture set differs')
    for name, entry in entries.items():
        path = (package / entry['file']).resolve(strict=True)
        _require(path.is_relative_to(package) and digest(path) == entry['sha256'], 'source texture bytes differ: '+name)
        header = path.read_bytes()[:33]
        _require(header[:8] == b'\x89PNG\r\n\x1a\n'
                 and list(struct.unpack_from('>II',header,16)) == entry['size'], 'PNG dimensions differ')
        expected = [4096,4096] if name in (INDEX_NAME,MACRO_NAME) else [512,512] if name==FLOW_NAME else [1024,1024]
        _require(entry['size'] == expected, 'unreviewed source texture scale: '+name)
    proof_path=(package/data['verification_file']).resolve(strict=True)
    _require(proof_path.is_relative_to(package) and digest(proof_path)==data['verification_sha256'], 'source proof bytes differ')
    proof=json.loads(proof_path.read_text(encoding='utf-8-sig'))
    _require(proof.get('status')=='passed' and proof.get('source_sha256')==SOURCE_SHA256
             and proof.get('resource_sha256')==RESOURCE_SHA256
             and proof.get('albedo_count')==23 and proof.get('biome_count')==65,
             'source proof did not pass')
    import wt_lc_reflectance
    data['reflectance'] = wt_lc_reflectance.validate(package, data)
    return data


# Wombat value noise, retained from Dagor's noise/Value2D.hlsl.
# Original attribution: Brian Sharpe, https://github.com/BrianSharpe/Wombat.
NOISE_CODE = r'''
struct WTDetailMath {
 float noise2(float2 P) {
  float2 Pi=floor(P),Pf=P-Pi;
  float4 Pt=float4(Pi,Pi+1);
  Pt-=floor(Pt*(1.0/71.0))*71.0;
  Pt+=float2(26,161).xyxy;Pt*=Pt;Pt=Pt.xzxz*Pt.yyww;
  float4 hash=frac(Pt*(1.0/951.135664));
  float2 b=Pf*Pf*Pf*(Pf*(Pf*6-15)+10);
  float4 b2=float4(b,1-b);return dot(hash,b2.zxzx*b2.wwyy);
 }
 float2 rotate(float2 value,float4 rot) { return value.x*rot.xz+value.y*rot.yw; }
};
WTDetailMath util;
'''


def shader_code(data):
    table=data['table']; biomes=table['biomes']; p=table['source_parameters']
    reflection=data['reflectance']
    def floats(value):
        return ','.join(format(float(x),'.10g') for x in value)
    def array(name, rows):
        return 'const float4 '+name+'[65]={'+','.join('float4('+floats(row)+')' for row in rows)+'};\n'
    constants = array('SliceHeight', [(b['layers'][0]['slice'],b['layers'][1]['slice'],
                                      b['layers'][0]['height_scale'],b['layers'][1]['height_scale']) for b in biomes])
    constants += array('InvertNormal', [(b['layers'][0]['invert_height'],b['layers'][1]['invert_height'],
                                        b['layers'][0]['normal_scale'],b['layers'][1]['normal_scale']) for b in biomes])
    constants += array('WeightRotation', [(b['layers'][0]['weight_mul'],b['layers'][1]['weight_mul'],
                                          b['random_flowmap'][0],b['random_flowmap'][1]) for b in biomes])
    constants += 'const float2 ReflectanceSlice[65]={'+','.join(
        'float2('+floats(row['reflectance_slices'])+')' for row in reflection['biomes'])+'};\n'
    constants += 'const float MicroColoring[12]={'+floats(reflection['microdetail_coloring'])+'};\n'
    constants += 'const float microIdScale='+floats([reflection['id_count_scale']])+';\n'
    constants += 'const float2 tileMeters=float2('+floats([p['detailMul1'],p['detailMul2']])+');\n'
    constants += 'const float rotatePeriod='+floats([p['randomRotatePeriod']])+';\n'
    constants += 'const float variationMeters='+floats([p['randomVariationPeriod']])+';\n'
    return NOISE_CODE + constants + r'''
float2 worldMeters=World.xy/100.0;
// Compute the continuous geometric basis before categorical ID/rotation selection.
float3 geometricNormal=normalize(VertexNormalWorld);
float3 worldDdx=ddx(World/100.0),worldDdy=ddy(World/100.0);
// Calculate the continuous texture footprint before categorical selection.
// Rotate both gradients with each fixed neighbour; retain anisotropic filtering.
float2 layerUV[2],layerUVDdx[2],layerUVDdy[2];
[unroll] for(int layer=0;layer<2;layer++){
 float2 tc=worldMeters/tileMeters[layer];
 if(layer==1){float a=13.1*3.14159265359/180;tc=float2(tc.x*cos(a)-tc.y*sin(a),tc.x*sin(a)+tc.y*cos(a));}
 layerUV[layer]=tc;layerUVDdx[layer]=ddx(tc);layerUVDdy[layer]=ddy(tc);
}
float3 gradientR1=cross(worldDdy,geometricNormal),gradientR2=cross(geometricNormal,worldDdx);
float gradientDet=dot(worldDdx,gradientR1);
float2 mapUV=float2(worldMeters.x,-worldMeters.y)/2048.0;
float3 macro=Texture2DSample(Macro,MacroSampler,mapUV).rgb;
MicrodetailIdOut=0;MicrodetailValidOut=0;
if(any(worldMeters<0) || any(worldMeters>2048)){
 NormalOut=normalize(VertexNormalWorld);RoughnessOut=0.9;MetallicOut=0;OpacityOut=1;return macro;
}
float2 imageSize=float2(4096,4096), invSize=1/imageSize;
float2 corner=mapUV*imageSize-0.5;
float2 fraction=frac(corner);
float4 bw=sqrt(float4(fraction,1-fraction));
float4 baseWeights=bw.zxxz*bw.yyww;
float2 cellCenter=(floor(corner)+0.5)*invSize;
float2 offsets[4]={float2(0,1),float2(1,1),float2(1,0),float2(0,0)};
int ids[4];float4 rotations[4];
[unroll] for(int k=0;k<4;k++){
 float2 at=cellCenter+offsets[k]*invSize;
 // Categorical IDs are always point-sampled from mip 0, never averaged as colors.
 ids[k]=min(64,(int)floor(Texture2DSampleLevel(Index,IndexSampler,at,0).r*255+0.1));
 float amount=WeightRotation[ids[k]].z;
 float angle=floor(util.noise2(at*amount*rotatePeriod)*128)/128*6.28318530718;
 float2 sc=float2(sin(angle),cos(angle));
 if(amount<0)sc=Texture2DSampleLevel(Flow,FlowSampler,at,0).rg*(-2)+1;
 rotations[k]=float4(sc.y,sc.x,-sc.x,sc.y);
}
float4 layerDiffuse[2];float3 layerNormal[2];float layerMultiplier[2];
float layerSmoothness[2],layerMicroId[2];
[unroll] for(int layer=0;layer<2;layer++){
 float2 tc=layerUV[layer],tcDdx=layerUVDdx[layer],tcDdy=layerUVDdy[layer];
 float4 samples[4];float2 alphaDelta[4];float4 heightWeights;float3 localNormals[4];
 layerMultiplier[layer]=0;
 [unroll]for(int k=0;k<4;k++){
  int id=ids[k];float slice=SliceHeight[id][layer];
  // The layer UV and its continuous gradients are shared. Equal slice AND all
  // four rotation entries therefore select exactly the same three samples.
  int previous=k;
  [unroll]for(int j=0;j<4;j++){
   if(j<k){
    if(slice==SliceHeight[ids[j]][layer] && all(rotations[k]==rotations[j]))
     previous=min(previous,j);
   }
  }
  [branch]if(previous<k){
   samples[k]=samples[previous];alphaDelta[k]=alphaDelta[previous];
  }else{
   float2 localUV=util.rotate(tc,rotations[k]);
   float2 localDx=util.rotate(tcDdx,rotations[k]),localDy=util.rotate(tcDdy,rotations[k]);
   samples[k]=Texture2DArraySampleGrad(Details,DetailsSampler,float3(localUV,slice),localDx,localDy);
   // Fixed identity and footprint on both local alpha differences. No lane's
   // categorical ID, rotation or blend weight participates in a derivative.
   float alphaDx=Texture2DArraySampleGrad(Details,DetailsSampler,
       float3(localUV+localDx,slice),localDx,localDy).a;
   float alphaDy=Texture2DArraySampleGrad(Details,DetailsSampler,
       float3(localUV+localDy,slice),localDx,localDy).a;
   alphaDelta[k]=float2(alphaDx-samples[k].a,alphaDy-samples[k].a);
  }
  float alpha=samples[k].a;
  // Strength and inversion belong to each biome, even when samples are shared.
  float heightMultiplier=SliceHeight[id][layer+2]*InvertNormal[id][layer+2];
  float3 localGradient=sign(gradientDet)*heightMultiplier*
      (alphaDelta[k].x*gradientR1+alphaDelta[k].y*gradientR2);
  localNormals[k]=abs(gradientDet)>1.e-10 ?
      normalize(abs(gradientDet)*geometricNormal-localGradient) : geometricNormal;
  heightWeights[k]=(InvertNormal[id][layer]>0.5 ? 1-alpha : alpha)*baseWeights[k]+baseWeights[k]*0.0001;
  layerMultiplier[layer]+=WeightRotation[id][layer]*baseWeights[k];
 }
 heightWeights/=max(dot(heightWeights,1),1.e-8);
 float4 weights=heightWeights*heightWeights;weights*=weights;weights=saturate(weights-2.e-10);
 weights/=max(dot(weights,1),1.e-8);
 layerDiffuse[layer]=0;layerNormal[layer]=0;layerSmoothness[layer]=0;layerMicroId[layer]=0;
 float4 reflectionSamples[4];float maxMicroWeight=0;
 [unroll]for(int k=0;k<4;k++){
  reflectionSamples[k]=0;
  [branch]if(weights[k]>0){
   float reflectionSlice=ReflectanceSlice[ids[k]][layer];int previous=k;
   // Reflectance has its own full identity key. Albedo reuse cannot stand in
   // for a potentially different reflectance slice or rotation.
   [unroll]for(int j=0;j<4;j++){
    if(j<k && weights[j]>0){
     if(reflectionSlice==ReflectanceSlice[ids[j]][layer] && all(rotations[k]==rotations[j]))
      previous=min(previous,j);
    }
   }
   [branch]if(previous<k) reflectionSamples[k]=reflectionSamples[previous];
   else{
    float2 localUV=util.rotate(tc,rotations[k]);
    float2 localDx=util.rotate(tcDdx,rotations[k]),localDy=util.rotate(tcDdy,rotations[k]);
    // Same wrap/filter/footprint as albedo, sharing its sampler state.
    reflectionSamples[k]=Texture2DArraySampleGrad(Reflectance,DetailsSampler,
        float3(localUV,reflectionSlice),localDx,localDy);
   }
   float4 r=reflectionSamples[k];
   float arrayNoFloat=r.a*microIdScale;int ci=min(11,(int)floor(arrayNoFloat));
   float coloring=lerp(MicroColoring[ci],MicroColoring[min(ci+1,11)],frac(arrayNoFloat));
   float3 sourceColor=lerp(float3(1,1,1),macro,coloring)*samples[k].rgb;
   layerDiffuse[layer]+=float4(sourceColor,samples[k].a)*weights[k];
   layerSmoothness[layer]+=r.r*weights[k];
   // Source make_clipmap selects a winning ID. It never blends IDs with the
   // normal/roughness weights. Strict > preserves first-neighbor ties.
   if(weights[k]>maxMicroWeight){layerMicroId[layer]=r.a;maxMicroWeight=weights[k];}
  }
  layerNormal[layer]+=localNormals[k]*weights[k];
 }
}
float randomWeight=util.noise2(worldMeters/variationMeters);
float2 split=float2(randomWeight,1-randomWeight);
float2 layerWeights=float2(layerDiffuse[0].a,layerDiffuse[1].a)*split*float2(layerMultiplier[0],layerMultiplier[1])+split*0.0001;
layerWeights/=max(dot(layerWeights,1),1.e-8);
layerWeights*=layerWeights;layerWeights*=layerWeights;layerWeights=saturate(layerWeights-2.e-10);
layerWeights/=max(dot(layerWeights,1),1.e-8);
float3 detail=layerDiffuse[0].rgb*layerWeights.x+layerDiffuse[1].rgb*layerWeights.y;
// Only normals are mixed. No derivative of height weights or categorical IDs.
// Source height_scale/normal_scale are explicit UE adapter multipliers, not metres.
NormalOut=normalize(layerNormal[0]*layerWeights.x+layerNormal[1]*layerWeights.y);
RoughnessOut=saturate(1-dot(float2(layerSmoothness[0],layerSmoothness[1]),layerWeights));
MetallicOut=0;OpacityOut=1;
// Source second layer wins an equal-weight tie. Raw A is retained for the
// original floor/next microdetail interpolation after the surface composition.
MicrodetailIdOut=layerWeights.x>layerWeights.y ? layerMicroId[0] : layerMicroId[1];
MicrodetailValidOut=1;
// Original G reflectance / B AO are preserved in the companion but not connected.
return max(detail*4.59479341998814,0);
'''


def author_surface(material, textures):
    import unreal
    import wt_materials as w
    source=json.loads((textures.package/'scene-source.json').read_text(encoding='utf-8-sig'))
    data=validate(textures.package,source)
    for name,entry in data['textures'].items():
        if name in textures.source and textures.source[name]['sha256'] != entry['sha256']:
            raise ValueError('land detail PNG differs from original inventory')
        textures.source[name]=dict(entry,file=str(textures.package/entry['file']))
    slices=[textures.get(name,True) for name in data['table']['albedo_names']]
    path=textures.folder+'/Textures/'+ARRAY_NAME
    array=w._load_existing(path)
    if array is None:
        unreal.SystemLibrary.execute_console_command(None,'r.AllowTexture2DArrayCreation 1')
        factory=unreal.Texture2DArrayFactory()
        # InitialTextures is not exposed to Python. The empty native factory is valid;
        # assigning editable SourceTextures below calls PostEditChangeProperty and
        # its real UpdateSourceFromSourceTextures(false) source-data rebuild.
        array=unreal.AssetToolsHelpers.get_asset_tools().create_asset(ARRAY_NAME,textures.folder+'/Textures',unreal.Texture2DArray,factory)
    elif not isinstance(array,unreal.Texture2DArray):
        raise RuntimeError('source detail array asset has the wrong class')
    if array is None:
        raise RuntimeError('native source detail array creation failed')
    existing=list(array.get_editor_property('source_textures'))
    if [x.get_path_name() if x else None for x in existing] != [x.get_path_name() for x in slices]:
        # UE5.8 PostEditChangeProperty rebuilds the real TextureSource on this property.
        array.set_editor_property('source_textures',slices)
    array.set_editor_property('srgb',True)
    array.set_editor_property('compression_settings',unreal.TextureCompressionSettings.TC_DEFAULT)
    array.set_editor_property('address_x',unreal.TextureAddress.TA_WRAP)
    array.set_editor_property('address_y',unreal.TextureAddress.TA_WRAP)
    array.set_editor_property('address_z',unreal.TextureAddress.TA_CLAMP)
    array.set_editor_property('mip_gen_settings',unreal.TextureMipGenSettings.TMGS_SIMPLE_AVERAGE)
    array.set_editor_property('lod_bias',0)
    array.set_editor_property('max_texture_size',0)
    array.set_editor_property('never_stream',True)
    unreal.EditorAssetLibrary.set_metadata_tag(array,'WT_SourceDefinitionSHA256',digest(textures.package/DEFINITION_FILE))
    unreal.SystemLibrary.execute_console_command(None,'Editor.AsyncTextureCompilationFinishAll')
    actual=json.loads(unreal.MapImportLibrary.describe_map_texture_array(array))
    if (actual.get('width'),actual.get('height'),actual.get('slices')) != (1024,1024,23):
        raise RuntimeError('native source detail texture array dimensions differ: '+json.dumps(actual))
    if actual.get('source_textures') != [x.get_path_name() for x in slices] or actual.get('mips',0)<2:
        raise RuntimeError('native source detail array slices/mips differ')
    if not unreal.EditorAssetLibrary.save_loaded_asset(array):
        raise RuntimeError('could not save source detail texture array')
    index=textures.get(INDEX_NAME,False,nearest=True)
    index.set_editor_property('mip_gen_settings',unreal.TextureMipGenSettings.TMGS_NO_MIPMAPS)
    index.set_editor_property('never_stream',True)
    index.set_editor_property('lod_bias',0)
    index.set_editor_property('max_texture_size',0)
    index.set_editor_property('address_x',unreal.TextureAddress.TA_WRAP)
    index.set_editor_property('address_y',unreal.TextureAddress.TA_WRAP)
    unreal.EditorAssetLibrary.save_loaded_asset(index)
    import wt_lc_reflectance
    reflection_array=wt_lc_reflectance.author_array(textures,data['reflectance'])
    inputs={
        'World':(w._expr(material,unreal.MaterialExpressionWorldPosition),''),
        'VertexNormalWorld':(w._expr(material,unreal.MaterialExpressionVertexNormalWS),''),
        'Macro':(w._texture(material,'DetailMacro',textures.get(MACRO_NAME,True),True),''),
        'Index':(w._texture(material,'DetailIndex',index,False),''),
        'Flow':(w._texture(material,'DetailFlow',textures.get(FLOW_NAME,False),False),''),
        'Details':(w._texture(material,'SourceDetails',array,True),''),
        'Reflectance':(w._texture(material,'SourceReflectance',reflection_array,False),''),
    }
    node=w._custom(material,shader_code(data),inputs,additional_outputs=[
        ('MicrodetailIdOut',unreal.CustomMaterialOutputType.CMOT_FLOAT1),
        ('MicrodetailValidOut',unreal.CustomMaterialOutputType.CMOT_FLOAT1)])
    unreal.EditorAssetLibrary.set_metadata_tag(material,wt_lc_reflectance.ADAPTER_TAG,
        wt_lc_reflectance.adapter_metadata(data['reflectance']))
    unreal.EditorAssetLibrary.set_metadata_tag(material,NORMAL_ADAPTER_TAG,normal_adapter_metadata())
    material.set_editor_property('tangent_space_normal',False)
    return node,data
