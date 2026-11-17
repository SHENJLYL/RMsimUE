"""Read the existing Blender source without saving it; audit/export into this workspace."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
import zlib

import bpy
import bmesh
import numpy as np
from mathutils import Matrix, Vector
sys.path.insert(0,str(Path(__file__).resolve().parent))
from wheel_contact_geometry import source_vertices


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def wheel_convex_vertices(mesh):
    work = bmesh.new()
    try:
        work.from_mesh(mesh)
        hull = bmesh.ops.convex_hull(work, input=list(work.verts), use_existing_faces=False)
        points = sorted({tuple(float(x) for x in v.co) for item in hull['geom']
                         if isinstance(item, bmesh.types.BMFace) for v in item.verts})
        return source_vertices(points)
    finally:
        work.free()


def bounds(obj):
    points = [obj.matrix_world @ Vector(p) for p in obj.bound_box]
    return {'lo': [min(p[i] for p in points) for i in range(3)],
            'hi': [max(p[i] for p in points) for i in range(3)]}


def exact_bounds(obj):
    verts=np.empty(len(obj.data.vertices)*3,dtype=np.float32)
    obj.data.vertices.foreach_get('co',verts)
    verts=verts.reshape(-1,3)
    transform=np.asarray(obj.matrix_world)
    verts=verts @ transform[:3,:3].T + transform[:3,3]
    return {'lo':verts.min(axis=0).tolist(),'hi':verts.max(axis=0).tolist()}


def tga_rgba(path):
    """Decode the source's uncompressed true-color TGA without color transforms."""
    raw=path.read_bytes()
    ident,cmap,kind=raw[:3]
    width,height=struct.unpack_from('<HH',raw,12)
    bits,descriptor=raw[16:18]
    if cmap!=0 or kind!=2 or bits!=32:
        raise RuntimeError(f'Unsupported source TGA layout: {path}')
    pixels=np.frombuffer(raw,dtype=np.uint8,count=width*height*4,offset=18+ident).reshape(height,width,4)
    pixels=pixels[:,:,[2,1,0,3]].copy()
    if not descriptor&32: pixels=pixels[::-1]
    if descriptor&16: pixels=pixels[:,::-1]
    return pixels


def png(path,pixels):
    height,width,channels=pixels.shape
    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    color_type={1:0,3:2,4:6}[channels]
    filtered=np.zeros((height,width*channels+1),dtype=np.uint8)
    filtered[:,1:]=pixels.reshape(height,-1)
    path.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,color_type,0,0,0))+chunk(b'IDAT',zlib.compress(filtered.tobytes(),6))+chunk(b'IEND',b''))


def exported_png_pixels(path):
    raw=path.read_bytes(); position=8; compressed=[]
    while position<len(raw):
        size=struct.unpack_from('>I',raw,position)[0]; tag=raw[position+4:position+8]
        data=raw[position+8:position+8+size]; position+=size+12
        if tag==b'IHDR': width,height,depth,kind,_,_,_=struct.unpack('>IIBBBBB',data)
        if tag==b'IDAT': compressed.append(data)
    channels={0:1,2:3,6:4}[kind]
    scanlines=np.frombuffer(zlib.decompress(b''.join(compressed)),dtype=np.uint8).reshape(height,width*channels+1)
    if depth!=8 or scanlines[:,0].any(): raise RuntimeError('Unexpected generated PNG layout')
    return scanlines[:,1:].reshape(height,width,channels)


