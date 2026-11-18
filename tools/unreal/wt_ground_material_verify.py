"""Read-only native contracts for original height decals and post-RVT microdetails."""
import hashlib
import json
from pathlib import Path
import re
import struct

import unreal
import wt_ground_microdetails as micro
import wt_height_decals as height


def _require(condition, message):
    if not condition:
        raise RuntimeError(message)


def _f32(value):
    return struct.unpack('<f', struct.pack('<f', float(value)))[0]


def _vector(value, expected, name):
    actual = [float(value.get_editor_property(c)) for c in ('r','g','b','a')]
    _require(actual == [_f32(v) for v in expected], 'source height vector differs: '+name)
    return actual


def _file(asset):
    path = asset.get_path_name().split('.',1)[0]
    _require(path.startswith('/Game/'), 'expected saved /Game material asset')
    return Path(unreal.Paths.project_content_dir()).resolve()/(path[6:]+'.uasset')


def _inputs(material, node):
    edit = unreal.MaterialEditingLibrary
    names = [str(x) for x in edit.get_material_expression_input_names(node)]
    nodes = list(edit.get_inputs_for_material_expression(material,node))
    _require(len(names)==len(nodes), 'native material input list lengths differ')
    return dict(zip(names,nodes))


def _output_pin(target, source, expected):
    # C++ returns bool plus FString&. UE Python versions expose str or tuple.
    value = unreal.MaterialEditingLibrary.get_input_node_output_name_for_material_expression(target,source)
    if isinstance(value,str):
        actual = value
    elif isinstance(value,(tuple,list)):
        _require(not any(isinstance(x,bool) and not x for x in value),
                 'native output-pin lookup failed')
        names = [str(x) for x in value if isinstance(x,str)]
        _require(len(names)==1, 'native output-pin return contract differs: '+repr(value))
        actual = names[0]
    else:
        raise RuntimeError('native output-pin return contract differs: '+repr(value))
    _require(actual==expected, 'serialized material output pin differs: '+actual+' != '+expected)
    return actual


def _reroute(material, node, source, expected):
    _require(isinstance(node,unreal.MaterialExpressionReroute),
             'source output must retain its unambiguous serialized reroute')
    upstream = list(unreal.MaterialEditingLibrary.get_inputs_for_material_expression(material,node))
    _require(len(upstream)==1 and upstream[0]==source, 'saved reroute source differs')
    return dict(node=node.get_path_name(), source=source.get_path_name(),
                output_pin=_output_pin(node,source,expected))


def _property(material, prop, source, pin):
    edit = unreal.MaterialEditingLibrary
    _require(edit.get_material_property_input_node(material,prop)==source,
             'saved material property source differs: '+str(prop))
    actual = str(edit.get_material_property_input_node_output_name(material,prop))
    _require(actual==pin, 'saved material property output differs: '+actual+' != '+pin)
    return actual


def texture_state(texture):
    _require(isinstance(texture,unreal.Texture2D), 'original height input is not Texture2D')
    unreal.SystemLibrary.execute_console_command(None,'Editor.AsyncTextureCompilationFinishAll')
    return dict(asset=texture.get_path_name(), srgb=bool(texture.get_editor_property('srgb')),
                compression=str(texture.get_editor_property('compression_settings')),
                size=[int(texture.blueprint_get_size_x()),int(texture.blueprint_get_size_y())],
                asset_file_sha256=micro.digest(_file(texture)))


