"""Source-channel adapters for authored Dagor rendinst mesh decals.

Pinned DagorEngine 9639942de5a8e5b01d91977f60ed82cda22d5f41:
  prog/daNetGame/shaders/decals/modulate2x_decal_inc.dshl
  prog/daNetGame/shaders/decals/blend_diffuse_decal_inc.dshl
  prog/daNetGame/shaders/decals/deferred_decal_inc.dshl
  prog/daNetGame/shaders/decals/decal_inc.dshl
  prog/daNetGame/shaders/rendinst_decal.dshl

UE DBuffer converts Modulate to Translucent. The color-multiply family therefore
uses a Surface/Unlit/Modulate material. It multiplies lit scene color, preserving
the source neutral factor and overlapping products. Source smoothness/metalness
multiplication is retained as metadata, not implemented by that surface pass.
Diffuse and normal-only decals use the native DeferredDecal mesh pass. UE exposes
one opacity; diffuse's source albedo gamma controls the shared blend. Independent
normal gamma, source per-section draw_order and paint palettes remain unadapted.
"""
import math

MODULATE_SHADERS = frozenset(('rendinst_deferred_modulate2x_decal', 'rendinst_modulate2x_diffuse_decal'))
DIFFUSE_SHADERS = frozenset(('rendinst_deferred_decal', 'rendinst_blend_diffuse_decal'))
NORMAL_SHADERS = frozenset(('rendinst_deferred_normal_decal',))
SHADERS = MODULATE_SHADERS | DIFFUSE_SHADERS | NORMAL_SHADERS
FLAVORS = frozenset(('DecalModulate', 'DecalDiffuse', 'DecalNormal'))
CAMERA_OFFSET_CM = 0.5  # source DECAL_TO_CAMERA_OFFSET = 0.005 m


def flavor(shader):
    if shader in MODULATE_SHADERS:
        return 'DecalModulate'
    if shader in DIFFUSE_SHADERS:
        return 'DecalDiffuse'
    if shader in NORMAL_SHADERS:
        return 'DecalNormal'
    return None


def contract(spec):
    kind = flavor(spec['shader'])
    if kind is None:
        return None
    slots = spec.get('texture_slots', [])
    required_slot = 2 if kind == 'DecalNormal' else 0
    if len(slots) <= required_slot or not slots[required_slot]:
        raise ValueError('source decal is missing its required texture slot')
    raw = {}
    for line in spec['parameters'].splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            try:
                raw[key.strip()] = [float(x.strip()) for x in value.split(',')]
            except ValueError:
                continue
    def vec(name, defaults):
        result = (raw.get(name, []) + list(defaults[len(raw.get(name, [])):]))[:len(defaults)]
        if len(result) != len(defaults) or not all(math.isfinite(x) for x in result):
            raise ValueError('nonfinite decal source parameter: ' + name)
        return result
    gamma = vec('gamma_mask', (1, 1, 0, 0))
    normal_gamma = vec('nm_met_sm_gamma', (1, 1, 0, 0))
    if min(gamma[:2]) < 0 or min(normal_gamma[:2]) < 0:
        raise ValueError('negative source opacity exponent is unsupported')
    if kind == 'DecalNormal' and normal_gamma[0] != normal_gamma[1]:
        raise ValueError('normal-only decal requires independent attribute opacity')
    deferred = 'deferred' in spec['shader']
    depth = [30.0, 40.0, float(deferred), 0.0] if kind == 'DecalModulate' else [8.0, 10.0, float(deferred), 0.0]
    return dict(schema='wt_rendinst_decal_adapter_v1', flavor=kind,
                source_shader=spec['shader'], camera_offset_cm=CAMERA_OFFSET_CM,
                depth_range_cm=depth, intensity=vec('intensity', (1, 0, 1, 0)),
                smoothness_metalness=vec('smoothness_metalness', (0, 0, 0, 0)),
                gamma_mask=gamma, normal_gamma=normal_gamma,
                atest=float(deferred and vec('atest', (0,))[0] > 0),
                draw_order=vec('draw_order', (100 if deferred else -10,))[0])


