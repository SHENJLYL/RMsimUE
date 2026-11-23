"""Verified base-color binding for the three original Flanders water_aces ponds.

Only the source RGB is bound. Density and the second source vector remain
recorded data until their consuming shader formula is established. The opaque,
rough neutral UE surface is an explicit renderer adaptation.
"""
import hashlib
import json
from pathlib import Path

MAP_ID = 'wt_vlaanderen'
SOURCE_MAP_SHA256 = '427c9b06b62af0635fa08f2d6238211b9850caf7717a0773f186e1835be63b8f'
MATERIAL_ID = 'WT_3baf052e10b5a3ecdbf7'
MESH_ID = 'world_static_000'
MESH_SHA256 = '07344e0ea99562383042088cb40887143744ab7847ab7d4aa68f07ea8e5772c7'
SOURCE_AUDIT_SHA256 = '83b8e07465427ec32048b667ccec72081eb52779ec170acbdffd0c64c2f56323'
LEVEL_CONFIG_SHA256 = 'e5a7d781e3a98e899c5e9665a1671eb88a5e12b31acf42344fa66fc73dcc5869'
COLOR_AND_DENSITY = (0.20000000298023224, 0.20000000298023224, 0.0, 15.0)
SOURCE_PARAMS = (1.0, 0.30000001192092896, 5.0, 0.0)
COLOR_RGBA = (*COLOR_AND_DENSITY[:3], 1.0)
UE_ROUGHNESS = 1.0
UE_METALLIC = 0.0
UE_SPECULAR = 0.5


def validate_level_globals(decoded):
    nodes = [node for node in decoded.get('children', []) if node.get('name') == 'shader_vars']
    if len(nodes) != 1:
        raise ValueError('expected one original level shader_vars block')
    expected = {'water_aces_color_and_density': COLOR_AND_DENSITY, 'water_aces_params': SOURCE_PARAMS}
    for name, values in expected.items():
        fields = [entry for entry in nodes[0].get('params', []) if entry.get('name') == name]
        if len(fields) != 1 or fields[0].get('type') != 'point4' or tuple(fields[0].get('value', [])) != values:
            raise ValueError('original water shader global differs: ' + name)
    return True


def water_surface_contract(source, package=None):
    """Require the audited map, material identity, and original receiver mesh."""
    specs = [entry for entry in source.get('materials', []) if entry.get('shader') == 'water_aces']
    if not specs:
        return None
    if source.get('id') != MAP_ID or source.get('source_sha256') != SOURCE_MAP_SHA256:
        raise ValueError('water base-color binding requires the audited original Flanders map')
    if len(specs) != 1:
        raise ValueError('expected one original water_aces material')
    spec = specs[0]
    identity_data = {key: spec[key] for key in ('shader', 'parameters', 'texture_slots')}
    identity = 'WT_' + hashlib.sha256(json.dumps(identity_data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:20]
    if (identity != MATERIAL_ID or spec.get('id') != MATERIAL_ID or spec['parameters'] != ''
            or spec['texture_slots'] != [None] * 11):
        raise ValueError('water_aces material differs from the audited source identity')
    receivers = [mesh for mesh in source.get('meshes', []) if MATERIAL_ID in mesh.get('material_slots', [])]
    if len(receivers) != 1:
        raise ValueError('original water binding must affect exactly its original static receiver')
    mesh = receivers[0]
    if (mesh['id'] != MESH_ID or mesh.get('sha256') != MESH_SHA256
            or mesh.get('file') != 'meshes/world_static_000.fbx'
            or mesh['material_slots'].index(MATERIAL_ID) != 4):
        raise ValueError('original water receiver identity or material slot differs')
    if package is not None:
        root = Path(package).resolve(strict=True)
        filename = (root / mesh['file']).resolve(strict=True)
        if root not in filename.parents or hashlib.sha256(filename.read_bytes()).hexdigest() != MESH_SHA256:
            raise ValueError('original water receiver FBX bytes differ')
    return {
        'schema': 'wt_water_base_color_contract_v1',
        'source_map_sha256': SOURCE_MAP_SHA256, 'material_id': MATERIAL_ID,
        'source_mesh_id': MESH_ID, 'source_mesh_sha256': MESH_SHA256, 'material_slot': 4,
        'source_audit_sha256': SOURCE_AUDIT_SHA256, 'decoded_level_sha256': LEVEL_CONFIG_SHA256,
        'source_level_entry': 'levels/avg_vlaanderen.blk',
        'source_globals': {'water_aces_color_and_density': list(COLOR_AND_DENSITY), 'water_aces_params': list(SOURCE_PARAMS)},
        'bound_source_rgb': list(COLOR_RGBA[:3]),
        'geometry': {'original_ponds': 3, 'source_triangles': 23, 'preserve_original_vertex_height': True},
        'ue_adapter': {'blend': 'Opaque', 'roughness': UE_ROUGHNESS, 'metallic': UE_METALLIC,
                       'specular': UE_SPECULAR, 'two_sided': True, 'world_position_offset': False},
        'renderer_coverage': 'source_base_color_only',
        'unresolved_source_semantics': ['density W=15 consuming formula and units',
                                        'water_aces_params consuming formula',
                                        'original wave, reflection and refraction resources'],
    }
