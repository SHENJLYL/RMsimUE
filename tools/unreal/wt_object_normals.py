"""Per-UV world-normal contract for the source layered object family.

Pinned Dagor helpers are reproduced in UE's Z-up frame. This changes the normal
branch only; original diffuse sampling, weights, alpha and PBR channels stay in
wt_materials. Degenerate UV frames return the geometric normal explicitly.
"""
import hashlib
import json

SCHEMA = 'wt_layered_uv_normal_v1'
METADATA_TAG = 'WT_LayeredNormalAdapter'
SHADERS = frozenset(('rendinst_layered', 'rendinst_layered_hmap_blend',
                     'rendinst_layered_hmap_vertex_blend'))
DAGOR_COMMIT = '9639942de5a8e5b01d91977f60ed82cda22d5f41'

LEGACY_BLEND = ' normal=normalize(float3(normal.xy+lerp(util.normalAG(n1).xy,util.normalAG(n2).xy,weight),normal.z));'

HELPERS = r'''
struct WTLayeredNormals {
 float3 fromLocal(float3 localNormal,float3 N,float3 dp2perp,float3 dp1perp,float2 uv) {
   float2 duv1=ddx(uv),duv2=ddy(uv);
   float3 T=dp2perp*duv1.x+dp1perp*duv2.x;
   float3 B=dp2perp*duv1.y+dp1perp*duv2.y;
   float grad=max(dot(T,T),dot(B,B));
   // psh_tangent.hlsl safe variant for original degenerate UV triangles.
   if(grad<=1.e-20) return N;
   float invmax=rsqrt(grad);
   return localNormal.z*N+(localNormal.x*invmax)*T+(localNormal.y*invmax)*B;
 }
 float3 combine(float3 baseNormal,float3 detailNormal,float3 N) {
   float3 t=baseNormal+N;
   float3 u=-detailNormal+N*(2*dot(detailNormal,N));
   return normalize(t*(dot(t,u)/max(dot(t,N),1.e-6))-u);
 }
};
'''

SOURCE_BLEND = r''' // Each sampled normal uses its own source UV cotangent frame.
 WTLayeredNormals layeredNormals;
 float3 geometric=normalize(GeometricNormal);
 // Dagor uses pointToEye: its screen derivatives are -ddx/ddy(World).
 float3 dp2perp=cross(geometric,-ddy(World));
 float3 dp1perp=cross(-ddx(World),geometric);
 float3 baseWorld=layeredNormals.fromLocal(normal,geometric,dp2perp,dp1perp,UV0);
 float3 detail1World=layeredNormals.fromLocal(util.normalAG(n1),geometric,dp2perp,dp1perp,UV2*Tiles.xy);
 float3 detail2World=layeredNormals.fromLocal(util.normalAG(n2),geometric,dp2perp,dp1perp,UV2*Tiles.zw);
 float3 layeredWorld=layeredNormals.combine(baseWorld,lerp(detail1World,detail2World,weight),geometric);
 // UE consumes a tangent normal here and applies TwoSidedSign afterward.
 normal=normalize(TransformWorldVectorToTangent(Parameters.TangentToWorld,layeredWorld));'''


def surface_code(legacy_code):
    if legacy_code.count(LEGACY_BLEND) != 1:
        raise ValueError('layered normal source anchor changed')
    return HELPERS + legacy_code.replace(LEGACY_BLEND, SOURCE_BLEND)


