"""Source-bound Flanders height-decal reflectance and alpha-height adapter.

The shipped source resources use d/null/r/null. Their cooked _r resources use
BARG packing, confirmed by the same-shader CDK sample and pinned asset exporter.
T2 therefore stores smoothness/reflectance/AO/microdetail ID in RGBA order.
The public shader uses separate normal/reflection slots; the slot mapping remains
an explicit resource-layout adaptation. Source PNG, UV and geometry stay intact.
"""
import json
import math
import struct

SCHEMA = 'wt_height_decal_surface_v4'
DAGOR_COMMIT = '9639942de5a8e5b01d91977f60ed82cda22d5f41'
SHADER = 'land_mesh_height_decal'
SOURCE_BUMP_SCALE = 0.08
# UE visual adaptation of the source UV-height gradient; not a source metre value.
HEIGHT_NORMAL_STRENGTH = 0.2
HEIGHT_NORMAL_METADATA_TAG = 'WT_HeightNormalAdapter'
HEIGHT_REFLECTION_METADATA_TAG = 'WT_HeightReflectionLayout'
HEIGHT_POLARITY_METADATA_TAG = 'WT_HeightPolarityAdapter'
# Source-id/texture-bound UE polarity adaptations approved from source RGB/alpha.
# Asphalt additionally retains its same-view native-probe identity.
# Explicit source height_above declarations take precedence.
HEIGHT_POLARITY_ADAPTERS = {'WT_318f5a3cd79c6587012f': {'basis': 'Original RGB/alpha silhouette and source UV-boundary witnesses; root reviewed '
                                      'all 18 source-shape panels',
                             'diffuse': 'ground_decal_soil_errosion_a_tex_d',
                             'diffuse_sha256': 'c09f179bca5994a43d30d2bae35a94f5be710c8f5b78b3b075a785b0903a1b22',
                             'height_above': 1,
                             'reflection': 'ground_decal_soil_errosion_a_tex_r',
                             'reflection_sha256': 'c5333eae886dc8f184df9a4432cae1e7518539e7645f9c697d2a97aa1292e2b6',
                             'source_shape_manifest_sha256': 'cc116516eb3d2c9d026bb4ac191311083fe2cb4877a79e16bfd8e06b90b70341',
                             'source_shape_report_sha256': '5e49a9195cd0e733b03e3463255136044dd470d6f515f390b7b995f925e835c8',
                             'source_uv_boundary_witnesses': 8344},
 'WT_35701bfc9a7d1810e224': {'basis': 'Original RGB/alpha silhouette and source UV-boundary witnesses; root reviewed '
                                      'all 18 source-shape panels',
                             'diffuse': 'sand_errosion_loft_a_tex_d',
                             'diffuse_sha256': 'c440ca9cc389458ec199d48f963c172f9defbf09cd0eb6fdaf33a79c98d0a14f',
                             'height_above': 1,
                             'reflection': 'soil_errosion_loft_b_tex_r',
                             'reflection_sha256': '84491c681549131c641c1889f22371f20df01b248e89e01ba159acf15655b43b',
                             'source_shape_manifest_sha256': 'cc116516eb3d2c9d026bb4ac191311083fe2cb4877a79e16bfd8e06b90b70341',
                             'source_shape_report_sha256': '5e49a9195cd0e733b03e3463255136044dd470d6f515f390b7b995f925e835c8',
                             'source_uv_boundary_witnesses': 803},
 'WT_3ec283666d818a9f5b22': {'basis': 'Original RGB/alpha silhouette and source UV-boundary witnesses; root reviewed '
                                      'all 18 source-shape panels',
                             'diffuse': 'ground_decal_battlefield_dirt_tex_d',
                             'diffuse_sha256': '04f8e556cebb81899cbc064ce88cd46f6de730276ffbf5f1028a4bbac3dcd8de',
                             'height_above': 1,
                             'reflection': 'ground_decal_battlefield_dirt_tex_r',
                             'reflection_sha256': 'e2349832eb1f499be78a22aee3b9c6a5a0a76326ea2e961bb8ba4500130e6a0a',
                             'source_shape_manifest_sha256': 'cc116516eb3d2c9d026bb4ac191311083fe2cb4877a79e16bfd8e06b90b70341',
                             'source_shape_report_sha256': '5e49a9195cd0e733b03e3463255136044dd470d6f515f390b7b995f925e835c8',
                             'source_uv_boundary_witnesses': 134},
 'WT_43749e19cb4c7864f27f': {'basis': 'Original RGB/alpha silhouette and source UV-boundary witnesses; root reviewed '
                                      'all 18 source-shape panels',
                             'diffuse': 'ground_decal_battlefield_dirt_dark_tex_d',
                             'diffuse_sha256': 'd27ebd8441ad20429b505f199b3e84d62613e59a35de7ca74603c2e82c74896b',
                             'height_above': 1,
                             'reflection': 'ground_decal_battlefield_dirt_dark_tex_r',
                             'reflection_sha256': 'd3de7fedab440644205599b8212d2ab550412901faee329eab98eef6f2d09bf8',
                             'source_shape_manifest_sha256': 'cc116516eb3d2c9d026bb4ac191311083fe2cb4877a79e16bfd8e06b90b70341',
                             'source_shape_report_sha256': '5e49a9195cd0e733b03e3463255136044dd470d6f515f390b7b995f925e835c8',
                             'source_uv_boundary_witnesses': 1437},
 'WT_56a8f9dad5f5fe075e90': {'basis': 'Original RGB/alpha silhouette and source UV-boundary witnesses; root reviewed '
                                      'all 18 source-shape panels',
                             'diffuse': 'decal_road_rail_tex_d',
                             'diffuse_sha256': 'e7ad16763a0bd09a189056d331bf5d03f18d64a0b2d2977f89babc52304f446d',
                             'height_above': 1,
                             'reflection': 'decal_road_rail_tex_r',
                             'reflection_sha256': 'f351746699ef0f70841e590a1df1845bc83ef661c3e7b2817634ff91dbd502c9',
                             'source_shape_manifest_sha256': 'cc116516eb3d2c9d026bb4ac191311083fe2cb4877a79e16bfd8e06b90b70341',
                             'source_shape_report_sha256': '5e49a9195cd0e733b03e3463255136044dd470d6f515f390b7b995f925e835c8',
                             'source_uv_boundary_witnesses': 988},
 'WT_6e25a8f73f405048601c': {'basis': 'Original RGB/alpha silhouette and source UV-boundary witnesses; root reviewed '
                                      'all 18 source-shape panels',
                             'diffuse': 'decal_road_rail_gravel_a_tex_d',
                             'diffuse_sha256': '706cd6b09a064509413f45585ae2fe60f4f498056762a192aced9c394ec64ec6',
                             'height_above': 1,
                             'reflection': 'decal_road_rail_gravel_a_tex_r',
                             'reflection_sha256': '4b3e1c7aff00bf4ba69245285135cda8dfaad8378b56f4c0db5be56b2fa4cec0',
                             'source_shape_manifest_sha256': 'cc116516eb3d2c9d026bb4ac191311083fe2cb4877a79e16bfd8e06b90b70341',
                             'source_shape_report_sha256': '5e49a9195cd0e733b03e3463255136044dd470d6f515f390b7b995f925e835c8',
                             'source_uv_boundary_witnesses': 1558},
 'WT_91dd399e4c661e36edb6': {'basis': 'Original RGB/alpha silhouette and source UV-boundary witnesses; root reviewed '
                                      'all 18 source-shape panels',
                             'diffuse': 'ground_decal_scorched_earth_a_burnt_tex_d',
                             'diffuse_sha256': '16deb4b8c68e9b58431c4294925b4c76b8d9eb6edd292843048d20a67666c613',
                             'height_above': 1,
                             'reflection': 'ground_decal_scorched_earth_a_burnt_tex_r',
                             'reflection_sha256': 'a33ce3d14031864380bc7721a18c95f135e9208c4faeb51783992ab1c4226201',
                             'source_shape_manifest_sha256': 'cc116516eb3d2c9d026bb4ac191311083fe2cb4877a79e16bfd8e06b90b70341',
                             'source_shape_report_sha256': '5e49a9195cd0e733b03e3463255136044dd470d6f515f390b7b995f925e835c8',
                             'source_uv_boundary_witnesses': 162},
 'WT_97af1d9a42e2f563dc16': {'basis': 'Original RGB/alpha silhouette and source UV-boundary witnesses; root reviewed '
                                      'all 18 source-shape panels',
                             'diffuse': 'ground_decal_debris_bricks_b_tex_d',
                             'diffuse_sha256': '9aba9c109c9a66dd31228e4b423bb5061f2ebd4a51947b0b50157df8be8b4b8b',
                             'height_above': 1,
                             'reflection': 'ground_decal_debris_bricks_b_tex_r',
                             'reflection_sha256': '7aa685940b12d0c28756c62925e6571b8409ace45257d47760a2969213813edf',
                             'source_shape_manifest_sha256': 'cc116516eb3d2c9d026bb4ac191311083fe2cb4877a79e16bfd8e06b90b70341',
                             'source_shape_report_sha256': '5e49a9195cd0e733b03e3463255136044dd470d6f515f390b7b995f925e835c8',
                             'source_uv_boundary_witnesses': 232},
 'WT_9a07e05fc39d69ca4fbe': {'basis': 'Original RGB/alpha silhouette and source UV-boundary witnesses; root reviewed '
                                      'all 18 source-shape panels',
                             'diffuse': 'river_sand_gravel_coast_border_tex_d',
                             'diffuse_sha256': 'e02bc923451b9a16798402511897dc6c6a1fac4b1b4117b073794cfacc937758',
                             'height_above': 1,
                             'reflection': 'river_sand_gravel_coast_border_tex_r',
                             'reflection_sha256': '5521210dd3766243a961d1c8e2f45a0a907ef28533a2340bd121aa165672c563',
                             'source_shape_manifest_sha256': 'cc116516eb3d2c9d026bb4ac191311083fe2cb4877a79e16bfd8e06b90b70341',
                             'source_shape_report_sha256': '5e49a9195cd0e733b03e3463255136044dd470d6f515f390b7b995f925e835c8',
                             'source_uv_boundary_witnesses': 304},
 'WT_9a32312dd7f5765f0476': {'basis': 'Original RGB/alpha silhouette and source UV-boundary witnesses; root reviewed '
                                      'all 18 source-shape panels',
                             'diffuse': 'ground_decal_soil_cliff_tex_d',
                             'diffuse_sha256': 'f02d4295a10f24055d535abffb0415f0c0a8cf50f6e0692692439c9d0f73f142',
                             'height_above': 1,
                             'reflection': 'ground_decal_soil_cliff_tex_r',
                             'reflection_sha256': 'ee379fa098b372c4a2eaaf9c4b68abc9c508d9eabeb1c599732fcbc5d65755c7',
                             'source_shape_manifest_sha256': 'cc116516eb3d2c9d026bb4ac191311083fe2cb4877a79e16bfd8e06b90b70341',
                             'source_shape_report_sha256': '5e49a9195cd0e733b03e3463255136044dd470d6f515f390b7b995f925e835c8',
                             'source_uv_boundary_witnesses': 212},
 'WT_9fb476f360cf8b8e16c5': {'basis': 'Original RGB/alpha silhouette and source UV-boundary witnesses; root reviewed '
                                      'all 18 source-shape panels',
                             'diffuse': 'ground_decal_coal_a_tex_d',
                             'diffuse_sha256': '175632ae2586230329f7ccc880fc485da3715a8ad4bb868cd990b09323d68b5c',
                             'height_above': 1,
                             'reflection': 'ground_decal_coal_a_tex_r',
                             'reflection_sha256': 'cac30315dd9446d8be23de1d9129cd70e4b5ebd7363ea64f6170a618398305b0',
                             'source_shape_manifest_sha256': 'cc116516eb3d2c9d026bb4ac191311083fe2cb4877a79e16bfd8e06b90b70341',
                             'source_shape_report_sha256': '5e49a9195cd0e733b03e3463255136044dd470d6f515f390b7b995f925e835c8',
                             'source_uv_boundary_witnesses': 226},
 'WT_ae32c3b40a762088d577': {'basis': 'Original RGB/alpha silhouette and source UV-boundary witnesses; root reviewed '
                                      'all 18 source-shape panels',
                             'diffuse': 'river_sand_soil_coast_border_tex_d',
                             'diffuse_sha256': '30b7ae68e713aa801f427aea320230f12f0b35c227038f76db10eb8c73c22d93',
                             'height_above': 1,
                             'reflection': 'river_sand_gravel_coast_border_tex_r',
                             'reflection_sha256': '5521210dd3766243a961d1c8e2f45a0a907ef28533a2340bd121aa165672c563',
                             'source_shape_manifest_sha256': 'cc116516eb3d2c9d026bb4ac191311083fe2cb4877a79e16bfd8e06b90b70341',
                             'source_shape_report_sha256': '5e49a9195cd0e733b03e3463255136044dd470d6f515f390b7b995f925e835c8',
                             'source_uv_boundary_witnesses': 528},
 'WT_d0fcdcc930a581d590de': {'basis': 'Original RGB/alpha silhouette and source UV-boundary witnesses; root reviewed '
                                      'all 18 source-shape panels',
                             'diffuse': 'decal_road_sand_g_tex_d',
                             'diffuse_sha256': '080b82cd0b024836b78780ee34b90b1445c40c85753b9ae26950ae9aab8d204e',
                             'height_above': 1,
                             'reflection': 'decal_road_sand_b_tex_r',
                             'reflection_sha256': '09f5e3076ba6edb9e211e2347b56c8e6a99137f599003c3b71159c3f1514cbc0',
                             'source_shape_manifest_sha256': 'cc116516eb3d2c9d026bb4ac191311083fe2cb4877a79e16bfd8e06b90b70341',
                             'source_shape_report_sha256': '5e49a9195cd0e733b03e3463255136044dd470d6f515f390b7b995f925e835c8',
                             'source_uv_boundary_witnesses': 1476},
 'WT_d7e4fb5a31e1eae8b011': {'basis': 'Original RGB/alpha silhouette and source UV-boundary witnesses; root reviewed '
                                      'all 18 source-shape panels',
                             'diffuse': 'ground_decal_oil_puddle_tex_d',
                             'diffuse_sha256': '85fa50069ce3095e1ed98ca12a14a099d075c157bff143dd4dd880ef4010e72d',
                             'height_above': 1,
                             'reflection': 'ground_decal_oil_puddle_tex_r',
                             'reflection_sha256': 'd662fa7fcac111ac8118afcbb196bca735e4d7a59c67f5146a5355c9d5a7f5f2',
                             'source_shape_manifest_sha256': 'cc116516eb3d2c9d026bb4ac191311083fe2cb4877a79e16bfd8e06b90b70341',
                             'source_shape_report_sha256': '5e49a9195cd0e733b03e3463255136044dd470d6f515f390b7b995f925e835c8',
                             'source_uv_boundary_witnesses': 2},
 'WT_dc22849ac8fc033e5ffd': {'basis': 'Original RGB/alpha silhouette and source UV-boundary witnesses; root reviewed '
                                      'all 18 source-shape panels',
                             'diffuse': 'decal_road_tracks_b_tex_d',
                             'diffuse_sha256': 'dbe982afafc8b3f627dabe806d69c29256976c2c026856d0fec1aaba82385376',
                             'height_above': 1,
                             'reflection': 'decal_road_tracks_b_tex_r',
                             'reflection_sha256': '3731cbbcc5f90c27ae0b928a9fa9f6051ca872116cfca558edb743eb5532127a',
                             'source_shape_manifest_sha256': 'cc116516eb3d2c9d026bb4ac191311083fe2cb4877a79e16bfd8e06b90b70341',
                             'source_shape_report_sha256': '5e49a9195cd0e733b03e3463255136044dd470d6f515f390b7b995f925e835c8',
                             'source_uv_boundary_witnesses': 1984},
 'WT_e82be167172b929d5356': {'basis': 'Original RGB/alpha silhouette and source UV-boundary witnesses; root reviewed '
                                      'all 18 source-shape panels',
                             'diffuse': 'decal_asphalt_road_worn_b_tex_d',
                             'diffuse_sha256': '6412455a137221ef4ed086e13d5b46d8557af5f1c7eb39b49647b0efafb9e2f1',
                             'height_above': 1,
                             'probe_verification_sha256': '650c3cf8c79a680e6dd8794b3e87e16dacf195620a2240ac34fd0b2d0a2ae4a8',
                             'reflection': 'decal_asphalt_road_worn_b_tex_r',
                             'reflection_sha256': '70306b1316242459b9ee63ef2b33c857536f4aa78b346c79c4c51a81641c0499',
                             'source_shape_manifest_sha256': 'cc116516eb3d2c9d026bb4ac191311083fe2cb4877a79e16bfd8e06b90b70341',
                             'source_shape_report_sha256': '5e49a9195cd0e733b03e3463255136044dd470d6f515f390b7b995f925e835c8',
                             'source_uv_boundary_witnesses': 2164,
                             'visual_reference': 'artifacts/maps/wt-visual-review-20261119/asphalt-probe-default-1600-02/view.png'},
 'WT_f71c86623ca459a14d14': {'basis': 'Original RGB/alpha silhouette and source UV-boundary witnesses; root reviewed '
                                      'all 18 source-shape panels',
                             'diffuse': 'ground_decal_debris_bricks_a_tex_d',
                             'diffuse_sha256': 'c96265bde254d429150dd1486992e46c882bbca037f6a361c3d0cc789b078f90',
                             'height_above': 1,
                             'reflection': 'ground_decal_debris_bricks_a_tex_r',
                             'reflection_sha256': 'c3ee043882bd21592d0f709d3324c92b9959b9c9dc827d0819e969287fa14a27',
                             'source_shape_manifest_sha256': 'cc116516eb3d2c9d026bb4ac191311083fe2cb4877a79e16bfd8e06b90b70341',
                             'source_shape_report_sha256': '5e49a9195cd0e733b03e3463255136044dd470d6f515f390b7b995f925e835c8',
                             'source_uv_boundary_witnesses': 1680},
 'WT_fd08e5ab7bca8dffbd0d': {'basis': 'Original RGB/alpha silhouette and source UV-boundary witnesses; root reviewed '
                                      'all 18 source-shape panels',
                             'diffuse': 'gravel_border_tex_d',
                             'diffuse_sha256': 'd89eda130698a6724cefff12897bd10f6390ac2a69b3a7f4a531d896994b3f8a',
                             'height_above': 1,
                             'reflection': 'river_sand_gravel_coast_border_tex_r',
                             'reflection_sha256': '5521210dd3766243a961d1c8e2f45a0a907ef28533a2340bd121aa165672c563',
                             'source_shape_manifest_sha256': 'cc116516eb3d2c9d026bb4ac191311083fe2cb4877a79e16bfd8e06b90b70341',
                             'source_shape_report_sha256': '5e49a9195cd0e733b03e3463255136044dd470d6f515f390b7b995f925e835c8',
                             'source_uv_boundary_witnesses': 379}}