MODULATE_CODE = r'''
float3 color=Texture2DSample(T0,T0Sampler,UV0).rgb;
// shader_global.dshl:474 uses these luminance weights.
float lum=dot(color,float3(0.299,0.587,0.114));
if(lum<Intensity.y || lum>Intensity.z) color=0.5;
else {
 color=lerp(color,lum.xxx,Intensity.w);
 color=(color-0.5)*Intensity.x+0.5;
}
float depthMask=DepthRange.z>0.5 ? saturate((DepthRange.y-abs(SceneDepth-PixelDepth))/max(DepthRange.y-DepthRange.x,0.0001)) : 1;
return lerp(float3(1,1,1),max(2*color,0),depthMask);
'''

NORMAL_CODE = r'''
float4 n=Texture2DSample(T2,T2Sampler,UV0);
float2 xy=n.ag*2-1;
NormalOut=normalize(float3(xy,sqrt(saturate(1-dot(xy,xy)))));
RoughnessOut=clamp(1-n.r,0.04,1);MetallicOut=saturate(n.b);
float alpha=saturate(abs(n.g-0.5)*20);
float depthMask=saturate((DepthRange.y-abs(SceneDepth-PixelDepth))/max(DepthRange.y-DepthRange.x,0.0001));
OpacityOut=(NormalGamma.x<10 ? pow(alpha,NormalGamma.x) : 0)*depthMask;
return float3(0,0,0);
'''

DIFFUSE_CODE = r'''
float4 color=Texture2DSample(T0,T0Sampler,UV0);
float4 n=Texture2DSample(T2,T2Sampler,UV0);
float2 xy=n.ag*2-1;
NormalOut=normalize(float3(xy,sqrt(saturate(1-dot(xy,xy)))));
RoughnessOut=clamp(1-n.r,0.04,1);MetallicOut=saturate(n.b);
float alpha=SourceAtest>0.5 ? smoothstep(0.45,0.55,color.a) : color.a;
float depthMask=DepthRange.z>0.5 ? saturate((DepthRange.y-abs(SceneDepth-PixelDepth))/max(DepthRange.y-DepthRange.x,0.0001)) : 1;
OpacityOut=pow(alpha,GammaMask.x)*depthMask;
return color.rgb;
'''


