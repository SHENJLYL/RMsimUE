"""SchoolGymFidelity material authors; import only inside a full UE Editor.

All writes are confined to the new SchoolGymFidelity asset namespace. Source
base_color_factor values are already linear; source color textures use sRGB.
No renderer/process is started by this module. The caller owns mesh import,
shader completion, visual acceptance and its fresh report destination.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import math
import re

import unreal

BASE = '/Game/RMSim/Maps/rmuc2027/Arena/SchoolGymFidelity'
ROOT = Path(__file__).resolve().parents[2]
EAL = unreal.EditorAssetLibrary
MEL = unreal.MaterialEditingLibrary
TOOLS = unreal.AssetToolsHelpers.get_asset_tools()
_spec = importlib.util.spec_from_file_location('school_fidelity_pbr_nodes', ROOT / 'tools/unreal/import_campus_gym_assets.py')
PBR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(PBR)
SKY_SOURCE = ROOT / 'assets/maps/rmuc2027/arena/sketchupbox-school-gym/environment-fidelity/SOURCE.json'
SKY_SHA256 = '30ff6bb1eec58c11b52254e89b5dc39aaf756dd332c9a14a4976d68f23feaa71'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def checked(root, relative, manifest):
    root = Path(root).resolve()
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts:
        raise ValueError('Unsafe source-relative texture path')
    path = (root / rel).resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError('Source escaped prepared bundle')
    records = manifest.get('files', {})
    if isinstance(records, list):
        records = {entry.get('file', entry.get('path')): entry for entry in records}
    record = records.get(rel.as_posix())
    if not record:
        raise ValueError('Texture has no source identity record: ' + relative)
    expected_size = record.get('bytes', record.get('size'))
    if expected_size != path.stat().st_size or record.get('sha256') != sha(path):
        raise ValueError('Source identity mismatch: ' + relative)
    return path, record


def _new_material(name, clear_translucent_cache=False):
    if not re.fullmatch(r'M_[A-Za-z0-9_]+', name):
        raise ValueError('Unsafe generated material name: ' + name)
    path = BASE + '/Materials/' + name
    if clear_translucent_cache and EAL.does_asset_exist(path):
        # UE 5.8 can retain an obsolete custom-output cache after node deletion.
        # Recreate only this generated asset in our independent namespace.
        if not EAL.delete_asset(path):
            raise RuntimeError('Cannot recreate generated thin glass: ' + path)
    value = unreal.load_asset(path)
    if value is None:
        value = TOOLS.create_asset(name, BASE + '/Materials', unreal.Material, unreal.MaterialFactoryNew())
    if not isinstance(value, unreal.Material):
        raise RuntimeError('Expected generated Material: ' + path)
    MEL.delete_all_material_expressions(value)
    value.set_editor_property('used_with_instanced_static_meshes', True)
    value.set_editor_property('used_with_nanite', True)
    value.set_editor_property('tangent_space_normal', True)
    value.set_editor_property('blend_mode', unreal.BlendMode.BLEND_OPAQUE)
    value.set_editor_property('shading_model', unreal.MaterialShadingModel.MSM_DEFAULT_LIT)
    value.set_editor_property('two_sided', False)
    return value


def _save_material(value):
    MEL.recompile_material(value)
    if not EAL.save_loaded_asset(value):
        raise RuntimeError('Cannot save material: ' + value.get_path_name())


def _texture(root, relative, role, manifest, cache, report, normal_convention='DirectX'):
    flip = role == 'Normal' and normal_convention.lower() == 'opengl'
    key = (relative, role, flip)
    if key in cache:
        return cache[key]
    path, source = checked(root, relative, manifest)
    suffix = '_GLtoDX' if flip else ''
    name = 'T_' + re.sub(r'[^A-Za-z0-9_]', '_', path.stem) + '_' + role + suffix
    value = next((obj for obj in PBR.import_asset(path, BASE + '/Textures', name)
                  if isinstance(obj, unreal.Texture2D)), None)
    if value is None:
        raise RuntimeError('Texture import returned no Texture2D: ' + relative)
    value.set_editor_property('srgb', role in ('Color', 'Emissive'))
    value.set_editor_property('compression_settings', unreal.TextureCompressionSettings.TC_NORMALMAP
                              if role == 'Normal' else unreal.TextureCompressionSettings.TC_DEFAULT)
    value.set_editor_property('flip_green_channel', flip)
    # Keep every source 2K mip. Larger original school images may retain 4K.
    dimensions = source.get('dimensions')
    limit = 2048 if dimensions and max(dimensions) <= 2048 else 4096
    value.set_editor_property('max_texture_size', limit)
    value.set_editor_property('lod_bias', 0)
    if relative.startswith('branding/'):
        value.set_editor_property('address_x', unreal.TextureAddress.TA_CLAMP)
        value.set_editor_property('address_y', unreal.TextureAddress.TA_CLAMP)
    if not EAL.save_loaded_asset(value):
        raise RuntimeError('Cannot save texture: ' + relative)
    if bool(value.get_editor_property('flip_green_channel')) != flip:
        raise RuntimeError('Normal-map convention readback differs')
    record = {'source': relative, 'source_sha256': source['sha256'], 'asset': value.get_path_name(),
              'role': role, 'srgb': bool(value.get_editor_property('srgb')),
              'max_texture_size': int(value.get_editor_property('max_texture_size')),
              'flip_green_channel': flip, 'source_dimensions': dimensions,
              'source_pixels_modified': False}
    if hasattr(value, 'blueprint_get_size_x') and hasattr(value, 'blueprint_get_size_y'):
        record['native_size_readback'] = [value.blueprint_get_size_x(), value.blueprint_get_size_y()]
    report.setdefault('textures', []).append(record)
    cache[key] = value
    return value


def _sample(mat, tex, role, y=0):
    sampler = (unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL if role == 'Normal' else
               unreal.MaterialSamplerType.SAMPLERTYPE_COLOR if role in ('Color', 'Emissive') else
               unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR)
    return PBR.expr(mat, unreal.MaterialExpressionTextureSample, y=y, texture=tex, sampler_type=sampler)


def _channel_scaled(mat, node, channel, factor, y):
    if abs(float(factor) - 1.) < 1.e-10:
        return node, channel
    return PBR.multiply(mat, node, float(factor), y, channel), ''


def _thin_glass(name, color, source_opacity, roughness=.02, boundary=False):
    if not all(math.isfinite(float(x)) for x in [*color, source_opacity, roughness]):
        raise ValueError('Non-finite glass property')
    mat = _new_material(name, clear_translucent_cache=True)
    mat.set_editor_property('blend_mode', unreal.BlendMode.BLEND_TRANSLUCENT)
    mat.set_editor_property('shading_model', unreal.MaterialShadingModel.MSM_THIN_TRANSLUCENT)
    mat.set_editor_property('translucency_lighting_mode', unreal.TranslucencyLightingMode.TLM_SURFACE_PER_PIXEL_LIGHTING)
    mat.set_editor_property('refraction_method', unreal.RefractionMode.RM_NONE)
    mat.set_editor_property('two_sided', True)
    mat.set_editor_property('screen_space_reflections', True)
    mat.set_editor_property('used_with_nanite', False)
    PBR.property_input(PBR.vector(mat, color), unreal.MaterialProperty.MP_BASE_COLOR)
    PBR.property_input(PBR.scalar(mat, roughness, 120), unreal.MaterialProperty.MP_ROUGHNESS)
    PBR.property_input(PBR.scalar(mat, 0., 220), unreal.MaterialProperty.MP_METALLIC)
    PBR.property_input(PBR.scalar(mat, .5, 300), unreal.MaterialProperty.MP_SPECULAR)
    fresnel = PBR.expr(mat, unreal.MaterialExpressionFresnel, x=-900, y=440,
                       exponent=5., base_reflect_fraction=.04)
    extra = .08 if boundary else .12
    opacity = PBR.add(mat, PBR.multiply(mat, fresnel, extra, 440), source_opacity, 440)
    PBR.property_input(opacity, unreal.MaterialProperty.MP_OPACITY)
    # Match the source window tint at its source coverage, while clear boundary
    # glass remains nearly neutral and introduces no image-space refraction.
    transmittance = [0.99, 0.995, 0.998] if boundary else [
        max(0., min(1., 1. - source_opacity * (1. - float(component)))) for component in color]
    output = PBR.expr(mat, unreal.MaterialExpressionThinTranslucentMaterialOutput, x=0, y=650)
    PBR.connect(PBR.vector(mat, transmittance, 650), output)
    _save_material(mat)
    if mat.get_editor_property('refraction_method') != unreal.RefractionMode.RM_NONE:
        raise RuntimeError('Clear glass unexpectedly enables refraction')
    return mat, transmittance


def make_material(name, data, root, manifest, cache, report):
    """Compatible with import_school_gym_assets.make_material's call contract."""
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', name):
        raise ValueError('Unsafe material slot: ' + name)
    factor = [float(value) for value in data.get('base_color_factor', [1., 1., 1., 1.])]
    if len(factor) != 4 or not all(math.isfinite(value) for value in factor):
        raise ValueError('Expected finite linear RGBA source factor')
    semantic = data.get('semantic', 'source')
    alpha_mode = data.get('alpha_mode', 'OPAQUE')
    if alpha_mode == 'BLEND' or semantic in ('glass', 'window_glass'):
        opacity = max(0., min(.95, factor[3]))
        mat, transmittance = _thin_glass('M_' + name, factor[:3], opacity,
                                       float(data.get('glass_roughness', .02)))
        report.setdefault('materials', []).append({'slot': name, 'asset': mat.get_path_name(),
            'semantic': semantic, 'source_linear_factor': factor, 'source_opacity': opacity,
            'thin_transmittance': transmittance, 'glass_roughness': float(data.get('glass_roughness', .02)),
            'refraction': 'None', 'shared_campus_asset_modified': False})
        return mat
    mat = _new_material('M_' + name)
    mat.set_editor_property('two_sided', bool(data.get('double_sided', False)))
    if data.get('base_color_texture'):
        color_texture = _texture(root, data['base_color_texture'], 'Color', manifest, cache, report)
        color = _sample(mat, color_texture, 'Color')
        if any(abs(factor[i] - 1.) > 1.e-10 for i in range(3)):
            color = PBR.color_tint(mat, color, factor[:3])
    else:
        color = PBR.vector(mat, factor[:3])
    PBR.property_input(color, unreal.MaterialProperty.MP_BASE_COLOR)
    roughness = max(.02, min(1., float(data.get('roughness_factor', .74))))
    metallic = max(0., min(1., float(data.get('metallic_factor', 0.))))
    if data.get('arm_texture'):
        arm_texture = _texture(root, data['arm_texture'], 'ARM', manifest, cache, report)
        arm = _sample(mat, arm_texture, 'ARM', 230)
        rough_node, rough_pin = _channel_scaled(mat, arm, 'G', roughness, 230)
        metal_node, metal_pin = _channel_scaled(mat, arm, 'B', metallic, 330)
        PBR.property_input(rough_node, unreal.MaterialProperty.MP_ROUGHNESS, rough_pin)
        PBR.property_input(metal_node, unreal.MaterialProperty.MP_METALLIC, metal_pin)
        PBR.property_input(arm, unreal.MaterialProperty.MP_AMBIENT_OCCLUSION, 'R')
    else:
        PBR.property_input(PBR.scalar(mat, roughness, 250), unreal.MaterialProperty.MP_ROUGHNESS)
        PBR.property_input(PBR.scalar(mat, metallic, 350), unreal.MaterialProperty.MP_METALLIC)
    if data.get('normal_texture'):
        texture = _texture(root, data['normal_texture'], 'Normal', manifest, cache, report,
                           data.get('normal_convention', 'DirectX'))
        normal = _sample(mat, texture, 'Normal', 480)
        strength = float(data.get('normal_scale', 1.))
        if not math.isfinite(strength) or not 0 <= strength <= 4:
            raise ValueError('Unexpected source normal strength')
        if abs(strength - 1.) > 1.e-10:
            scaled = PBR.color_tint(mat, normal, [strength, strength, 1.], 480)
            normalized = PBR.expr(mat, unreal.MaterialExpressionNormalize, x=100, y=480)
            PBR.connect(scaled, normalized)
            normal = normalized
        PBR.property_input(normal, unreal.MaterialProperty.MP_NORMAL)
    if alpha_mode == 'MASK':
        mat.set_editor_property('blend_mode', unreal.BlendMode.BLEND_MASKED)
        mat.set_editor_property('opacity_mask_clip_value', float(data.get('alpha_cutoff', .45)))
        if data.get('alpha_texture'):
            alpha = _sample(mat, _texture(root, data['alpha_texture'], 'Alpha', manifest, cache, report), 'Alpha', 720)
            PBR.property_input(alpha, unreal.MaterialProperty.MP_OPACITY_MASK, 'R')
        elif data.get('base_color_texture'):
            alpha = _sample(mat, _texture(root, data['base_color_texture'], 'Color', manifest, cache, report), 'Color', 720)
            PBR.property_input(alpha, unreal.MaterialProperty.MP_OPACITY_MASK, 'A')
        else:
            raise ValueError('Masked source material has no opacity image')
    if data.get('shading_model') == 'TwoSidedFoliage':
        if alpha_mode != 'MASK':
            raise ValueError('School vegetation must use its verified masked alpha')
        mat.set_editor_property('shading_model', unreal.MaterialShadingModel.MSM_TWO_SIDED_FOLIAGE)
        mat.set_editor_property('two_sided', True)
        tint = data.get('subsurface_color_factor', [.35, .35, .35])
        subsurface = PBR.color_tint(mat, color, tint, 850)
        PBR.property_input(subsurface, unreal.MaterialProperty.MP_SUBSURFACE_COLOR)
    emissive = data.get('emissive_factor', [0., 0., 0.])
    if max(emissive) > 0:
        if data.get('emissive_texture'):
            emission_texture = _texture(root, data['emissive_texture'], 'Emissive', manifest, cache, report)
            emission = PBR.color_tint(mat, _sample(mat, emission_texture, 'Emissive', 1000), emissive, 1000)
        else:
            emission = PBR.vector(mat, emissive, 1000)
        PBR.property_input(emission, unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    _save_material(mat)
    report.setdefault('materials', []).append({'slot': name, 'asset': mat.get_path_name(),
        'semantic': semantic, 'source_linear_factor': factor, 'constant_gamma_conversion_applied': False,
        'base_color_texture': data.get('base_color_texture'), 'alpha_texture': data.get('alpha_texture'),
        'arm_texture': data.get('arm_texture'), 'normal_texture': data.get('normal_texture'),
        'normal_convention': data.get('normal_convention', 'DirectX'), 'normal_scale': data.get('normal_scale', 1.),
        'blend_mode': str(mat.get_editor_property('blend_mode')),
        'shading_model': str(mat.get_editor_property('shading_model')),
        'two_sided': bool(mat.get_editor_property('two_sided')),
        'source_uv_policy': 'Mesh UVs preserved, KHR transforms already baked by preparation.'})
    return mat


def import_presentation_assets(source_path=None, report=None):
    """Author pinned 8K pure sky and clear fence glass; returns runtime asset paths.

    source_path may be the SOURCE.json or its directory. Defaults to the selected
    school's environment-fidelity source. No active manifest is written here.
    """
    if report is None:
        report = {}
    record_path = Path(source_path).resolve() if source_path is not None else SKY_SOURCE
    if record_path.is_dir():
        record_path = record_path / 'SOURCE.json'
    source = json.loads(record_path.read_text(encoding='utf-8-sig'))
    if source.get('id') != 'kloofendal_48d_partly_cloudy_puresky' or source.get('resolution') != [8192, 4096]:
        raise ValueError('Unexpected pure-sky source identity')
    path = (record_path.parent / source['file']).resolve(strict=True)
    if not path.is_relative_to(record_path.parent.resolve()):
        raise ValueError('Sky source escaped its source folder')
    if sha(path) != SKY_SHA256 or source['sha256'] != SKY_SHA256 or path.stat().st_size != source['size']:
        raise ValueError('Pinned 8K HDR bytes changed')
    texture = next((obj for obj in PBR.import_asset(path, BASE + '/Textures', 'T_SchoolSky8K', factory=unreal.TextureFactory())
                    if isinstance(obj, unreal.TextureCube)), None)
    if texture is None:
        raise RuntimeError('HDR import must create a TextureCube')
    texture.set_editor_property('srgb', False)
    texture.set_editor_property('compression_settings', unreal.TextureCompressionSettings.TC_HDR)
    texture.set_editor_property('max_texture_size', 2048)
    texture.set_editor_property('lod_bias', 0)
    if not EAL.save_loaded_asset(texture):
        raise RuntimeError('Cannot save 8K-source sky cubemap')
    sky = _new_material('M_SchoolSky')
    sky.set_editor_property('shading_model', unreal.MaterialShadingModel.MSM_UNLIT)
    sky.set_editor_property('two_sided', True)
    sky.set_editor_property('is_sky', True)
    camera = PBR.expr(sky, unreal.MaterialExpressionCameraVectorWS, x=-1000)
    direction = PBR.multiply(sky, camera, -1.)
    cube = PBR.expr(sky, unreal.MaterialExpressionTextureSampleParameterCube, x=-100, y=0,
                    texture=texture, parameter_name='SchoolSky8K',
                    sampler_type=unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR)
    PBR.connect(direction, cube)
    brightness = PBR.expr(sky, unreal.MaterialExpressionScalarParameter, x=-100, y=240,
                          parameter_name='EnvironmentBrightness', default_value=512.)
    radiance = PBR.expr(sky, unreal.MaterialExpressionMultiply, x=200)
    PBR.connect(cube, radiance, 'A', 'RGB')
    PBR.connect(brightness, radiance, 'B')
    PBR.property_input(radiance, unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    _save_material(sky)
    glass, transmittance = _thin_glass('M_ClearBoundaryGlass', [.04, .05, .055], .018, .02, True)
    paths = {'glass_material': glass.get_path_name(), 'environment_material': sky.get_path_name(),
             'environment_cubemap': texture.get_path_name()}
    report['presentation_assets'] = dict(paths)
    report['presentation_details'] = {'source': str(record_path), 'source_sha256': SKY_SHA256,
        'source_resolution': source['resolution'], 'source_bytes': path.stat().st_size,
        'cubemap_max_texture_size': int(texture.get_editor_property('max_texture_size')),
        'cubemap_compression': str(texture.get_editor_property('compression_settings')),
        'cubemap_srgb': bool(texture.get_editor_property('srgb')),
        'sky_sample_direction': '-CameraVectorWS', 'sky_default_brightness': 512.,
        'glass_roughness': .02, 'glass_front_opacity': .018 + .04 * .08,
        'glass_refraction': 'None', 'glass_transmittance': transmittance,
        'shared_campus_assets_modified': False, 'helper_sha256': sha(Path(__file__).resolve()),
        'scope': 'Saved native assets; caller must verify resource completion and field-level rendered views.'}
    return paths
