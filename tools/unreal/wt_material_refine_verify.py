"""Read-only native checks for source rendinst decals and indexed ground details."""
import hashlib
import json
from pathlib import Path
import struct

import unreal
import wt_rendinst_decals as decals
import wt_landdetail as detail


def _require(value,message):
    if not value:
        raise RuntimeError(message)


def _f32(value):
    return struct.unpack('<f',struct.pack('<f',float(value)))[0]


def _vec(value,expected,name):
    actual=[float(value.get_editor_property(c)) for c in ('r','g','b','a')]
    _require(actual==[_f32(v) for v in expected],'source decal vector differs: '+name)
    return actual


def _file(asset):
    path=asset.get_path_name().split('.',1)[0]
    _require(path.startswith('/Game/'),'expected a saved project asset')
    return Path(unreal.Paths.project_content_dir()).resolve()/(path[6:]+'.uasset')


def decal_parent(material):
    name=material.get_name()
    kind=name.removeprefix('WT_Parent_')
    if kind not in decals.FLAVORS:
        return None
    edit,prop=unreal.MaterialEditingLibrary,unreal.MaterialProperty
    base=edit.get_material_property_input_node(material,prop.MP_BASE_COLOR)
    emissive=edit.get_material_property_input_node(material,prop.MP_EMISSIVE_COLOR)
    normal=edit.get_material_property_input_node(material,prop.MP_NORMAL)
    expected_code={'DecalModulate':decals.MODULATE_CODE,'DecalDiffuse':decals.DIFFUSE_CODE,
                   'DecalNormal':decals.NORMAL_CODE}[kind]
    nodes=list(edit.get_material_expressions(material))
    surfaces=[n for n in nodes if isinstance(n,unreal.MaterialExpressionCustom)
              and str(n.get_editor_property('code'))==expected_code]
    _require(len(surfaces)==1,'source decal surface code differs')
    surface=surfaces[0]
    if kind=='DecalModulate':
        _require(base is None and normal is None and emissive==surface,
                 'multiply decal must write only its unlit factor')
        _require(material.get_editor_property('material_domain')==unreal.MaterialDomain.MD_SURFACE
                 and material.get_editor_property('blend_mode')==unreal.BlendMode.BLEND_MODULATE
                 and material.get_editor_property('shading_model')==unreal.MaterialShadingModel.MSM_UNLIT
                 and material.get_editor_property('translucency_pass')==unreal.MaterialTranslucencyPass.MTP_BEFORE_DOF,
                 'multiply decal domain/blend/pass differs')
    else:
        _require(material.get_editor_property('material_domain')==unreal.MaterialDomain.MD_DEFERRED_DECAL
                 and material.get_editor_property('blend_mode')==unreal.BlendMode.BLEND_TRANSLUCENT,
                 'source mesh decal is missing native DeferredDecal domain')
        _require(normal==surface and edit.get_material_property_input_node(material,prop.MP_OPACITY)==surface,
                 'source normal/coverage output is disconnected')
        _require(base is None if kind=='DecalNormal' else base==surface,
                 'normal-only must not write BaseColor; diffuse must retain original color')
    _require(material.get_editor_property('used_with_instanced_static_meshes')
             and material.get_editor_property('two_sided'),'source mesh decal HISM/sidedness differs')
    wpo=edit.get_material_property_input_node(material,prop.MP_WORLD_POSITION_OFFSET)
    _require(isinstance(wpo,unreal.MaterialExpressionCustom)
             and str(wpo.get_editor_property('code'))=='return normalize(Camera-World)*OffsetCm;',
             'source five-millimetre camera offset is missing')
    fields=[str(x) for x in edit.get_material_expression_input_names(wpo)]
    inputs=dict(zip(fields,edit.get_inputs_for_material_expression(material,wpo)))
    offset=inputs.get('OffsetCm')
    _require(isinstance(offset,unreal.MaterialExpressionScalarParameter)
             and float(offset.get_editor_property('default_value'))==decals.CAMERA_OFFSET_CM,
             'source camera offset scale differs')
    return dict(flavor=kind,code_sha256=hashlib.sha256(expected_code.encode()).hexdigest(),
                base_color_written=base is not None,emissive_factor_written=emissive is not None,
                source_camera_offset_cm=decals.CAMERA_OFFSET_CM,
                original_geometry_unchanged=True,
                independent_attribute_opacity=False if kind=='DecalDiffuse' else True)


