"""Pure source contract and shader for Flanders' original global landclass colors.

The lmap packed weight atlas and class records are transported losslessly. This
adapter restores global macro colors; it does not implement Dagor microdetails.
"""
import hashlib
import json
import math
from pathlib import Path
import re
import struct

SCHEMA = 'wt_landclass_surface_v1'
PACKAGE_SCHEMA = 'wt_landclass_surface_package_v1'
PROOF_SCHEMA = 'wt_landclass_surface_verification_v1'
DAGOR_COMMIT = '9639942de5a8e5b01d91977f60ed82cda22d5f41'
LAND_MATERIAL_ID = 'WT_22356521c26047d92cb7'
CLASS_NAMES = ('normandy_fields_a', 'avg_vlaanderen_detailed_biome',
               'berlin_forest', 'sicily_city_tile', 'kursk_sand')
COLOR_NAMES = ('normandy_fields_a', 'avg_vlaanderen_detailed_biome_tex_d',
               'berlin_forest', 'sicily_small_city_tile', 'kursk_sand')
DATA_NAMES = ('WT_LandWeights', 'WT_LandCells0', 'WT_LandCells1')
TEXTURE_PARAMETERS = tuple('LC' + str(i) for i in range(5)) + DATA_NAMES


def _require(value, message):
    if not value:
        raise ValueError('source landclass: ' + message)