def contract(legacy_code):
    return dict(schema=SCHEMA, dagor_commit=DAGOR_COMMIT,
        source_shaders=sorted(SHADERS), parent='WT_Parent_Surface',
        base_normal_uv=0, detail_normal_uv=2,
        frame='per-sampled-UV scale-invariant cotangent frame from negative world derivatives',
        composition='world-space RNM after the existing source detail weight',
        output='TransformWorldVectorToTangent(Parameters.TangentToWorld, layeredWorld)',
        two_sided='unflipped geometric frame; engine applies TwoSidedSign once',
        degenerate_uv='geometric normal when max squared tangent length <= 1e-20',
        non_layered_branches='original shader text unchanged',
        texture_uv_color_weights_alpha_roughness_metallic='unchanged',
        code_sha256=hashlib.sha256(surface_code(legacy_code).encode()).hexdigest(),
        source_references=[
            'prog/gameLibs/render/shaders/psh_tangent.hlsl:108-134',
            'prog/gameLibs/render/shaders/normaldetail.hlsl:73-105',
            'prog/gameLibs/render/shaders/layered_inc.dshl:304-313'],
        ue_references=[
            'Engine/Shaders/Private/Common.ush:1988-1999',
            'Engine/Shaders/Private/MaterialTemplate.ush:4353-4380'])


def metadata(legacy_code):
    return json.dumps(contract(legacy_code), sort_keys=True, separators=(',', ':'))


def graph_record(material):
    import unreal
    import wt_materials
    from wt_ground_material_verify import _reroute
    edit = unreal.MaterialEditingLibrary

    def require(value, message):
        if not value:
            raise RuntimeError('layered object normal: ' + message)

    require(material.get_name() == 'WT_Parent_Surface', 'unexpected parent')
    require(bool(material.get_editor_property('tangent_space_normal')),
            'parent must consume tangent-space NormalOut')
    require(bool(material.get_editor_property('two_sided')),
            'existing two-sided surface contract changed')
    require(bool(material.get_editor_property('used_with_instanced_static_meshes')),
            'source HISM permutation is absent')
    base = edit.get_material_property_input_node(material, unreal.MaterialProperty.MP_BASE_COLOR)
    require(isinstance(base, unreal.MaterialExpressionCustom), 'BaseColor is not the source Custom')
    require(str(base.get_editor_property('code')) == surface_code(wt_materials.OBJECT_CODE),
            'source surface code differs')
    names = [str(x) for x in edit.get_material_expression_input_names(base)]
    nodes = list(edit.get_inputs_for_material_expression(material, base))
    require(len(names) == len(nodes) and all(x is not None for x in nodes),
            'Custom input is disconnected')
    inputs = dict(zip(names, nodes))
    texture_inputs = {name: node for name, node in inputs.items()
                      if isinstance(node, unreal.MaterialExpressionTextureObjectParameter)}
    require(set(texture_inputs) == {'T' + str(i) for i in range(7)},
            'the seven original source texture inputs changed')
    require(isinstance(inputs.get('World'), unreal.MaterialExpressionWorldPosition),
            'World must be the absolute world-position node')
    require(isinstance(inputs.get('GeometricNormal'), unreal.MaterialExpressionVertexNormalWS),
            'GeometricNormal must retain the unflipped vertex normal')
    for index in range(3):
        uv = inputs.get('UV' + str(index))
        require(isinstance(uv, unreal.MaterialExpressionTextureCoordinate)
                and uv.get_editor_property('coordinate_index') == index,
                'source UV channel changed: ' + str(index))
    normal = edit.get_material_property_input_node(material, unreal.MaterialProperty.MP_NORMAL)
    connection = _reroute(material, normal, base, 'NormalOut')
    require(unreal.EditorAssetLibrary.get_metadata_tag(material, METADATA_TAG)
            == metadata(wt_materials.OBJECT_CODE), 'saved normal provenance differs')
    return dict(source_contract=contract(wt_materials.OBJECT_CODE),
                custom=base.get_path_name(), normal_connection=connection,
                source_texture_inputs={name: node.get_path_name()
                                       for name, node in texture_inputs.items()},
                inputs={key: inputs[key].get_path_name()
                        for key in ('World', 'GeometricNormal', 'UV0', 'UV1', 'UV2')},
                tangent_space_normal=True, engine_two_sided_sign=True)