def convert_materials(materials,package,source_hashes):
    textures=package/'textures'; textures.mkdir(parents=True,exist_ok=True)
    converted={}; records=[]
    for mat in materials:
        original_nodes=mat.node_tree.nodes
        paths={n.name:Path(bpy.path.abspath(n.image.filepath)) for n in original_nodes if n.type=='TEX_IMAGE' and n.image}
        if 'tex0' not in paths or 'tex2' not in paths:
            raise RuntimeError(f'{mat.name}: no auditable source tex0/tex2')
        for path in paths.values():
            source_hashes[str(path)]=sha256(path)
        outputs={}
        color_key=str(paths['tex0'])
        if color_key not in converted:
            rgba=tga_rgba(paths['tex0'])
            color_path=textures/(paths['tex0'].stem.removesuffix('_c')+'_base_color.png')
            png(color_path,rgba)
            converted[color_key]={'base_color':color_path.relative_to(package).as_posix(),
                                  'source_color_pixels_sha256':hashlib.sha256(rgba.tobytes()).hexdigest()}
        outputs.update(converted[color_key])
        normal_key=str(paths['tex2'])
        if normal_key not in converted:
            packed=tga_rgba(paths['tex2'])
            stem=paths['tex2'].stem.removesuffix('_n')
            x=packed[:,:,3].astype(np.float32)/127.5-1
            y=packed[:,:,1].astype(np.float32)/127.5-1
            norm=np.sqrt(x*x+y*y)
            clamped=int(np.count_nonzero(norm>1))
            divisor=np.maximum(norm,1)
            x=x/divisor; y=y/divisor
            z=np.sqrt(np.maximum(0,1-x*x-y*y))
            normal_dx=np.rint((np.stack((x,y,z),axis=2)+1)*127.5).clip(0,255).astype(np.uint8)
            normal_gl=normal_dx.copy(); normal_gl[:,:,1]=255-normal_gl[:,:,1]
            channels={'normal_dx':normal_dx,'normal_gl':normal_gl,
                      'roughness':(255-packed[:,:,0:1]),'metallic':packed[:,:,2:3]}
            converted[normal_key]={}
            for key,pixels in channels.items():
                destination=textures/f'{stem}_{key}.png'
                png(destination,pixels)
                converted[normal_key][key]=destination.relative_to(package).as_posix()
            converted[normal_key]['normal_xy_clamped_pixels']=clamped
        outputs.update(converted[normal_key])
        record={'name':mat.name,'textures':{k:v for k,v in outputs.items() if k in ('base_color','normal_dx','normal_gl','roughness','metallic')},
                'base_color_factor':[1,1,1,1],'metallic_factor':1,'roughness_factor':1,
                'base_color_srgb':True,'scalar_and_normal_srgb':False,
                'opacity':float(mat.get('surface_opacity',1.0)),
                'emission_color':list(mat.get('emission_color_linear',[0,0,0,1]))[:3],
                'emission_strength':float(mat.get('emission_strength',0)),
                'source_normal_encoding':'A=X,G=Y,R=gloss,B=metallic',
                'roughness_conversion':'1 - source R; approximate perceptual roughness, not calibrated',
                'source_color_pixels_sha256':outputs['source_color_pixels_sha256'],
                'normal_xy_clamped_pixels':outputs['normal_xy_clamped_pixels'],
                'source_textures':{k:str(v) for k,v in paths.items()}}
        records.append(record)
        # Only the in-memory copy is adapted. No source material or source texture is saved.
        mat.use_nodes=True; nodes=mat.node_tree.nodes; nodes.clear()
        bsdf=nodes.new('ShaderNodeBsdfPrincipled'); out=nodes.new('ShaderNodeOutputMaterial')
        mat.node_tree.links.new(bsdf.outputs['BSDF'],out.inputs['Surface'])
        for key,socket in [('base_color','Base Color'),('roughness','Roughness'),('metallic','Metallic')]:
            node=nodes.new('ShaderNodeTexImage'); node.image=bpy.data.images.load(str(package/record['textures'][key]),check_existing=True)
            node.image.colorspace_settings.name='sRGB' if key=='base_color' else 'Non-Color'
            mat.node_tree.links.new(node.outputs['Color'],bsdf.inputs[socket])
        node=nodes.new('ShaderNodeTexImage'); node.image=bpy.data.images.load(str(package/record['textures']['normal_gl']),check_existing=True)
        node.image.colorspace_settings.name='Non-Color'
        normal=nodes.new('ShaderNodeNormalMap'); mat.node_tree.links.new(node.outputs['Color'],normal.inputs['Color'])
        mat.node_tree.links.new(normal.outputs['Normal'],bsdf.inputs['Normal'])
        bsdf.inputs['Alpha'].default_value=record['opacity']
        bsdf.inputs['Emission Color'].default_value=(*record['emission_color'],1)
        bsdf.inputs['Emission Strength'].default_value=record['emission_strength']
        if record['opacity']<1:
            bsdf.inputs['Transmission Weight'].default_value=.65
            bsdf.inputs['IOR'].default_value=1.49
    return records