COOKED_REFLECTION_CHANNELS = dict(smoothness='R', reflectance='G',
                                  ambient_occlusion='B', microdetail_id='A')
COOKED_REFLECTION_LAYOUT = dict(
    schema='wt_cooked_ground_reflection_barg_v1', build_swizzle_argb='BARG',
    source_rgba=dict(R='reflectance', G='ambient_occlusion', B='microdetail_id', A='smoothness'),
    cooked_rgba=dict(R='smoothness', G='reflectance', B='ambient_occlusion', A='microdetail_id'),
    rule='WarThunderCDK/develop/assets/entities/sample_ground_decal/.folder.blk',
    rule_sha256='4235b832b5b4f3c44e69c7823a6d6356d382896444b12e7913c3f9e18040453f',
    same_shader_dag_sha256='0d7b7f568ec72ebb3342a2d90567be71b26c8e941e2d8c29d6e10c08d055ad99',
    exporter='prog/tools/sceneTools/assetExp/exporters/texExp.cpp:1796-1803,2377-2407',
    exporter_sha256='57c1ddf4bccdfb1e5d9f37b9181129a978dbc14814334fee2e0e7ba30beb6991',
    scope='Audited Flanders d/null/r/null resources; source bytes retained')
SLOT_COUNT = 12
ID_COUNT_SCALE = struct.unpack('<f', struct.pack('<f', 255 / (255 // (SLOT_COUNT - 1))))[0]
HEIGHT_SOURCE = (SOURCE_BUMP_SCALE, ID_COUNT_SCALE, SLOT_COUNT + 1, 0)


def normal_adapter():
    return dict(kind='ue_no_normal_height_gradient_scale',parameter='HeightNormalStrength',
                value=HEIGHT_NORMAL_STRENGTH,source_bump_scale=SOURCE_BUMP_SCALE,
                source_height_domain='normalized_source_uv_height',source_metres=False)


def normal_adapter_metadata():
    return json.dumps(normal_adapter(),sort_keys=True,separators=(',',':'))


def reflection_metadata():
    return json.dumps(COOKED_REFLECTION_LAYOUT,sort_keys=True,separators=(',',':'))


def height_polarity_contract(spec):
    adapter = HEIGHT_POLARITY_ADAPTERS.get(spec['id'])
    if adapter is not None:
        slots = spec.get('texture_slots', [])
        names = [slots[i].split('*')[0].split('$')[0] if i < len(slots) and slots[i] else '' for i in (0, 2)]
        if spec.get('shader') != SHADER or names != [adapter['diffuse'], adapter['reflection']]:
            raise ValueError('approved height-polarity source-id/texture binding differs')
    declared = None
    for line in spec.get('parameters', '').splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            if key.strip() == 'height_above':
                declared = float(value.split(',')[0].strip())
                if declared not in (0, 1):
                    raise ValueError('unsupported explicit source height_above')
    active = adapter is not None and declared is None
    value = declared if declared is not None else adapter['height_above'] if active else 0
    return dict(schema='wt_height_polarity_adapter_v1', material_id=spec['id'],
                value=value, source_explicit=declared is not None,
                origin='source_parameter' if declared is not None else 'source_bound_ue_visual_adapter' if active else 'existing_ue_default',
                adapter_active=active, adapter=dict(adapter) if adapter is not None else None)


def height_polarity_metadata(spec):
    return json.dumps(height_polarity_contract(spec),sort_keys=True,separators=(',',':'))


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
        slot_mapping='Explicit adaptation of original d/null/r/null resources using their cooked BARG reflection channels',
        reflection_channels=dict(COOKED_REFLECTION_CHANNELS),
        cooked_reflection_layout=dict(COOKED_REFLECTION_LAYOUT),
        normal='Published no-normal alpha-height finite differences and perturb_normal_height2d; clipmap XY becomes UE world XY',
        source_bump_scale=SOURCE_BUMP_SCALE, ue_normal_adapter=normal_adapter(),
        height_source=list(HEIGHT_SOURCE), height_polarity=height_polarity_contract(spec),
        normal_space='world_aligned_clipmap; combined with receiver geometric normal by RNM',
        rvt_microdetail_payload='(1 + clamp(T2.a * id_count_scale, 0, slot_count - 1)) / (slot_count + 1)',
        rvt_payload_channel='Specular', rvt_mask='coverage one before native opacity multiplication',
        textures=dict(T0=dict(srgb=True, compression='TC_DEFAULT'),
                      T2=dict(srgb=False, compression='TC_VECTOR_DISPLACEMENTMAP')),
        ue_surface=dict(roughness='clamp(1 - source_reflection.r, 0.04, 1)', metallic=0),
        source_reflectance_and_ao_rendered=False, original_geometry_and_png_unchanged=True,
        dagor_commit=DAGOR_COMMIT,
        references=['prog/daNetGame/shaders/make_clipmap.dshl:1397-1435',
                    'prog/gameLibs/render/shaders/psh_derivate.hlsl:33-48',
                    'prog/tools/sceneTools/assetExp/exporters/texExp.cpp:1796-1803,2377-2407',
                    'WarThunderCDK/develop/assets/entities/sample_ground_decal/.folder.blk'])


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
RoughnessOut=clamp(1-reflection.r,0.04,1);
MetallicOut=0;
float sourceId=clamp(reflection.a*HeightSource.y,0,HeightSource.z-2);
MicrodetailPayloadOut=(1+sourceId)/HeightSource.z;
float sourceAlpha=(1-2*HeightBlend.z)*c.a+HeightBlend.z;
float vertexAlpha=pow(OpacityUV.x,HeightBlend.y);
OpacityOut=saturate((vertexAlpha-sourceAlpha)/(0.0001+HeightBlend.x));
return max(c.rgb,0);
'''
