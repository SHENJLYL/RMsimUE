"""Source-bound Flanders height-decal reflectance and alpha-height adapter.

The shipped source resources use d/null/r/null; the pinned public shader uses
separate normal/reflection slots. Mapping T2 _r to the published reflection
channels is an explicit resource-layout adaptation. No source PNG/UV is changed.
"""
import json
import math
import struct

SCHEMA = 'wt_height_decal_surface_v3'
DAGOR_COMMIT = '9639942de5a8e5b01d91977f60ed82cda22d5f41'
SHADER = 'land_mesh_height_decal'
SOURCE_BUMP_SCALE = 0.08
# UE visual adaptation of the source UV-height gradient; not a source metre value.
HEIGHT_NORMAL_STRENGTH = 0.2
HEIGHT_NORMAL_METADATA_TAG = 'WT_HeightNormalAdapter'
SLOT_COUNT = 12
ID_COUNT_SCALE = struct.unpack('<f', struct.pack('<f', 255 / (255 // (SLOT_COUNT - 1))))[0]
HEIGHT_SOURCE = (SOURCE_BUMP_SCALE, ID_COUNT_SCALE, SLOT_COUNT + 1, 0)


def normal_adapter():
    return dict(kind='ue_no_normal_height_gradient_scale',parameter='HeightNormalStrength',
                value=HEIGHT_NORMAL_STRENGTH,source_bump_scale=SOURCE_BUMP_SCALE,
                source_height_domain='normalized_source_uv_height',source_metres=False)


def normal_adapter_metadata():
    return json.dumps(normal_adapter(),sort_keys=True,separators=(',',':'))


def contract(spec, microdetails):
    if spec.get('shader') != SHADER:
        raise ValueError('expected the audited height-decal shader')
    slots = spec.get('texture_slots', [])
    if len(slots) < 4 or slots[1] or any(slots[3:]):
        raise ValueError('height-decal source texture layout differs from d/null/r/null')
    names = [slots[i].split('*')[0].split('$')[0] if slots[i] else '' for i in (0, 2)]
    if not names[0].endswith('_tex_d') or not names[1].endswith('_tex_r'):
        raise ValueError('height-decal must bind its original diffuse and reflection resources')
    if microdetails['slot_count'] != SLOT_COUNT or microdetails['id_count_scale'] != ID_COUNT_SCALE:
        raise ValueError('original Flanders microdetail count/CPU ID scale differs')
    return dict(schema=SCHEMA, material_id=spec['id'], source_shader=SHADER,
        source_diffuse=names[0], source_reflection=names[1], source_normal=None,
        source_slots=dict(diffuse=0, reflection=2, normal=None),
        public_reference_slots=dict(diffuse=0, normal=2, reflection=3),
        slot_mapping='Explicit adaptation of original d/null/r/null resources to the published reflection-channel contract',
        reflection_channels=dict(smoothness='A', ambient_occlusion='G', reflectance='R', microdetail_id='B'),
        normal='Published no-normal alpha-height finite differences and perturb_normal_height2d; clipmap XY becomes UE world XY',
        source_bump_scale=SOURCE_BUMP_SCALE, ue_normal_adapter=normal_adapter(),
        height_source=list(HEIGHT_SOURCE),
        normal_space='world_aligned_clipmap; combined with receiver geometric normal by RNM',
        rvt_microdetail_payload='(1 + clamp(T2.b * id_count_scale, 0, slot_count - 1)) / (slot_count + 1)',
        rvt_payload_channel='Specular', rvt_mask='coverage one before native opacity multiplication',
        textures=dict(T0=dict(srgb=True, compression='TC_DEFAULT'),
                      T2=dict(srgb=False, compression='TC_VECTOR_DISPLACEMENTMAP')),
        ue_surface=dict(roughness='clamp(1 - source_reflection.a, 0.04, 1)', metallic=0),
        source_reflectance_and_ao_rendered=False, original_geometry_and_png_unchanged=True,
        dagor_commit=DAGOR_COMMIT,
        references=['prog/daNetGame/shaders/make_clipmap.dshl:1397-1435',
                    'prog/gameLibs/render/shaders/psh_derivate.hlsl:33-48'])


HEIGHT_DECAL_CODE = r'''
float4 c=Texture2DSample(T0,T0Sampler,UV0);
float4 reflection=Texture2DSample(T2,T2Sampler,UV0);
// Published no-normal branch: alpha height at current and adjacent pixel UVs.
float2 dx=ddx(UV0),dy=ddy(UV0);
float dHs=(Texture2DSample(T0,T0Sampler,UV0+dx).a-c.a)*HeightSource.x;
float dHt=(Texture2DSample(T0,T0Sampler,UV0+dy).a-c.a)*HeightSource.x;
// psh_derivate.hlsl perturb_normal_height2d, expressed with UE Z-up axes.
float3 up=float3(0,0,1);
float3 sx=float3(dx,0),sy=float3(dy,0);
float3 r1=cross(sy,up),r2=cross(up,sx);
float det=dot(sx,r1);
float3 gradient=sign(det)*(dHs*r1+dHt*r2);
// Explicit UE fallback-normal strength; original 0.08 and UV derivatives stay intact.
NormalOut=abs(det)>1.e-20 ? normalize(abs(det)*up-gradient*HeightNormalStrength) : up;
RoughnessOut=clamp(1-reflection.a,0.04,1);
MetallicOut=0;
float sourceId=clamp(reflection.b*HeightSource.y,0,HeightSource.z-2);
MicrodetailPayloadOut=(1+sourceId)/HeightSource.z;
float sourceAlpha=(1-2*HeightBlend.z)*c.a+HeightBlend.z;
float vertexAlpha=pow(OpacityUV.x,HeightBlend.y);
OpacityOut=saturate((vertexAlpha-sourceAlpha)/(0.0001+HeightBlend.x));
return max(c.rgb,0);
'''