def height_instance(material, spec, package, inventory, data):
    source = height.contract(spec,data)
    from wt_materials import height_blend_parameters
    edit = unreal.MaterialEditingLibrary
    root = material.get_path_name().rsplit('/Materials/',1)[0]
    actual = _vector(edit.get_material_instance_vector_parameter_value(material,'HeightSource'),
                     height.HEIGHT_SOURCE,'HeightSource')
    blend = _vector(edit.get_material_instance_vector_parameter_value(material,'HeightBlend'),
                    height_blend_parameters(spec),'HeightBlend')
    strength = float(edit.get_material_instance_scalar_parameter_value(material,'HeightNormalStrength'))
    _require(strength==_f32(height.HEIGHT_NORMAL_STRENGTH)
             and edit.is_material_instance_parameter_overridden(material,'HeightNormalStrength'),
             'height-decal UE normal-strength override differs')
    metadata = str(unreal.EditorAssetLibrary.get_metadata_tag(material,height.HEIGHT_NORMAL_METADATA_TAG))
    _require(metadata==height.normal_adapter_metadata(), 'height-decal UE normal-strength metadata differs')
    textures = {}
    for index,color in ((0,True),(2,False)):
        name = spec['texture_slots'][index].split('*')[0].split('$')[0]
        asset_name = re.sub('[^A-Za-z0-9_]','_',name)+('_C' if color else '_D')
        expected = root+'/Textures/'+asset_name+'.'+asset_name
        texture = edit.get_material_instance_texture_parameter_value(material,'T'+str(index))
        item = texture_state(texture)
        compression = (unreal.TextureCompressionSettings.TC_DEFAULT if color
                       else unreal.TextureCompressionSettings.TC_VECTOR_DISPLACEMENTMAP)
        _require(texture.get_path_name()==expected and item['srgb']==color
                 and texture.get_editor_property('compression_settings')==compression,
                 'original height texture binding/gamma/packing differs: '+name)
        entry = inventory[name]
        _require(micro.digest(Path(package)/'textures'/(name+'.png'))==entry['sha256'],
                 'original height source PNG bytes differ: '+name)
        _require(item['size']==entry['size'], 'native height texture dimensions differ: '+name)
        item.update(source_png_sha256=entry['sha256'], source_size=entry['size'])
        textures['T'+str(index)] = item
    return dict(source_contract=source,actual_height_source=actual,actual_height_blend=blend,
                actual_height_normal_strength=strength,height_normal_adapter_metadata=json.loads(metadata),
                texture_bindings=textures)


def rvt_writer(material, data):
    edit,prop = unreal.MaterialEditingLibrary,unreal.MaterialProperty
    is_height = material.get_name()=='WT_Parent_HeightDecal'
    base = edit.get_material_property_input_node(material,prop.MP_BASE_COLOR)
    writers = [n for n in edit.get_material_expressions(material)
               if isinstance(n,unreal.MaterialExpressionRuntimeVirtualTextureOutput)]
    _require(len(writers)==1,'ground source must retain one RVT writer')
    upstream = _inputs(material,writers[0])
    pins = {}
    for field,pin in (('BaseColor','return'),('Roughness','RoughnessOut'),('Opacity','OpacityOut')):
        pins[field] = _reroute(material,upstream.get(field),base,pin)
    mask = upstream.get('Mask')
    _require(isinstance(mask,unreal.MaterialExpressionConstant)
             and float(mask.get_editor_property('r'))==1,
             'RVT coverage must write raw one before native opacity multiplication')
    if is_height:
        _require(str(base.get_editor_property('code'))==height.HEIGHT_DECAL_CODE,
                 'source height/reflectance shader differs')
        _require(not material.get_editor_property('tangent_space_normal')
                 and material.get_editor_property('blend_mode')==unreal.BlendMode.BLEND_TRANSLUCENT
                 and material.get_editor_property('used_with_instanced_static_meshes')
                 and material.get_editor_property('two_sided'),
                 'height world-normal, blend, HISM or sidedness differs')
        for field,pin in ((prop.MP_BASE_COLOR,'return'),(prop.MP_NORMAL,'NormalOut'),
                          (prop.MP_ROUGHNESS,'RoughnessOut'),(prop.MP_OPACITY,'OpacityOut')):
            _property(material,field,base,pin)
        pins['Normal'] = _reroute(material,upstream.get('Normal'),base,'NormalOut')
        pins['Specular'] = _reroute(material,upstream.get('Specular'),base,'MicrodetailPayloadOut')
        inputs = _inputs(material,base)
        for key,index in (('UV0',0),('OpacityUV',1)):
            node = inputs.get(key)
            _require(isinstance(node,unreal.MaterialExpressionTextureCoordinate)
                     and int(node.get_editor_property('coordinate_index'))==index,
                     'original source height/opacity UV differs')
        node = inputs.get('HeightSource')
        _require(isinstance(node,unreal.MaterialExpressionVectorParameter)
                 and str(node.get_editor_property('parameter_name'))=='HeightSource',
                 'source height/reflection parameter is absent')
        vector = _vector(node.get_editor_property('default_value'),height.HEIGHT_SOURCE,'HeightSource')
        strength_node = inputs.get('HeightNormalStrength')
        _require(isinstance(strength_node,unreal.MaterialExpressionScalarParameter)
                 and str(strength_node.get_editor_property('parameter_name'))=='HeightNormalStrength',
                 'height-decal UE normal-strength source is not its scalar parameter')
        strength = float(strength_node.get_editor_property('default_value'))
        _require(strength==_f32(height.HEIGHT_NORMAL_STRENGTH), 'height-decal parent normal strength differs')
        metadata = str(unreal.EditorAssetLibrary.get_metadata_tag(material,height.HEIGHT_NORMAL_METADATA_TAG))
        _require(metadata==height.normal_adapter_metadata(), 'height-decal parent adapter metadata differs')
        return dict(height_surface=True,source_shader_code_sha256=hashlib.sha256(height.HEIGHT_DECAL_CODE.encode()).hexdigest(),
                    output_pins=pins,height_source=vector,height_normal_strength=strength,
                    height_normal_strength_node=strength_node.get_path_name(),
                    height_normal_adapter_metadata=json.loads(metadata),
                    raw_mask=1,native_opacity_multiplication_count=1,
                    source_definition_sha256=micro.DEFINITION_SHA256)
    payload = upstream.get('Specular')
    _require(isinstance(payload,unreal.MaterialExpressionConstant)
             and float(payload.get_editor_property('r'))==0,
             'ground decals without verified source IDs must write invalid zero payload')
    normal = upstream.get('Normal')
    _require(isinstance(normal,unreal.MaterialExpressionTransform),
             'generic ground decal normal transform is absent')
    _output_pin(normal,base,'NormalOut')
    return dict(height_surface=False,output_pins=pins,raw_mask=1,raw_microdetail_payload=0,
                native_opacity_multiplication_count=1)


