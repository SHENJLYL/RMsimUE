"""Source-bound LC reflectance companion, shared sampler contract and native array."""
import hashlib
import json
from pathlib import Path
import struct

DEFINITION_FILE = 'terrain-reflectance/definition.json'
ARRAY_NAME = 'WT_LandDetailReflectance'
ADAPTER_TAG = 'WT_LandDetailReflectanceAdapter'
CHANNELS = {'R':'smoothness','G':'reflectance','B':'ambient_occlusion','A':'microdetail_id_unorm'}
SWIZZLE_SHA256 = 'f3fde1e24431dd191593fc737444ece16c02f776494da8e675646a61a3761c77'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(value, message):
    if not value:raise ValueError('LC reflectance: '+message)


def _file(base, relative, expected):
    p=(base/relative).resolve(strict=True)
    require(p.is_relative_to(base.resolve()) and digest(p)==expected,'bound file differs: '+relative)
    return p


def validate(package, detail):
    package=Path(package).resolve(strict=True)
    path=package/DEFINITION_FILE;base=path.parent
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    require(data.get('schema')=='wt_lc_reflectance_v1' and data.get('status')=='prepared', 'unsupported companion')
    require(data['source_sha256']==detail['source_sha256'] and data['dagor_commit']==detail['dagor_commit'] and
            data['landdetail_definition_sha256']==digest(package/'terrain-details/definition.json') and
            data['raw_landclass_sha256']==detail['source_definition']['sha256'], 'original landclass identity differs')
    require(data['channels']==CHANNELS and data['swizzle_proof_sha256']==SWIZZLE_SHA256,'cooked channel interpretation differs')
    proof_file=_file(base,data['proof_file'],data['proof_sha256'])
    proof=json.loads(proof_file.read_text(encoding='utf-8-sig'))
    require(proof['status']=='passed' and proof['texture_count']==23 and proof['biome_count']==65 and
            proof['original_png_bytes_unchanged'] and proof['decoded_highest_mip_matches_original'], 'source preparation is incomplete')
    swizzle=_file(base,data['swizzle_proof_file'],SWIZZLE_SHA256)
    require(json.loads(swizzle.read_text(encoding='utf-8-sig'))['status']=='source_verified','swizzle consumer is unverified')
    _file(base,data['source_audit_file'],data['source_audit_sha256'])
    for ref in data['source_references']:_file(base,ref['file'],ref['sha256'])
    micro_file=package/'ground-microdetails/definition.json'
    require(digest(micro_file)==data['microdetail_definition_sha256'],'original microdetail level configuration differs')
    micro=json.loads(micro_file.read_text(encoding='utf-8-sig'))
    require(data['microdetail_coloring']==[s['params'][0] for s in micro['slots']] and len(micro['slots'])==12 and
            data['id_count_scale']==micro['id_count_scale'],'microdetail coloring/ID conversion differs')
    table=detail['table'];defs=table['definitions']
    names=sorted({d['reflectance'] for d in defs.values() if d.get('reflectance')})
    require(data['reflectance_names']==names and len(names)==23 and set(data['textures'])==set(names),'source array slice inventory differs')
    pairs=[]
    for i,name in enumerate(table['albedo_names']):
        refs={d['reflectance'] for d in defs.values() if d.get('albedo')==name}
        require(len(refs)==1,'ambiguous original albedo/reflectance pair')
        ref=refs.pop();pairs.append(dict(albedo_slice=i,albedo=name,reflectance_slice=names.index(ref),reflectance=ref))
    require(data['albedo_to_reflectance']==pairs,'reflectance-to-albedo slice mapping differs')
    biomes=[dict(id=b['id'],reflectance_slices=[names.index(l['reflectance']) for l in b['layers']],
                 grass_microdetail_removal_bits=b['grass_microdetail_removal_bits']) for b in table['biomes']]
    require(data['biomes']==biomes,'source 65-biome reflection mapping differs')
    rows={r['name']:r for r in proof['textures']}
    require(rows==data['textures'],'texture contract differs from prepared proof')
    for name,entry in data['textures'].items():
        file=_file(base,entry['file'],entry['sha256']);header=file.read_bytes()[:33]
        require(entry['size']==[1024,1024] and entry['srgb'] is False and header[:8]==b'\x89PNG\r\n\x1a\n' and
                list(struct.unpack_from('>II',header,16))==[1024,1024] and header[24:26]==bytes([8,6]),
                'original reflection image format differs: '+name)
    return data