def author_parent(material, textures, kind):
    import unreal
    import wt_materials as w
    if kind not in FLAVORS:
        raise ValueError('unrecognized source decal parent')
    material.set_editor_property('two_sided', True)
    material.set_editor_property('tangent_space_normal', True)
    w.EDIT.set_material_usage(material, unreal.MaterialUsage.MATUSAGE_INSTANCED_STATIC_MESHES)
    inputs = {
        'UV0': (w._expr(material, unreal.MaterialExpressionTextureCoordinate, coordinate_index=0), ''),
        'SceneDepth': (w._expr(material, unreal.MaterialExpressionSceneDepth), ''),
        'PixelDepth': (w._expr(material, unreal.MaterialExpressionPixelDepth), ''),
        'DepthRange': (w._vector(material, 'DepthRange', (8,10,1,0)), 'RGBA'),
    }
    if kind == 'DecalModulate':
        material.set_editor_property('material_domain', unreal.MaterialDomain.MD_SURFACE)
        # Every editor property change can compile: establish an unlit model first.
        material.set_editor_property('shading_model', unreal.MaterialShadingModel.MSM_UNLIT)
        material.set_editor_property('blend_mode', unreal.BlendMode.BLEND_MODULATE)
        material.set_editor_property('translucency_pass', unreal.MaterialTranslucencyPass.MTP_BEFORE_DOF)
        inputs['T0'] = (w._texture(material, 'T0', textures.get(None, False, 'MidGray'), False), '')
        inputs['Intensity'] = (w._vector(material, 'Intensity', (1,0,1,0)), 'RGBA')
        surface = w._leaf_custom(material, MODULATE_CODE, inputs)
        w._prop(surface, '', w.P.MP_EMISSIVE_COLOR)
    else:
        # Translucent is valid in Surface and DeferredDecal; Opaque is not.
        material.set_editor_property('blend_mode', unreal.BlendMode.BLEND_TRANSLUCENT)
        material.set_editor_property('material_domain', unreal.MaterialDomain.MD_DEFERRED_DECAL)
        material.set_editor_property('shading_model', unreal.MaterialShadingModel.MSM_DEFAULT_LIT)
        inputs['T2'] = (w._texture(material, 'T2', textures.get(None, False, 'NormalAG'), False), '')
        if kind == 'DecalNormal':
            inputs['NormalGamma'] = (w._vector(material, 'NormalGamma', (1,1,0,0)), 'RGBA')
            surface = w._custom(material, NORMAL_CODE, inputs, bind_base_color=False)
        else:
            inputs['T0'] = (w._texture(material, 'T0', textures.get(None, True, 'White'), True), '')
            inputs['GammaMask'] = (w._vector(material, 'GammaMask', (1,1,0,0)), 'RGBA')
            inputs['SourceAtest'] = (w._scalar(material, 'SourceAtest', 0), '')
            surface = w._custom(material, DIFFUSE_CODE, inputs)
        w._prop(surface, 'OpacityOut', w.P.MP_OPACITY)
    # Render adaptation only. The original FBX vertices and instance scales stay intact.
    offset = w._leaf_custom(material, 'return normalize(Camera-World)*OffsetCm;', {
        'Camera': (w._expr(material, unreal.MaterialExpressionCameraPositionWS), ''),
        'World': (w._expr(material, unreal.MaterialExpressionWorldPosition), ''),
        'OffsetCm': (w._scalar(material, 'SourceCameraOffsetCm', CAMERA_OFFSET_CM), ''),
    })
    w._prop(offset, '', w.P.MP_WORLD_POSITION_OFFSET)
    w._save_parent(material)
    return material


def author_instance(instance, spec, textures):
    import unreal
    import wt_materials as w
    data = contract(spec)
    if data is None:
        raise ValueError('not an authored rendinst decal')
    w.EDIT.clear_all_material_instance_parameters(instance)
    instance.set_editor_property('base_property_overrides', unreal.MaterialInstanceBasePropertyOverrides())
    vectors = {'DepthRange': data['depth_range_cm']}
    if data['flavor'] == 'DecalModulate':
        vectors['Intensity'] = data['intensity']
    elif data['flavor'] == 'DecalNormal':
        vectors['NormalGamma'] = data['normal_gamma']
    else:
        vectors['GammaMask'] = data['gamma_mask']
        w.EDIT.set_material_instance_scalar_parameter_value(instance, 'SourceAtest', data['atest'])
    for name, value in vectors.items():
        w.EDIT.set_material_instance_vector_parameter_value(instance, name, unreal.LinearColor(*value))
    w.EDIT.set_material_instance_scalar_parameter_value(instance, 'SourceCameraOffsetCm', CAMERA_OFFSET_CM)
    indices = (0,) if data['flavor']=='DecalModulate' else (2,) if data['flavor']=='DecalNormal' else (0,2)
    for index in indices:
        color = index==0 and data['flavor']=='DecalDiffuse'
        texture = textures.get(spec['texture_slots'][index], color, 'NormalAG' if index==2 else 'White')
        w.EDIT.set_material_instance_texture_parameter_value(instance, 'T'+str(index), texture)
    w.EDIT.update_material_instance(instance)
    if not unreal.EditorAssetLibrary.save_loaded_asset(instance):
        raise RuntimeError('could not save source decal instance')
    return data