def microdetail_graph(material, data, composite_code):
    edit,prop = unreal.MaterialEditingLibrary,unreal.MaterialProperty
    expressions = list(edit.get_material_expressions(material))
    expected = micro.shader_code(data)
    posts = [n for n in expressions if isinstance(n,unreal.MaterialExpressionCustom)
             and str(n.get_editor_property('code'))==expected]
    composites = [n for n in expressions if isinstance(n,unreal.MaterialExpressionCustom)
                  and str(n.get_editor_property('code'))==composite_code]
    samples = [n for n in expressions if isinstance(n,unreal.MaterialExpressionRuntimeVirtualTextureSample)]
    _require(len(posts)==len(composites)==len(samples)==1,
             'ground receiver requires one source-over composition and one post-RVT detail layer')
    post,composite,sample = posts[0],composites[0],samples[0]
    for field,pin in ((prop.MP_BASE_COLOR,'return'),(prop.MP_NORMAL,'NormalOut'),
                      (prop.MP_ROUGHNESS,'RoughnessOut'),(prop.MP_METALLIC,'MetallicOut')):
        _property(material,field,post,pin)
    inputs = _inputs(material,post)
    _require(set(inputs)=={'BaseColor','BaseNormalWorld','BaseRoughness','BaseMetallic','DecalMask',
                          'DecalIdPayload','World','BoundsMin','BoundsMax','Microdetails'}
             and all(inputs.values()) and inputs['BaseColor']==composite,
             'post-RVT microdetail input closure differs')
    pins = {}
    for field,pin in (('BaseNormalWorld','NormalOut'),('BaseRoughness','RoughnessOut'),('BaseMetallic','MetallicOut')):
        pins[field] = _reroute(material,inputs[field],composite,pin)
    for field,pin in (('DecalMask','Mask'),('DecalIdPayload','Specular')):
        pins[field] = _reroute(material,inputs[field],sample,pin)
    source = _inputs(material,composite)
    _require(isinstance(source.get('BaseGeometricNormal'),unreal.MaterialExpressionVertexNormalWS),
             'source clipmap RNM is missing the geometric terrain normal')
    for field,pin in (('DecalColor','BaseColor'),('DecalNormalWorld','Normal'),
                      ('DecalRoughness','Roughness'),('DecalMask','Mask')):
        pins['composite_'+field] = _reroute(material,source[field],sample,pin)
    _require(isinstance(inputs['World'],unreal.MaterialExpressionWorldPosition)
             and all(inputs[key]==source[key] for key in ('World','BoundsMin','BoundsMax')),
             'microdetail world coordinates/bounds diverged from the RVT receiver')
    parameter = inputs['Microdetails']
    _require(isinstance(parameter,unreal.MaterialExpressionTextureObjectParameter)
             and str(parameter.get_editor_property('parameter_name'))=='SourceGroundMicrodetails',
             'source microdetail texture-array parameter differs')
    array = parameter.get_editor_property('texture')
    _require(isinstance(array,unreal.Texture2DArray), 'original microdetails require an array')
    before = micro.digest(_file(array))
    native = json.loads(unreal.MapImportLibrary.describe_map_texture_array(array))
    root = material.get_path_name().rsplit('/Materials/',1)[0]
    expected_paths = [root+'/Textures/'+s['name']+'_D.'+s['name']+'_D' for s in data['slots']]
    _require(native.get('width')==native.get('height')==native.get('source_width')==native.get('source_height')==512
             and native.get('slices')==native.get('source_slices')==12 and native.get('mips',0)>1
             and native.get('source_textures')==expected_paths and expected_paths[4]==expected_paths[11],
             'native original microdetail dimensions/slice order differ')
    _require(not array.get_editor_property('srgb')
             and array.get_editor_property('compression_settings')==unreal.TextureCompressionSettings.TC_VECTOR_DISPLACEMENTMAP
             and array.get_editor_property('address_x')==unreal.TextureAddress.TA_WRAP
             and array.get_editor_property('address_y')==unreal.TextureAddress.TA_WRAP
             and array.get_editor_property('address_z')==unreal.TextureAddress.TA_CLAMP
             and array.get_editor_property('mip_gen_settings')==unreal.TextureMipGenSettings.TMGS_SIMPLE_AVERAGE
             and bool(array.get_editor_property('never_stream'))
             and array.get_editor_property('lod_bias')==0 and array.get_editor_property('max_texture_size')==0
             and unreal.EditorAssetLibrary.get_metadata_tag(array,'WT_SourceDefinitionSHA256')==micro.DEFINITION_SHA256,
             'microdetail array gamma/mips/packing/source binding differs')
    slices = list(array.get_editor_property('source_textures'))
    slice_files = {}
    for slot,texture in zip(data['slots'],slices):
        if texture.get_path_name() in slice_files:
            continue
        item = texture_state(texture)
        _require(item['size']==[512,512] and not item['srgb']
                 and texture.get_editor_property('compression_settings')==unreal.TextureCompressionSettings.TC_VECTOR_DISPLACEMENTMAP
                 and unreal.EditorAssetLibrary.get_metadata_tag(texture,'WT_GroundMicrodetailPNG_SHA256')==slot['texture_sha256'],
                 'microdetail source slice encoding/source binding differs')
        item['source_png_sha256']=slot['texture_sha256']
        slice_files[texture.get_path_name()]=item
    _require(micro.digest(_file(array))==before, 'read-only microdetail array gate changed its asset file')
    return dict(definition_sha256=micro.DEFINITION_SHA256,source_proof_sha256=data['proof_sha256'],
                native_array=native,array_file_sha256=before,slice_files=slice_files,output_pins=pins,
                shader_code_sha256=hashlib.sha256(expected.encode()).hexdigest(),
                composition_order='source_land_detail_then_RVT_source_over_then_source_microdetail',
                source_uv_scale_per_m=data['uv_scale_per_m'],source_id_scale=data['id_count_scale'],
                transport='raw_specular_payload_native_opacity_then_clear_recovery',
                selection='continuous_floor_next_fraction_with_coverage_and_valid_payload_fade',
                native_visual_quality_verified=False)