def adapter_metadata(data):
    return json.dumps(dict(schema='wt_lc_reflectance_adapter_v1',channels=CHANNELS,
        source_swizzle_sha256=data['swizzle_proof_sha256'],
        sample_reuse_key='reflectance_slice_and_all_four_rotation_components_within_one_layer',
        sampler='DetailsSampler shared with albedo; no ReflectanceSampler read',
        max_reflectance_reads=8,min_reflectance_reads=2,max_total_lc_array_reads=32,min_total_lc_array_reads=8,
        additional_sampler_states=0,filtering='rotated_continuous_SampleGrad',
        roughness='1-minus-source-R',selfcolor='per-neighbor original coloring parameter interpolation before final source weights',
        microdetail_id='source A from maximum-weight neighbor; maximum-weight layer; never average distinct IDs',
        neighbor_tie='first',layer_tie='second',unconnected_channels=['G reflectance','B ambient occlusion'],
        mip_policy='UE SimpleAverage derived from exact original highest mip'),sort_keys=True,separators=(',',':'))


def author_array(textures, data):
    import unreal
    import wt_materials as w
    base=textures.package/Path(DEFINITION_FILE).parent
    for name,entry in data['textures'].items():
        if name in textures.source:require(textures.source[name]['sha256']==entry['sha256'],'source texture inventory conflicts')
        textures.source[name]=dict(entry,file=str(base/entry['file']))
    slices=[textures.get(name,False) for name in data['reflectance_names']]
    for texture,name in zip(slices,data['reflectance_names']):
        unreal.EditorAssetLibrary.set_metadata_tag(
            texture,'WT_LandDetailReflectancePNG_SHA256',data['textures'][name]['sha256'])
        require(unreal.EditorAssetLibrary.save_loaded_asset(texture),
                'native reflectance source texture metadata save failed: '+name)
    folder=textures.folder+'/Textures';array=w._load_existing(folder+'/'+ARRAY_NAME)
    if array is None:
        unreal.SystemLibrary.execute_console_command(None,'r.AllowTexture2DArrayCreation 1')
        array=unreal.AssetToolsHelpers.get_asset_tools().create_asset(ARRAY_NAME,folder,unreal.Texture2DArray,unreal.Texture2DArrayFactory())
    require(isinstance(array,unreal.Texture2DArray),'native reflectance array creation failed')
    expected=[t.get_path_name() for t in slices]
    actual=[t.get_path_name() if t else None for t in array.get_editor_property('source_textures')]
    if actual!=expected:array.set_editor_property('source_textures',slices)
    for key,value in dict(srgb=False,compression_settings=unreal.TextureCompressionSettings.TC_VECTOR_DISPLACEMENTMAP,
        address_x=unreal.TextureAddress.TA_WRAP,address_y=unreal.TextureAddress.TA_WRAP,address_z=unreal.TextureAddress.TA_CLAMP,
        mip_gen_settings=unreal.TextureMipGenSettings.TMGS_SIMPLE_AVERAGE,lod_bias=0,max_texture_size=0,never_stream=True).items():
        array.set_editor_property(key,value)
    unreal.EditorAssetLibrary.set_metadata_tag(array,'WT_SourceDefinitionSHA256',digest(textures.package/DEFINITION_FILE))
    unreal.SystemLibrary.execute_console_command(None,'Editor.AsyncTextureCompilationFinishAll')
    native=json.loads(unreal.MapImportLibrary.describe_map_texture_array(array))
    require((native.get('width'),native.get('height'),native.get('slices'),native.get('mips'))==(1024,1024,23,11),
            'native reflectance dimensions or complete derived mip chain differ: '+json.dumps(native))
    require(native.get('source_textures')==expected,'native reflection source slice order differs')
    require(unreal.EditorAssetLibrary.save_loaded_asset(array),'native reflectance array save failed')
    return array