def decal_instance(material,spec,package,inventory):
    edit=unreal.MaterialEditingLibrary
    data=decals.contract(spec)
    root=material.get_path_name().rsplit('/Materials/',1)[0]
    vectors={'DepthRange':data['depth_range_cm']}
    if data['flavor']=='DecalModulate':vectors['Intensity']=data['intensity']
    elif data['flavor']=='DecalNormal':vectors['NormalGamma']=data['normal_gamma']
    else:vectors['GammaMask']=data['gamma_mask']
    actual={name:_vec(edit.get_material_instance_vector_parameter_value(material,name),expected,name)
            for name,expected in vectors.items()}
    _require(edit.get_material_instance_scalar_parameter_value(material,'SourceCameraOffsetCm')==decals.CAMERA_OFFSET_CM,
             'instance source camera offset differs')
    if data['flavor']=='DecalDiffuse':
        _require(edit.get_material_instance_scalar_parameter_value(material,'SourceAtest')==data['atest'],
                 'source decal alpha-test branch differs')
    slots=(0,) if data['flavor']=='DecalModulate' else (2,) if data['flavor']=='DecalNormal' else (0,2)
    textures={}
    for index in slots:
        raw=spec['texture_slots'][index]
        name=raw.split('*')[0].split('$')[0] if raw else '_DefaultNormalAG'
        color=index==0 and data['flavor']=='DecalDiffuse'
        suffix='_C' if color else '_D'
        path=root+'/Textures/'+name+suffix+'.'+name+suffix
        texture=edit.get_material_instance_texture_parameter_value(material,'T'+str(index))
        compression=unreal.TextureCompressionSettings.TC_DEFAULT if color else unreal.TextureCompressionSettings.TC_VECTOR_DISPLACEMENTMAP
        _require(isinstance(texture,unreal.Texture2D) and texture.get_path_name()==path
                 and bool(texture.get_editor_property('srgb'))==color
                 and texture.get_editor_property('compression_settings')==compression,
                 'source decal texture path/gamma/packing differs: '+name)
        entry=inventory[name] if raw else None
        if entry:
            _require(detail.digest(Path(package)/'textures'/(name+'.png'))==entry['sha256'],
                     'original decal PNG bytes differ: '+name)
        textures['T'+str(index)]=dict(asset=path,srgb=color,source_png_sha256=entry['sha256'] if entry else None,
                                    asset_sha256=detail.digest(_file(texture)))
    return dict(source_contract=data,actual_vectors=actual,texture_bindings=textures)


