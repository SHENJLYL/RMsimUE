"""Author RMUC's low-cost glass and moving sky materials in the full UE editor."""

import importlib.util
import json
from pathlib import Path
import re

import unreal

spec = importlib.util.spec_from_file_location('school_pbr', Path(__file__).with_name('school_fidelity_materials.py'))
school = importlib.util.module_from_spec(spec)
spec.loader.exec_module(school)
P = school.PBR

# Both materials sample the same plane in Unreal world centimetres.
CLOUD_FIELD = r'''
struct FCloudField
{
    float Hash(float2 p)
    {
        float3 q = frac(float3(p.xyx) * 0.1031);
        q += dot(q, q.yzx + 33.33);
        return frac((q.x + q.y) * q.z);
    }

    float Noise(float2 p)
    {
        float2 cell = floor(p);
        float2 u = frac(p);
        u = u * u * (3.0 - 2.0 * u);
        return lerp(lerp(Hash(cell), Hash(cell + float2(1, 0)), u.x),
                    lerp(Hash(cell + float2(0, 1)), Hash(cell + float2(1, 1)), u.x), u.y);
    }

    float Density(float2 position, float seconds)
    {
        float2 p = (position - float2(360.0, 115.0) * seconds) / 21000.0 + float2(13.0, 8.0);
        float field = 0.62 * Noise(p) + 0.26 * Noise(2.07 * p + 3.0) + 0.12 * Noise(4.13 * p + 11.0);
        return smoothstep(0.46, 0.70, field);
    }
};
FCloudField clouds;
float seconds = FixedCloudTime >= 0.0 ? FixedCloudTime : Clock;
'''


def expression(material, code, inputs, output, description):
    descriptors = []
    for name in inputs:
        descriptor = unreal.CustomInput()
        descriptor.set_editor_property('input_name', name)
        descriptors.append(descriptor)
    node = P.expr(material, unreal.MaterialExpressionCustom, x=100, code=code,
                  inputs=descriptors, output_type=output, description=description)
    for name, value in inputs.items():
        P.connect(value, node, name)
    return node


def scalar_parameter(material, name, default):
    return P.expr(material, unreal.MaterialExpressionScalarParameter, parameter_name=name, default_value=default)


def clock_inputs(material):
    return dict(Clock=P.expr(material, unreal.MaterialExpressionTime),
                FixedCloudTime=scalar_parameter(material, 'FixedCloudTime', -1.0))


def cube_input(material, texture):
    return P.expr(material, unreal.MaterialExpressionTextureObjectParameter, texture=texture,
                  parameter_name='SchoolEnvironment', sampler_type=unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR)


def make_glass(texture):
    material = school._new_material('M_ClearBoundaryGlassBakedReflection')
    material.set_editor_property('blend_mode', unreal.BlendMode.BLEND_TRANSLUCENT)
    material.set_editor_property('shading_model', unreal.MaterialShadingModel.MSM_UNLIT)
    material.set_editor_property('two_sided', True)
    material.set_editor_property('screen_space_reflections', False)
    material.set_editor_property('refraction_method', unreal.RefractionMode.RM_NONE)
    material.set_editor_property('used_with_nanite', False)
    fresnel = P.expr(material, unreal.MaterialExpressionFresnel, exponent=3.5, base_reflect_fraction=.0)
    reflection = P.expr(material, unreal.MaterialExpressionReflectionVectorWS)
    emissive = expression(material, '''
float3 baked = TextureCubeSampleLevel(Environment, EnvironmentSampler, Reflection, 5).rgb;
return float3(12.0, 18.0, 22.0) + min(baked, 1.4) * (56.0 + 65.0 * Grazing);
''', dict(Environment=cube_input(material, texture), Reflection=reflection, Grazing=fresnel),
        unreal.CustomMaterialOutputType.CMOT_FLOAT3, 'Blurred baked reflection without scene queries')
    P.property_input(emissive, unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    P.property_input(P.add(material, P.multiply(material, fresnel, .12), .025), unreal.MaterialProperty.MP_OPACITY)
    school._save_material(material)
    return material


def make_sky(texture):
    material = school._new_material('M_SchoolLandscapeClouds')
    material.set_editor_property('shading_model', unreal.MaterialShadingModel.MSM_UNLIT)
    material.set_editor_property('two_sided', True)
    material.set_editor_property('is_sky', True)
    emissive = expression(material, CLOUD_FIELD + r'''
float3 ray = -normalize(ViewDirection);
float3 base = TextureCubeSample(Environment, EnvironmentSampler, ray).rgb;
float travel = max(0.0, 13000.0 - Camera.z) / max(ray.z, 0.045);
float cover = clouds.Density(Camera.xy + travel * ray.xy, seconds);
cover *= smoothstep(0.045, 0.22, ray.z);
float3 cloudColor = lerp(float3(0.39, 0.42, 0.47), float3(0.99, 1.02, 1.07), cover);
return lerp(base, cloudColor, 0.92 * cover) * Brightness;
''', dict(Environment=cube_input(material, texture),
        ViewDirection=P.expr(material, unreal.MaterialExpressionCameraVectorWS),
        Camera=P.expr(material, unreal.MaterialExpressionCameraPositionWS),
        Brightness=scalar_parameter(material, 'EnvironmentBrightness', 512.0), **clock_inputs(material)),
        unreal.CustomMaterialOutputType.CMOT_FLOAT3, 'Moving projected clouds over the original HDR backdrop')
    P.property_input(emissive, unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    school._save_material(material)
    return material


def make_shadow():
    material = school._new_material('M_SchoolCloudShadows')
    material.set_editor_property('material_domain', unreal.MaterialDomain.MD_LIGHT_FUNCTION)
    sun = P.expr(material, unreal.MaterialExpressionVectorParameter, parameter_name='CloudSunDirection',
                 default_value=unreal.LinearColor(0, 0, 1, 0))
    mask = expression(material, CLOUD_FIELD + r'''
float travel = max(0.0, 13000.0 - Position.z) / max(SunToSky.z, 0.12);
return 1.0 - 0.52 * clouds.Density(Position.xy + travel * SunToSky.xy, seconds);
''', dict(Position=P.expr(material, unreal.MaterialExpressionWorldPosition), SunToSky=sun, **clock_inputs(material)),
        unreal.CustomMaterialOutputType.CMOT_FLOAT1, 'Project the same moving cloud field along sunlight')
    P.property_input(mask, unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    school._save_material(material)
    return material


def main():
    match = re.search(r'-VenueImportReport=(?:"([^"]+)"|([^\s"]+))', unreal.SystemLibrary.get_command_line())
    if not match:
        raise ValueError('Missing absolute -VenueImportReport path')
    output = Path(match[1] or match[2])
    texture = unreal.load_asset(school.BASE + '/Textures/T_SchoolSky8K')
    if not isinstance(texture, unreal.TextureCube):
        raise RuntimeError('The verified school environment cubemap is required')
    materials = dict(glass_material=make_glass(texture), environment_material=make_sky(texture),
                     cloud_shadows_material=make_shadow())
    output.write_text(json.dumps({name: value.get_path_name() for name, value in materials.items()}, indent=2)+'\n',
                      encoding='utf-8')
    unreal.log('RMSIM_SCHOOL_WEATHER_ASSETS_SAVED '+str(output))


if __name__ == '__main__':
    main()