def export_assets(source,package,output,report):
    package.mkdir(parents=True,exist_ok=True); (package/'model').mkdir(exist_ok=True)
    rig=json.loads((source/'rig_manifest.json').read_text(encoding='utf-8'))
    source_scale=float(rig['linear_scale'])
    spec=json.loads((source/'sentry_spec.json').read_text(encoding='utf-8-sig'))
    if not math.isfinite(source_scale) or source_scale<=0 or source_scale!=float(spec['linear_scale']):
        raise RuntimeError('Source rig/spec linear scale is invalid or inconsistent')
    # War Thunder intentionally enlarged the CAD. Robotics packages restore original metres.
    export_scale=1.0/source_scale
    source_hashes={str(source/f):sha256(source/f) for f in ['aim_sentry.blend','rig_manifest.json','component_manifest.json','material_palette.json','source_assembly.json','build_model.py','preview_model.py','sentry_spec.json','configured_parameters.json','MATERIAL_SOURCES.md']}
    visible=list(bpy.data.collections['visible_lod00'].objects)
    groups={'body':[],'yaw':[],'pitch':[]}
    pivots={'body':Vector((0,0,0)),'yaw':Vector(rig['yaw_pivot_m']),'pitch':Vector(rig['pitch_pivot_m'])}
    moving_parts={}
    for key,position in rig['wheel_centers_m'].items():
        groups['suspension_'+key]=[];pivots['suspension_'+key]=Vector(position)
        groups['steering_'+key]=[];pivots['steering_'+key]=Vector(position)
        groups['wheel_'+key]=[];pivots['wheel_'+key]=Vector(position)
        moving_parts['ex_decor_carriage_'+key]='suspension_'+key
        moving_parts['ex_decor_carrier_'+key]='steering_'+key
    for cap,key in rig['suspension_caps'].items():moving_parts[cap]='suspension_'+key
    moving_parts['ex_decor_referee_terminal']='suspension_'+rig['referee_terminal_wheel']
    source_pivots=pivots
    pivots={name:position*export_scale for name,position in source_pivots.items()}
    for obj in visible:
        if obj.name in rig['pitch_visible_parts']: group='pitch'
        elif obj.name.startswith('wheel_') and obj.name in groups: group=obj.name
        elif obj.name in moving_parts:group=moving_parts[obj.name]
        elif obj.parent and obj.parent.name=='bone_turret': group='yaw'
        else: group='body'
        groups[group].append(obj)
    if any(not members for members in groups.values()):raise RuntimeError('source suspension/visual group is empty')
    if set(moving_parts)-{obj.name for obj in visible}:raise RuntimeError('source suspension mapping references missing geometry')
    used=sorted({s.material for o in visible for s in o.material_slots if s.material},key=lambda m:m.name)
    material_records=convert_materials(used,package,source_hashes)
    original_names={obj.name for obj in bpy.data.objects}
    export_objects=[]; parts=[]
    for group,objects in groups.items():
        clones=[]
        for obj in objects:
            copy=obj.copy(); copy.data=obj.data.copy(); copy.parent=None
            bpy.context.scene.collection.objects.link(copy)
            copy.data.transform(Matrix.Scale(export_scale,4) @ Matrix.Translation(-source_pivots[group]) @ obj.matrix_world)
            copy.matrix_world=Matrix.Identity(4)
            # A source empty material slot is unused; remove it with index remapping.
            slots=[slot.material for slot in copy.material_slots]
            if any(mat is None for mat in slots):
                for polygon in copy.data.polygons:
                    if slots[polygon.material_index] is None:
                        raise RuntimeError(f'{obj.name}: used empty material slot; refusing guessed material')
                for index in reversed(range(len(slots))):
                    if slots[index] is None: copy.data.materials.pop(index=index)
            clones.append(copy)
        bpy.ops.object.select_all(action='DESELECT')
        for clone in clones: clone.select_set(True)
        bpy.context.view_layer.objects.active=clones[0]
        bpy.ops.object.join()
        merged=bpy.context.view_layer.objects.active; merged.name='export_'+group
        # Source CAD vertex alpha encodes material classes; final color comes from
        # the original atlas, so these non-color attributes must not reach UE.
        for attribute in list(merged.data.color_attributes): merged.data.color_attributes.remove(attribute)
        for uv in list(merged.data.uv_layers):
            if uv.name=='Camouflage': merged.data.uv_layers.remove(uv)
        triangulate=merged.modifiers.new('Portable triangles','TRIANGULATE')
        if hasattr(triangulate,'keep_custom_normals'): triangulate.keep_custom_normals=True
        bpy.ops.object.modifier_apply(modifier=triangulate.name)
        merged.data.calc_loop_triangles()
        parent=None if group=='body' else ('yaw' if group=='pitch' else
            'steering_'+group.removeprefix('wheel_') if group.startswith('wheel_') else
            'suspension_'+group.removeprefix('steering_') if group.startswith('steering_') else 'body')
        relative=pivots[group]-(pivots[parent] if parent else Vector((0,0,0)))
        part={'name':group,'parent':parent,'fbx':f'model/{group}.fbx',
              'origin':{'xyz':list(relative),'rpy':[0,0,0]},'pivot_world_m':list(pivots[group]),
              'rotation_axis_ros':[0,0,1] if group=='yaw' or group.startswith('steering_') else ([0,1,0] if group=='pitch' or group.startswith('wheel_') else [0,0,0]),
              'material_slots':[s.material.name for s in merged.material_slots],
              'source_objects':[o.name for o in objects],'triangles':len(merged.data.loop_triangles),
              'local_bounds_m':exact_bounds(merged)}
        if group.startswith('wheel_'):
            # Preserve the source tyre crown; a full-width cylinder invents edge contacts.
            part['collision_vertices_m'] = wheel_convex_vertices(merged.data)
        bpy.ops.export_scene.fbx(filepath=str(package/part['fbx']),use_selection=True,
            object_types={'MESH'},global_scale=1.0,apply_unit_scale=True,apply_scale_options='FBX_SCALE_UNITS',
            axis_forward='X',axis_up='Z',bake_space_transform=False,use_mesh_modifiers=True,
            mesh_smooth_type='FACE',use_tspace=True,bake_anim=False,add_leaf_bones=False,
            path_mode='RELATIVE',embed_textures=False,use_custom_props=False,colors_type='NONE')
        part['sha256']=sha256(package/part['fbx']); parts.append(part); export_objects.append(merged)
    for obj in list(bpy.data.objects):
        if obj.name in original_names: bpy.data.objects.remove(obj,do_unlink=True)
    for part,obj in zip(parts,export_objects): obj.location=part['pivot_world_m']
    bpy.context.view_layer.update()
    bounds_all=[exact_bounds(obj) for obj in export_objects]
    world_bounds={'lo':[min(b['lo'][i] for b in bounds_all) for i in range(3)],'hi':[max(b['hi'][i] for b in bounds_all) for i in range(3)]}
    manifest={'schema':'manual_asset_rig_v1','id':'aim_sentry','display_name':'AIM Sentry (existing visual asset)',
        'units':'metres','coordinate_system':'ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP',
        'source_linear_scale_vs_cad':source_scale,'export_scale_vs_blend':export_scale,'export_linear_scale_vs_cad':1.0,
        'bounds_m':world_bounds,'dimensions_m':[world_bounds['hi'][i]-world_bounds['lo'][i] for i in range(3)],
        'parts':parts,'materials':material_records,'wheel_radius_m':rig['wheel_radius_m']*export_scale,
        'drive_profile':'drive-profile.json',
        'steering':[{'part':'steering_'+key,'suspension_part':'suspension_'+key,'axis':[0,0,1]} for key in rig['wheel_centers_m']],
        'suspension':[{'part':'suspension_'+key,'steering_part':'steering_'+key,'wheel_part':'wheel_'+key,'axis':[0,0,1],
            'rest_position_m':list(pivots['suspension_'+key]),
            'source_compression_reference_m':rig['suspension_compression_m']*export_scale,
            'source_guide_length_reference_m':rig['suspension_geometry'][key]['guide_length_m']*export_scale,
            'source_guide_shift_reference_m':rig['suspension_geometry'][key]['guide_shift_m']*export_scale,
            'reference_only':True,'calibrated':False,
            'reference_note':'WT animation/geometry record only; not calibrated bump/droop travel, spring or damping'}
            for key in rig['wheel_centers_m']],
        'muzzle':{'parent':'pitch','origin':{'xyz':list(Vector(rig['muzzle_position_m'])*export_scale-pivots['pitch']),'rpy':[0,0,0]},'forward_ros':rig['muzzle_forward']},
        'fbx_export':{'axis_forward':'X','axis_up':'Z','apply_scale_options':'FBX_SCALE_UNITS','apply_unit_scale':True,'blender_unit_scale':1.0,'global_scale':1.0},
        'physics':{'mode':'geometry_for_swerve_contact_sample','mass_kg':None,'inertia':None,'calibrated':False},
        'assumptions':['The explicit War Thunder linear_scale is removed from geometry, pivots, wheels and muzzle; original CAD metres are restored.',
           'WT mass=3000kg, tracked differential, engine power, 40mm projectile and ammunition are excluded.',
           'Four translating modules carry independent Z-steering carriers and Y-rolling wheels. Carriages, caps and the referee terminal translate without steering or wheel spin.',
           'Suspension axes are ROS+Z and origins remain the source neutral wheel centres; old WT compression/guide values are reference geometry only, not physical calibration.',
           'Pitch/yaw frames use world-parallel ROS axes; source WT control-bone bases are baked into the mesh, not copied as robotics joint frames.',
           'PBR normal DX reconstructs X=A,Y=G,Z=positive sqrt(1-X^2-Y^2). GL flips green. Roughness=1-R and metallic=B; appearance conversion is not optical calibration.',
           'Collision proxy and motion limits are integration assumptions, not validated chassis dynamics.'],
        'provenance':{'source_files':[{'path':p,'sha256':h} for p,h in sorted(source_hashes.items())],
          'license':'User-provided local derivative; no blanket redistribution license is established for CAD/model assets.',
          'pbr_basis':'ambientCG Metal028 CC0 source documented in original MATERIAL_SOURCES.md; project atlas conversion preserves its current colors.'}}
    (package/'asset_import.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    # Render only exported parts reconstructed at their measured pivots.
    scene=bpy.context.scene; scene.render.engine='CYCLES'; scene.cycles.samples=16; scene.cycles.use_denoising=True
    scene.render.resolution_x=1200; scene.render.resolution_y=900; scene.render.resolution_percentage=100
    scene.view_settings.view_transform='AgX'
    scene.world.color=(.18,.18,.18)
    for location,energy,size in [((1.5,-2,2.5),400,2),((-1,1,2),250,2)]:
        bpy.ops.object.light_add(type='AREA',location=location); light=bpy.context.object
        light.data.energy=energy; light.data.shape='DISK'; light.data.size=size
        light.rotation_euler=(Vector((0,0,.4))-light.location).to_track_quat('-Z','Y').to_euler()
    bpy.ops.object.camera_add(location=(1.8,-2.2,1.45)); camera=bpy.context.object
    camera.rotation_euler=(Vector((0,0,.42))-camera.location).to_track_quat('-Z','Y').to_euler()
    camera.data.type='ORTHO'; camera.data.ortho_scale=1.65; scene.camera=camera
    scene.render.filepath=str(output/'aim_sentry_export_preview.png'); scene.render.image_settings.file_format='PNG'
    bpy.ops.render.render(write_still=True)
    unchanged=all(sha256(Path(p))==h for p,h in source_hashes.items())
    if not unchanged: raise RuntimeError('A read-only source asset changed during export')
    print('EXPORTED',len(parts),'parts;',sum(p['triangles'] for p in parts),'triangles;',len(material_records),'materials; sources unchanged')


def verify_assets(package,output):
    from io_scene_fbx import parse_fbx
    manifest=json.loads((package/'asset_import.json').read_text(encoding='utf-8'))
    parts=manifest['parts']
    by_name={part['name']:part for part in parts}
    if len(by_name)!=len(parts):raise RuntimeError('Duplicate rig part names')
    source_objects=[name for part in parts for name in part['source_objects']]
    if len(source_objects)!=len(set(source_objects)):raise RuntimeError('A source mesh belongs to multiple exported parts')
    neutral_positions={}
    def neutral_position(name,seen=()):
        if name in neutral_positions:return neutral_positions[name]
        if name in seen or name not in by_name:raise RuntimeError('Invalid part parent graph')
        part=by_name[name]
        if part['origin']['rpy']!=[0,0,0]:raise RuntimeError('This measured-pivot exporter expects parallel neutral ROS axes')
        parent=neutral_position(part['parent'],seen+(name,)) if part['parent'] else np.zeros(3)
        neutral_positions[name]=parent+np.array(part['origin']['xyz'])
        return neutral_positions[name]
    neutral_error=max(float(np.abs(neutral_position(part['name'])-part['pivot_world_m']).max()) for part in parts)
    if neutral_error>2e-7:raise RuntimeError('Hierarchy changed a neutral source pivot')
    for suspension in manifest.get('suspension',[]):
        part=by_name.get(suspension['part']);steer=by_name.get(suspension.get('steering_part'));wheel=by_name.get(suspension['wheel_part'])
        if not part or not steer or not wheel or part['parent']!='body' or steer['parent']!=part['name'] or wheel['parent']!=steer['name'] or \
            wheel['origin']['xyz']!=[0,0,0] or suspension['axis']!=[0,0,1] or suspension['reference_only'] is not True:
            raise RuntimeError('Invalid source suspension/wheel hierarchy')
        if not math.isfinite(suspension['source_compression_reference_m']) or suspension['source_compression_reference_m']<0:
            raise RuntimeError('Invalid geometric suspension reference')
    roundtrip=[]
    for part in parts:
        tree,version=parse_fbx.parse(str(package/part['fbx']))
        def child(node,name): return next(e for e in node.elems if e.id==name)
        objects=child(tree,b'Objects')
        geometries=[o for o in objects.elems if o.id==b'Geometry']
        models=[o for o in objects.elems if o.id==b'Model']
        if len(geometries)!=1 or len(models)!=1: raise RuntimeError('Expected a single static mesh FBX')
        geometry=geometries[0]; model=models[0]
        vertices=np.asarray(child(geometry,b'Vertices').props[0]).reshape(-1,3)
        indices=np.asarray(child(geometry,b'PolygonVertexIndex').props[0])
        if not np.isfinite(vertices).all(): raise RuntimeError('Nonfinite FBX vertex')
        if len(indices)%3 or not (indices[2::3]<0).all() or not (indices.reshape(-1,3)[:,:2]>=0).all():
            raise RuntimeError('FBX must contain only triangles')
        decoded=np.where(indices<0,-indices-1,indices)
        if decoded.min()<0 or decoded.max()>=len(vertices): raise RuntimeError('Invalid FBX vertex index')
        actual={'lo':vertices.min(axis=0).tolist(),'hi':vertices.max(axis=0).tolist()}
        error=max(abs(actual[k][i]-part['local_bounds_m'][k][i]) for k in ['lo','hi'] for i in range(3))
        if error>2e-5: raise RuntimeError(f"{part['name']}: roundtrip bounds drift {error}")
        if len(indices)//3!=part['triangles']: raise RuntimeError('Triangle count changed in serialized FBX')
        properties={p.props[0]:p.props[4:] for p in child(model,b'Properties70').elems}
        if properties.get(b'Lcl Translation',[0,0,0])!=[0,0,0]: raise RuntimeError('Unexpected FBX translation')
        if properties.get(b'Lcl Scaling',[1,1,1])!=[1,1,1]: raise RuntimeError('Unexpected FBX scale')
        settings={p.props[0]:p.props[4:] for p in child(child(tree,b'GlobalSettings'),b'Properties70').elems}
        if settings[b'UnitScaleFactor']!=[100.0] or settings[b'UpAxis']!=[2]: raise RuntimeError('Unexpected FBX units/axes')
        mats={o.props[0]:o.props[1].split(b'\0')[0].decode() for o in objects.elems if o.id==b'Material'}
        slots=[mats[c.props[1]] for c in child(tree,b'Connections').elems if c.props[0]==b'OO' and c.props[2]==model.props[0] and c.props[1] in mats]
        if slots!=part['material_slots']: raise RuntimeError(f"{part['name']}: material slot order changed: {slots}")
        roundtrip.append({'part':part['name'],'method':'Blender bundled pure-Python FBX binary parser; no native mesh import',
                          'fbx_version':version,'max_bounds_error_m':error,'triangles':len(indices)//3,'material_slots':slots,
                          'node_rotation_degrees':properties.get(b'Lcl Rotation',[0,0,0]),'unit_scale_factor_cm':100,'status':'PASS'})
    unchanged=all(sha256(Path(entry['path']))==entry['sha256'] for entry in manifest['provenance']['source_files'])
    if not unchanged: raise RuntimeError('A read-only source asset changed during export')
    texture_checks=[]
    for material in manifest['materials']:
        source_color=tga_rgba(Path(material['source_textures']['tex0']))
        actual_color=exported_png_pixels(package/material['textures']['base_color'])
        if not np.array_equal(source_color,actual_color): raise RuntimeError('Base color pixels changed')
        packed=tga_rgba(Path(material['source_textures']['tex2']))
        if not np.array_equal(255-packed[:,:,0:1],exported_png_pixels(package/material['textures']['roughness'])):
            raise RuntimeError('Roughness channel conversion mismatch')
        if not np.array_equal(packed[:,:,2:3],exported_png_pixels(package/material['textures']['metallic'])):
            raise RuntimeError('Metallic channel conversion mismatch')
        normal_dx=exported_png_pixels(package/material['textures']['normal_dx']).astype(np.float32)/127.5-1
        normal_length_error=float(np.max(np.abs(np.linalg.norm(normal_dx,axis=2)-1)))
        if normal_length_error>.014: raise RuntimeError('Reconstructed normal length outside 8-bit tolerance')
        texture_checks.append({'material':material['name'],'base_color_rgba_identical':True,
                               'roughness_and_metallic_channels_exact':True,'max_normal_length_error':normal_length_error,'status':'PASS'})
    result={'source_unchanged':unchanged,'exported_parts':len(parts),'triangles':sum(p['triangles'] for p in parts),
            'neutral_hierarchy_max_pivot_error_m':neutral_error,'articulated_suspension_modules':len(manifest.get('suspension',[])),
            'dimensions_m':manifest['dimensions_m'],'serialized_fbx_checks':roundtrip,
            'texture_checks':texture_checks,
            'native_import_status':'Blender 5.1.2 imported a wheel; repeated native import of the large CAD body crashed. UE import, orientation and material appearance still require UE runtime verification.',
            'files':[{'path':str(p.relative_to(package)),'bytes':p.stat().st_size,'sha256':sha256(p)} for p in sorted(package.rglob('*')) if p.is_file()]}
    (output/'aim_sentry_asset_report.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print('VERIFIED',len(parts),'parts;',result['triangles'],'triangles; sources unchanged')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', default='E:/WarThunderModding/war_thunder_sentry')
    parser.add_argument('--output', default='D:/simulator/artifacts/assets')
    parser.add_argument('--package', default='D:/simulator/assets/vehicles/aim_sentry')
    parser.add_argument('--audit-only',action='store_true')
    parser.add_argument('--verify-only',action='store_true')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    source = Path(args.source).resolve()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.verify_only:
        verify_assets(Path(args.package).resolve(),output)
        return
    blend = source / 'aim_sentry.blend'
    before = sha256(blend)
    bpy.ops.wm.open_mainfile(filepath=str(blend), load_ui=False, use_scripts=False)
    report = {'blender_version': bpy.app.version_string, 'source':str(blend),
              'source_sha256':before, 'unit_scale':bpy.context.scene.unit_settings.scale_length,
              'collections': {c.name:[o.name for o in c.objects] for c in bpy.data.collections},
              'objects': [], 'materials': [], 'images': []}
    for obj in bpy.data.objects:
        item = {'name':obj.name, 'type':obj.type, 'parent':obj.parent.name if obj.parent else None,
                'matrix_world':[list(row) for row in obj.matrix_world],
                'hide_render':obj.hide_render,'hide_viewport':obj.hide_viewport,
                'collections':[c.name for c in obj.users_collection]}
        if obj.type=='MESH':
            obj.data.calc_loop_triangles()
            item.update(bounds_m=bounds(obj),triangles=len(obj.data.loop_triangles),
                        materials=[s.material.name if s.material else None for s in obj.material_slots],
                        uv_layers=[u.name for u in obj.data.uv_layers],
                        modifiers=[{'name':m.name,'type':m.type} for m in obj.modifiers])
        report['objects'].append(item)
    for mat in bpy.data.materials:
        item={'name':mat.name, 'use_nodes':mat.use_nodes, 'diffuse_color':list(mat.diffuse_color),
              'custom': {k:str(v) for k,v in mat.items()}}
        if mat.node_tree:
            item['nodes']=[{'name':n.name,'type':n.type,'image':getattr(getattr(n,'image',None),'filepath',None)} for n in mat.node_tree.nodes]
        report['materials'].append(item)
    for img in bpy.data.images:
        report['images'].append({'name':img.name,'filepath':img.filepath,'size':list(img.size),'packed':bool(img.packed_file)})
    assert sha256(blend)==before, 'Source .blend changed during read-only audit'
    (output/'aim_sentry_source_audit.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print('AUDIT',len(report['objects']),'objects',len(report['materials']),'materials')
    if not args.audit_only: export_assets(source,Path(args.package).resolve(),output,report)


if __name__=='__main__':
    main()