def landdetail_graph(material,data,package):
    edit=unreal.MaterialEditingLibrary
    expected=detail.shader_code(data)
    nodes=[n for n in edit.get_material_expressions(material) if isinstance(n,unreal.MaterialExpressionCustom)
           and str(n.get_editor_property('code'))==expected]
    _require(len(nodes)==1,'saved ground detail code/table differs from original source')
    node=nodes[0];fields=[str(x) for x in edit.get_material_expression_input_names(node)]
    from wt_ground_material_verify import _inputs, _reroute
    metadata=str(unreal.EditorAssetLibrary.get_metadata_tag(material,detail.NORMAL_ADAPTER_TAG))
    _require(metadata==detail.normal_adapter_metadata(),'saved LC1 local-normal adapter metadata differs')
    normal_field='DetailNormalWorld' if material.get_name()=='WT_Parent_Land' else 'BaseNormalWorld'
    bindings=[]
    for consumer in edit.get_material_expressions(material):
        if not isinstance(consumer,unreal.MaterialExpressionCustom) or consumer==node:
            continue
        incoming=_inputs(material,consumer)
        reroute=incoming.get(normal_field)
        if not isinstance(reroute,unreal.MaterialExpressionReroute):
            continue
        linked=list(edit.get_inputs_for_material_expression(material,reroute))
        if len(linked)==1 and linked[0]==node:
            bindings.append(dict(consumer=consumer.get_path_name(),input=normal_field,
                                 **_reroute(material,reroute,node,'NormalOut')))
    _require(len(bindings)==1,'LC1 local normal must feed exactly one saved receiver through NormalOut')
    upstream=dict(zip(fields,edit.get_inputs_for_material_expression(material,node)))
    _require(set(upstream)=={'World','VertexNormalWorld','Macro','Index','Flow','Details'}
             and all(upstream.values()),'source ground detail inputs are disconnected')
    array=upstream['Details'].get_editor_property('texture')
    _require(isinstance(array,unreal.Texture2DArray),'ground details must retain independent array slices')
    before=detail.digest(_file(array))
    native=json.loads(unreal.MapImportLibrary.describe_map_texture_array(array))
    root=material.get_path_name().rsplit('/Materials/',1)[0]
    expected_slices=[root+'/Textures/'+name+'_C.'+name+'_C' for name in data['table']['albedo_names']]
    _require(native.get('width')==1024 and native.get('height')==1024 and native.get('slices')==23
             and native.get('source_width')==1024 and native.get('source_height')==1024
             and native.get('source_slices')==23 and native.get('source_textures')==expected_slices
             and native.get('mips',0)>1,'native source detail array dimensions/slices differ')
    _require(bool(array.get_editor_property('srgb')) and bool(array.get_editor_property('never_stream'))
             and array.get_editor_property('compression_settings')==unreal.TextureCompressionSettings.TC_DEFAULT
             and array.get_editor_property('lod_bias')==0 and array.get_editor_property('max_texture_size')==0,
             'source detail array gamma/resolution differs')
    _require(unreal.EditorAssetLibrary.get_metadata_tag(array,'WT_SourceDefinitionSHA256')==
             detail.digest(Path(package)/detail.DEFINITION_FILE),'source array definition binding differs')
    texture_bindings={}
    for parameter,name,srgb in [('Macro',detail.MACRO_NAME,True),('Index',detail.INDEX_NAME,False),('Flow',detail.FLOW_NAME,False)]:
        texture=upstream[parameter].get_editor_property('texture')
        suffix=('_C' if srgb else '_D')+('_Point' if parameter=='Index' else '')
        path=root+'/Textures/'+name+suffix+'.'+name+suffix
        _require(isinstance(texture,unreal.Texture2D) and texture.get_path_name()==path
                 and bool(texture.get_editor_property('srgb'))==srgb,
                 'source macro/index/flow binding differs')
        if parameter=='Index':
            _require(texture.get_editor_property('filter')==unreal.TextureFilter.TF_NEAREST
                     and texture.get_editor_property('mip_gen_settings')==unreal.TextureMipGenSettings.TMGS_NO_MIPMAPS
                     and bool(texture.get_editor_property('never_stream')),'biome categorical IDs must not have averaged mips')
        texture_bindings[parameter]=dict(asset=path,source_sha256=data['textures'][name]['sha256'])
    _require(detail.digest(_file(array))==before,'read-only array audit changed its saved file')
    return dict(definition_sha256=detail.digest(Path(package)/detail.DEFINITION_FILE),
                shader_code_sha256=hashlib.sha256(expected.encode()).hexdigest(),
                native_array=native,array_sha256=before,texture_bindings=texture_bindings,
                source_biome_count=65,source_package_adapter_note=data['adapter']['normal'],
                normal_adapter=json.loads(metadata),normal_binding=bindings[0],
                native_visual_quality_verified=False)