def _sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def digest(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def local_file(package, relative):
    _require(isinstance(relative, str) and relative, 'missing relative file')
    path = Path(relative)
    _require(not path.is_absolute() and '..' not in path.parts, 'nonlocal file path')
    path = (package / path).resolve(strict=True)
    _require(path.is_relative_to(package) and path.is_file(), 'file escapes package')
    return path


def validate_landclass_surface(package, source):
    """Check original level identity, byte proofs, textures and exact draw scope."""
    package = Path(package).resolve(strict=True)
    meshes = [m for m in source.get('meshes', []) if LAND_MATERIAL_ID in m['material_slots']]
    contract = source.get('source_landclass_surface')
    if not meshes and contract is None:
        return None  # Material-only fixtures have no scene binding.
    _require(isinstance(contract, dict), 'land_mesh is missing its global source binding')
    for key, expected in [('schema', SCHEMA), ('shader', 'land_mesh'),
                          ('material_id', LAND_MATERIAL_ID)]:
        _require(contract.get(key) == expected, 'unsupported ' + key)
    _require(sorted(m['id'] for m in meshes) == sorted(contract.get('mesh_ids', [])), 'receiver mesh set differs')
    _require(all(m['material_slots'] == [LAND_MATERIAL_ID] for m in meshes), 'receiver has unrelated material slots')
    path = local_file(package, contract.get('manifest_file'))
    _require(_sha(contract.get('manifest_sha256')) and digest(path) == contract['manifest_sha256'], 'manifest bytes differ')
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    _require(data.get('schema') == PACKAGE_SCHEMA, 'unsupported package schema')
    _require(data.get('source_sha256') == source.get('source_sha256') and _sha(data.get('source_lmap_sha256')), 'original level differs')
    _require(data.get('dagor_commit') == DAGOR_COMMIT, 'source shader revision differs')
    proof_file = local_file(package, data.get('verification_file'))
    _require(digest(proof_file) == data.get('verification_sha256'), 'verification bytes differ')
    proof = json.loads(proof_file.read_text(encoding='utf-8-sig'))
    _require(proof.get('schema') == PROOF_SCHEMA and proof.get('status') == 'passed', 'verification did not pass')
    for key in ('source_sha256', 'source_lmap_sha256', 'domain', 'atlas', 'classes', 'textures'):
        _require(proof.get(key) == data.get(key), 'proof/package mismatch: ' + key)
    for name in ('source_boundaries_exact', 'packed_records_roundtrip_exact',
                 'atlas_decoded_pixels_exact', 'weights_source_formula_checked'):
        _require(proof.get('checks', {}).get(name) is True, 'missing source check: ' + name)
    domain = data['domain']
    _require(domain == dict(cell_m=1024.0, grid_m=16.0, cells=[32, 32], origin_cells=[-16, -16]), 'unreviewed world mapping')
    atlas = data['atlas']
    _require(atlas.get('element_size') == 64 and atlas.get('page_size') == 68, 'source weight page layout differs')
    _require(atlas.get('pages_per_row') == 112 and atlas.get('page_count') == 448 and atlas.get('dimensions') == [7616, 272], 'source atlas dimensions differ')
    _require(_sha(atlas.get('ddsx_sha256')) and _sha(atlas.get('decoded_rgb_sha256')), 'missing original atlas hashes')
    classes = data['classes']
    _require(len(classes) == 5 and tuple(x['name'] for x in classes) == CLASS_NAMES, 'source class table differs')
    _require(tuple(x['texture_name'] for x in classes) == COLOR_NAMES, 'source color textures differ')
    for i, item in enumerate(classes):
        _require(item.get('id') == i and item.get('size_m') == [4096, 2048, 2048, 1024, 128][i], 'class identity/scale differs')
        _require(item.get('offset_m') == [0.0, 0.0], 'unreviewed class offset')
        _require(_sha(item.get('source_record_sha256')), 'missing source class record hash')
    textures = data['textures']
    _require(set(textures) == set(TEXTURE_PARAMETERS), 'texture set differs')
    for name, entry in textures.items():
        path = local_file(package, entry.get('file'))
        _require(_sha(entry.get('sha256')) and digest(path) == entry['sha256'], 'texture bytes differ: ' + name)
        header = path.read_bytes()[:33]
        _require(header[:8] == b'\x89PNG\r\n\x1a\n' and len(header) == 33, 'not a PNG: ' + name)
        _require(list(struct.unpack_from('>II', header, 16)) == entry.get('size'), 'texture dimensions differ: ' + name)
        is_data = name in DATA_NAMES
        _require(entry.get('srgb') is (not is_data), 'texture gamma differs: ' + name)
        _require(entry.get('nearest') is name.startswith('WT_LandCells'), 'texture filter differs: ' + name)
        _require(entry.get('no_mips') is is_data, 'texture mip contract differs: ' + name)
        _require(entry.get('address') == ('clamp' if is_data else 'wrap'), 'texture address mode differs: ' + name)
    _require(textures['WT_LandWeights']['size'] == atlas['dimensions'], 'weight PNG dimensions differ')
    _require(all(textures[k]['size'] == [32, 32] for k in DATA_NAMES[1:]), 'cell lookup dimensions differ')
    records = data.get('cells', [])
    _require(len(records) == 1024, 'cell count differs')
    for i, rec in enumerate(records):
        _require(rec.get('cell') == i and rec.get('xy') == [i % 32, i // 32], 'cell ordering differs')
        count = rec.get('blend_count')
        _require(type(count) is int and 1 <= count <= 4, 'unsupported original blend count')
        ids = rec.get('class_ids')
        _require(isinstance(ids, list) and len(ids) == 7 and len(set(ids[:count])) == count, 'invalid original class IDs')
        _require(all(type(x) is int and 0 <= x < 5 for x in ids[:count]) and ids[count:] == [255] * (7-count), 'class slots differ')
        page = rec.get('pages', [None])[0]
        _require(type(page) is int and 0 <= page < 448, 'page index exceeds atlas')
    _require(proof.get('cells_sha256') == hashlib.sha256(json.dumps(records, sort_keys=True, separators=(',', ':')).encode()).hexdigest(), 'cell proof differs')
    return data


LANDCLASS_CODE = r'''
// Original lmap packed atlas: 68px page, 2px border, 64px cell interior.
// UE absolute XY centimetres correspond to Dagor XZ metres.
float2 worldXZ=World.xy/100.0;
float2 cellFloat=worldXZ/LandGrid.x-LandAtlas2.yz;
float2 period=2.0*(LandGrid.yz-0.5*LandGrid.w/LandGrid.x);
float2 wrapped=cellFloat-period*floor(cellFloat/period);
float2 folded=min(wrapped,period-wrapped);
float2 cell=floor(folded);
float2 cellUV=frac(folded);
float2 lookupUV=(cell+0.5)/LandGrid.yz;
float4 meta=round(Texture2DSampleLevel(WT_LandCells0,WT_LandCells0Sampler,lookupUV,0)*255.0);
float4 more=round(Texture2DSampleLevel(WT_LandCells1,WT_LandCells1Sampler,lookupUV,0)*255.0);
int count=(int)meta.z;
int page=(int)(meta.x+256.0*meta.y);
float4 ids=float4(meta.w,more.xyz);
float4 weights=float4(1,0,0,0);
if(count>1){
    float2 pixel=float2(page%(int)LandAtlas.z,page/(int)LandAtlas.z)*LandAtlas.y+2.0+cellUV*LandAtlas.x;
    float2 atlasUV=pixel/float2(LandAtlas.w,LandAtlas2.x);
    float3 rgb=Texture2DSampleLevel(WT_LandWeights,WT_LandWeightsSampler,atlasUV,0).rgb;
    weights=float4(rgb,0)*float4(0<count-1,1<count-1,2<count-1,0);
    float last=saturate(1.0-dot(weights,float4(1,1,1,1)));
    weights+=last*float4(count==1,count==2,count==3,count==4);
}
float w0=dot(weights,float4(ids.x==0,ids.y==0,ids.z==0,ids.w==0));
float w1=dot(weights,float4(ids.x==1,ids.y==1,ids.z==1,ids.w==1));
float w2=dot(weights,float4(ids.x==2,ids.y==2,ids.z==2,ids.w==2));
float w3=dot(weights,float4(ids.x==3,ids.y==3,ids.z==3,ids.w==3));
float w4=dot(weights,float4(ids.x==4,ids.y==4,ids.z==4,ids.w==4));
float3 color=Texture2DSample(LC0,LC0Sampler,worldXZ*LCMap0.xy+LCMap0.zw).rgb*w0;
color+=Texture2DSample(LC1,LC1Sampler,worldXZ*LCMap1.xy+LCMap1.zw).rgb*w1;
color+=Texture2DSample(LC2,LC2Sampler,worldXZ*LCMap2.xy+LCMap2.zw).rgb*w2;
color+=Texture2DSample(LC3,LC3Sampler,worldXZ*LCMap3.xy+LCMap3.zw).rgb*w3;
color+=Texture2DSample(LC4,LC4Sampler,worldXZ*LCMap4.xy+LCMap4.zw).rgb*w4;
NormalOut=float3(0,0,1);RoughnessOut=0.9;MetallicOut=0;OpacityOut=1;
return color;
'''


def shader_vectors(data):
    domain, atlas = data['domain'], data['atlas']
    vectors = dict(LandGrid=(domain['cell_m'], *domain['cells'], domain['grid_m']),
                   LandAtlas=(atlas['element_size'], atlas['page_size'], atlas['pages_per_row'], atlas['dimensions'][0]),
                   LandAtlas2=(atlas['dimensions'][1], *domain['origin_cells'], 0))
    for item in data['classes']:
        tile = 1.0 / item['size_m']
        offset = [-item['offset_m'][i]-domain['origin_cells'][i]*domain['cell_m'] for i in range(2)]
        vectors['LCMap' + str(item['id'])] = (tile, -tile, offset[0]*tile, -offset[1]*tile)
    return vectors
