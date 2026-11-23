"""Prepare the CC-BY Indoor Basketball Court for RMsimUE5.

Windows example:
  E:\\blender\\blender.exe -b --python prepare_nguyen_venue.py -- \
      --source nguyen_indoor_basketball_court.glb --output prepared \
      --branding-dir branding --scale 1.8 --floor-z -0.305

The source is never overwritten. OBJ vertex numbers are metres in canonical
UE coordinates (ROS.x, -ROS.y, ROS.z). Import at 100 cm/m; runtime scale is 1.
"""
import argparse
import hashlib
import json
import math
import shutil
import struct
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector

SOURCE_SHA256 = '2da363924cc4786a6d859990c26a0a48ec7427083a64f32f565817aac4127d99'
SOURCE_URL = 'https://sketchfab.com/3d-models/indoor-basketball-court-943a9bc962974d02aa34592084687c3c'
DOWNLOAD_URL = 'https://huggingface.co/datasets/allenai/objaverse/resolve/main/glbs/000-154/943a9bc962974d02aa34592084687c3c.glb'
HOOP_OBJECTS = ['Object_'+str(i) for i in (119,120,122,123,125,127,129,130,132,133,135,137)]
MATERIAL_INFO = [
    ('Mat_00_Doors', 'painted_door'),
    ('Mat_01_DoorHardware', 'brushed_steel'),
    ('Mat_02_WindowFrame', 'painted_metal'),
    ('Mat_03_WindowGlass', 'glass'),
    ('Mat_04_MetalRails', 'polished_steel'),
    ('Mat_05_CleanWoodFloor', 'wood_floor'),
    ('Mat_06_HoopBlack', 'rubber'),
    ('Mat_07_HoopNet', 'fabric'),
    ('Mat_08_HoopRim', 'painted_metal'),
    ('Mat_09_ScoreboardFrame', 'painted_metal'),
    ('Mat_10_LegacyScoreboardScreen', 'legacy_screen_removed'),
    ('Mat_11_LightHousing', 'painted_metal'),
    ('Mat_12_LightEmitter', 'light_emitter'),
    ('Mat_13_LightReflector', 'light_reflector'),
    ('Mat_14_LightDarkMetal', 'painted_metal'),
    ('Mat_15_Structure', 'structural_steel'),
    ('Mat_16_RoofMetal', 'roof_metal'),
    ('Mat_17_WallPaint', 'painted_wall'),
    ('Mat_18_WoodWall', 'wood_panel'),
    ('Mat_19_BalconyDeck', 'painted_floor'),
    ('Mat_20_BlueSeats', 'blue_plastic_seat'),
]


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(4*1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def bounds(ob):
    pts=[ob.matrix_world@Vector(v) for v in ob.bound_box]
    return ([min(p[i] for p in pts) for i in range(3)],
            [max(p[i] for p in pts) for i in range(3)])


def all_bounds(objects):
    b=[bounds(ob) for ob in objects]
    return ([min(x[0][i] for x in b) for i in range(3)],
            [max(x[1][i] for x in b) for i in range(3)])


def reverse_faces(mesh):
    if hasattr(mesh, 'flip_normals'):
        mesh.flip_normals()
    else:
        import bmesh
        bm=bmesh.new();bm.from_mesh(mesh)
        bmesh.ops.reverse_faces(bm, faces=list(bm.faces))
        bm.to_mesh(mesh);bm.free()


def bake_matrix(ob, matrix):
    ob.data.transform(matrix)
    if matrix.determinant()<0:
        reverse_faces(ob.data)
    ob.parent=None
    ob.matrix_world=Matrix.Identity(4)
    ob.data.update()


def set_principled_weight(material):
    if material.use_nodes:
        for node in material.node_tree.nodes:
            if node.type=='BSDF_PRINCIPLED' and 'Weight' in node.inputs:
                node.inputs['Weight'].default_value=1.0


def new_brand_material(name, image_path):
    mat=bpy.data.materials.new(name)
    mat.use_nodes=True
    nodes=mat.node_tree.nodes
    bsdf=nodes.get('Principled BSDF')
    bsdf.inputs['Base Color'].default_value=(1,1,1,1)
    bsdf.inputs['Metallic'].default_value=0
    bsdf.inputs['Roughness'].default_value=.8
    if 'Weight' in bsdf.inputs:
        bsdf.inputs['Weight'].default_value=1
    tex=nodes.new('ShaderNodeTexImage')
    tex.image=bpy.data.images.load(str(image_path), check_existing=True)
    mat.node_tree.links.new(tex.outputs['Color'],bsdf.inputs['Base Color'])
    if 'Emission Color' in bsdf.inputs:
        mat.node_tree.links.new(tex.outputs['Color'],bsdf.inputs['Emission Color'])
        bsdf.inputs['Emission Strength'].default_value=.15
    return mat


def plane(name, center, horizontal, width, height, material):
    c=Vector(center); h=Vector(horizontal)*(width/2); v=Vector((0,0,height/2))
    mesh=bpy.data.meshes.new(name)
    mesh.from_pydata([c-h-v,c+h-v,c+h+v,c-h+v],[],[(0,1,2,3)])
    mesh.materials.append(material)
    uv=mesh.uv_layers.new(name='UVMap')
    for li,coord in enumerate(((0,0),(1,0),(1,1),(0,1))):
        uv.data[li].uv=coord
    ob=bpy.data.objects.new(name,mesh)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def join_group(objects, name):
    bpy.ops.object.select_all(action='DESELECT')
    for ob in objects:
        ob.select_set(True)
    bpy.context.view_layer.objects.active=objects[0]
    if len(objects)>1:
        bpy.ops.object.join()
    ob=bpy.context.view_layer.objects.active
    ob.name=name; ob.data.name=name
    return ob


def split_geometry(ob, labels):
    """Partition authored triangles while retaining UVs, smooth flags and slots."""
    unique=np.unique(labels)
    if len(unique)==1:
        return [(str(unique[0]),ob)]
    old=ob.data
    verts=np.empty(len(old.vertices)*3,dtype=np.float32)
    old.vertices.foreach_get('co',verts);verts=verts.reshape((-1,3))
    tri_verts=np.empty(len(old.loop_triangles)*3,dtype=np.int32)
    old.loop_triangles.foreach_get('vertices',tri_verts);tri_verts=tri_verts.reshape((-1,3))
    tri_loops=np.empty(len(old.loop_triangles)*3,dtype=np.int32)
    old.loop_triangles.foreach_get('loops',tri_loops);tri_loops=tri_loops.reshape((-1,3))
    tri_poly=np.empty(len(old.loop_triangles),dtype=np.int32)
    old.loop_triangles.foreach_get('polygon_index',tri_poly)
    uv_arrays=[]
    for layer in old.uv_layers:
        arr=np.empty(len(layer.data)*2,dtype=np.float32)
        layer.data.foreach_get('uv',arr)
        uv_arrays.append((layer.name,arr.reshape((-1,2))))
    corner_normals=None
    if hasattr(old,'corner_normals'):
        arr=np.empty(len(old.corner_normals)*3,dtype=np.float32)
        old.corner_normals.foreach_get('vector',arr)
        corner_normals=arr.reshape((-1,3))
    results=[]
    for label in unique:
        idx=np.flatnonzero(labels==label)
        used,remap=np.unique(tri_verts[idx].ravel(),return_inverse=True)
        mesh=bpy.data.meshes.new(ob.name+'_'+str(label))
        mesh.from_pydata(verts[used].tolist(),[],remap.reshape((-1,3)).tolist())
        for mat in old.materials:mesh.materials.append(mat)
        selected_loops=tri_loops[idx].ravel()
        for name,arr in uv_arrays:
            layer=mesh.uv_layers.new(name=name)
            layer.data.foreach_set('uv',arr[selected_loops].ravel())
        for new_poly,old_index in zip(mesh.polygons,tri_poly[idx]):
            new_poly.material_index=old.polygons[int(old_index)].material_index
            new_poly.use_smooth=old.polygons[int(old_index)].use_smooth
        if corner_normals is not None and hasattr(mesh,'normals_split_custom_set'):
            mesh.normals_split_custom_set(corner_normals[selected_loops].tolist())
        mesh.update()
        part=bpy.data.objects.new(mesh.name,mesh)
        bpy.context.scene.collection.objects.link(part)
        results.append((str(label),part))
    bpy.data.objects.remove(ob,do_unlink=True)
    if old.users==0:bpy.data.meshes.remove(old)
    return results


def align_winding_to_authored_normals(ob):
    """Repair source mirrored-node winding without reversing the authored normals."""
    old=ob.data;old.calc_loop_triangles()
    vertices=np.empty(len(old.vertices)*3,dtype=np.float32)
    old.vertices.foreach_get('co',vertices);vertices=vertices.reshape((-1,3))
    indices=np.empty(len(old.loop_triangles)*3,dtype=np.int32)
    old.loop_triangles.foreach_get('vertices',indices);indices=indices.reshape((-1,3))
    loops=np.empty(len(old.loop_triangles)*3,dtype=np.int32)
    old.loop_triangles.foreach_get('loops',loops);loops=loops.reshape((-1,3))
    normals=np.empty(len(old.corner_normals)*3,dtype=np.float32)
    old.corner_normals.foreach_get('vector',normals);normals=normals.reshape((-1,3))
    points=vertices[indices].astype(np.float64)
    geometric=np.cross(points[:,1]-points[:,0],points[:,2]-points[:,0])
    authored=normals[loops].mean(axis=1)
    length=np.linalg.norm(geometric,axis=1)
    dot=(geometric*authored).sum(axis=1)/np.maximum(length,1e-20)
    bad=(dot<-.0001)&(length>1e-12)
    count=int(bad.sum())
    if not count:return 0
    indices[bad]=indices[bad][:,[0,2,1]]
    loops[bad]=loops[bad][:,[0,2,1]]
    polyidx=np.empty(len(old.loop_triangles),dtype=np.int32)
    old.loop_triangles.foreach_get('polygon_index',polyidx)
    mesh=bpy.data.meshes.new(old.name+'_WindingRepaired')
    mesh.from_pydata(vertices.tolist(),[],indices.tolist())
    for mat in old.materials:mesh.materials.append(mat)
    selected_loops=loops.ravel()
    for layer in old.uv_layers:
        arr=np.empty(len(layer.data)*2,dtype=np.float32)
        layer.data.foreach_get('uv',arr)
        dest=mesh.uv_layers.new(name=layer.name)
        dest.data.foreach_set('uv',arr.reshape((-1,2))[selected_loops].ravel())
    for newp,oldi in zip(mesh.polygons,polyidx):
        newp.material_index=old.polygons[int(oldi)].material_index
        newp.use_smooth=old.polygons[int(oldi)].use_smooth
    mesh.normals_split_custom_set(normals[selected_loops].tolist())
    mesh.update();ob.data=mesh
    if old.users==0:bpy.data.meshes.remove(old)
    return count


def group_for_lumen(all_meshes, brand_objects):
    """Keep floors, roofs, walls and galleries separate for Lumen Surface Cache."""
    bins={}
    before=sum(sum(len(f.vertices)-2 for f in ob.data.polygons) for ob in all_meshes)
    for ob in all_meshes:
        if ob in brand_objects or ob.name=='Object_139':
            bins.setdefault('RMVenueBranding',[]).append(ob)
            continue
        key=ob.data.materials[0].name
        ob.data.calc_loop_triangles()
        verts=np.empty(len(ob.data.vertices)*3,dtype=np.float32)
        ob.data.vertices.foreach_get('co',verts);verts=verts.reshape((-1,3))
        indices=np.empty(len(ob.data.loop_triangles)*3,dtype=np.int32)
        ob.data.loop_triangles.foreach_get('vertices',indices)
        centers=verts[indices.reshape((-1,3))].mean(axis=1)
        x,y=centers[:,0],centers[:,1]
        dominant_x=np.abs(x)/16.3>np.abs(y)/20.2
        side=np.where(dominant_x,np.where(x>0,'XPos','XNeg'),np.where(y>0,'YPos','YNeg'))
        if key=='Mat_05_CleanWoodFloor':
            labels=np.full(len(centers),'RMVenueFloor')
        elif key=='Mat_20_BlueSeats':
            labels=np.where(y>0,'RMVenueSeatsYPos','RMVenueSeatsYNeg')
        elif key=='Mat_16_RoofMetal':
            labels=np.where(y>0,'RMVenueRoofYPos','RMVenueRoofYNeg')
        elif key=='Mat_15_Structure':
            labels=np.where(x<-5.5,'RMVenueStructureXNeg',np.where(x>5.5,'RMVenueStructureXPos','RMVenueStructureCenter'))
        elif key.startswith(('Mat_11_','Mat_12_','Mat_13_','Mat_14_')):
            labels=np.where(y<-6,'RMVenueLightsYNeg',np.where(y>6,'RMVenueLightsYPos','RMVenueLightsCenter'))
        elif key=='Mat_03_WindowGlass':
            labels=np.char.add('RMVenueGlass',side)
        elif key=='Mat_04_MetalRails':
            labels=np.char.add('RMVenueRail',side)
        elif key=='Mat_19_BalconyDeck':
            labels=np.char.add('RMVenueGallery',side)
        elif key.startswith(('Mat_00_','Mat_01_','Mat_02_','Mat_17_','Mat_18_')):
            labels=np.char.add('RMVenueWall',side)
        else:
            raise RuntimeError('Unclassified authored geometry: '+ob.name+' / '+key)
        for label,part in split_geometry(ob,labels):
            bins.setdefault(label,[]).append(part)
    groups=[join_group(bins[name],name) for name in sorted(bins)]
    after=sum(sum(len(f.vertices)-2 for f in ob.data.polygons) for ob in groups)
    assert before==after,(before,after)
    assert len(groups)<=32,len(groups)
    return groups


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--branding-dir',required=True)
    parser.add_argument('--scale',type=float,default=1.8)
    parser.add_argument('--floor-z',type=float,default=-.305)
    parser.add_argument('--save-blend',action='store_true')
    argv=sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
    args=parser.parse_args(argv)
    source=Path(args.source).resolve(); output=Path(args.output).resolve()
    branding=Path(args.branding_dir).resolve()
    assert source.is_file(),source
    actual_sha=sha(source)
    assert actual_sha==SOURCE_SHA256, f'Unexpected source: {actual_sha}'
    assert 1.7<=args.scale<=2.1, 'Use a reasonable uniform gym scale, 1.7 to 2.1.'
    output.mkdir(parents=True,exist_ok=True)
    (output/'textures').mkdir(exist_ok=True)
    (output/'branding').mkdir(exist_ok=True)
    blob=source.read_bytes()
    assert blob[:4]==b'glTF'
    json_len=struct.unpack_from('<I',blob,12)[0]
    gltf=json.loads(blob[20:20+json_len])
    bin_start=28+json_len
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(source))
    bpy.context.view_layer.update()
    source_objects=[ob for ob in bpy.context.scene.objects if ob.type=='MESH']
    source_world_bounds=all_bounds(source_objects)
    source_object_records=[]
    for ob in source_objects:
        mn,mx=bounds(ob)
        source_object_records.append({'name':ob.name,'bounds_min_m':mn,
            'bounds_max_m':mx,'triangles':sum(len(p.vertices)-2 for p in ob.data.polygons),
            'materials':[m.name for m in ob.data.materials if m]})
    # Snapshot all matrices before clearing the original hierarchy.
    matrices={ob.name:ob.matrix_world.copy() for ob in source_objects}
    for ob in source_objects:
        bake_matrix(ob,matrices[ob.name])
    bpy.context.view_layer.update()
    image_files={}
    image_sizes={}
    for i,entry in enumerate(gltf['images']):
        view=gltf['bufferViews'][entry['bufferView']]
        raw=blob[bin_start+view['byteOffset']:bin_start+view['byteOffset']+view['byteLength']]
        target=output/'textures'/f'SourceImage_{i:02d}.png'
        im=bpy.data.images.get(f'Image_{i}')
        assert im is not None, f'Expected imported Image_{i}'
        if entry['mimeType']=='image/png':
            target.write_bytes(raw)
        else:
            im.filepath_raw=str(target);im.file_format='PNG';im.save()
        im.filepath_raw=str(target)
        image_files[i]=target.relative_to(output).as_posix()
        image_sizes[i]=list(im.size)
    materials={}
    material_objects={}
    for i,srcmat in enumerate(gltf['materials']):
        key,semantic=MATERIAL_INFO[i]
        mat=bpy.data.materials.get(srcmat['name'])
        assert mat is not None, srcmat['name']
        mat.name=key
        set_principled_weight(mat)
        material_objects[i]=mat
        pbr=srcmat.get('pbrMetallicRoughness',{})
        result={
            'source_name':srcmat['name'],'source_index':i,'semantic':semantic,
            'base_color_factor':pbr.get('baseColorFactor',[1,1,1,1]),
            'metallic_factor':pbr.get('metallicFactor',1),
            'roughness_factor':pbr.get('roughnessFactor',1),
            'normal_scale':srcmat.get('normalTexture',{}).get('scale',1),
            'emissive_factor':srcmat.get('emissiveFactor',[0,0,0]),
            'alpha_mode':srcmat.get('alphaMode','OPAQUE'),
            'alpha_cutoff':srcmat.get('alphaCutoff',.5),
            'double_sided':srcmat.get('doubleSided',False),
            'pbr_workflow':'metallic_roughness',
            'pbr_upgrade_hint':'Keep author tint; use existing licensed fine PBR detail with world-aligned metric tiling.',
        }
        for outkey,container,inkey in (
            ('base_color_texture',pbr,'baseColorTexture'),
            ('metallic_roughness_texture',pbr,'metallicRoughnessTexture'),
            ('normal_texture',srcmat,'normalTexture'),
            ('emissive_texture',srcmat,'emissiveTexture'),
        ):
            if inkey in container:
                texidx=container[inkey]['index']; imageidx=gltf['textures'][texidx]['source']
                result[outkey]=image_files[imageidx]
                result[outkey+'_size']=image_sizes[imageidx]
        if 'KHR_materials_transmission' in srcmat.get('extensions',{}):
            result['transmission_factor']=srcmat['extensions']['KHR_materials_transmission'].get('transmissionFactor',0)
        materials[key]=result
    # Replace court markings with the same author's clean wood image.
    floor=bpy.data.objects['Object_117']
    floor_surface_z=max(v.co.z for v in floor.data.vertices)
    floor_mat=material_objects[5]
    for node in floor_mat.node_tree.nodes:
        if node.type=='TEX_IMAGE':
            node.image=bpy.data.images['Image_2']
    for uv in floor.data.uv_layers.active.data:
        uv.uv.x*=4;uv.uv.y*=4
    materials['Mat_05_CleanWoodFloor'].update({
        'base_color_texture':image_files[2],
        'base_color_texture_size':image_sizes[2],
        'mesh_uv_multiplier_applied':[4,4],
        'modification':'Reused original clean wood texture; removed original basketball painted lines.',
    })
    removed=[]
    for name in HOOP_OBJECTS+['Object_140']:
        ob=bpy.data.objects.get(name)
        assert ob is not None, name
        removed.append({'name':name,'reason':'Basketball apparatus' if name in HOOP_OBJECTS else 'Unrelated scoreboard text and digits'})
        bpy.data.objects.remove(ob,do_unlink=True)
    # Retain and resize the original scoreboard cabinet around a 4:1 brand sign.
    frame=bpy.data.objects['Object_139']
    mn,mx=bounds(frame)
    cx=(mn[0]+mx[0])/2;cz=(mn[2]+mx[2])/2
    for v in frame.data.vertices:
        v.co.x=cx+(v.co.x-cx)*(4.4/args.scale)/(mx[0]-mn[0])
        v.co.z=cz+(v.co.z-cz)*(1.4/args.scale)/(mx[2]-mn[2])
    frame_mat=material_objects[9]
    bsdf=frame_mat.node_tree.nodes.get('Principled BSDF')
    for link in list(frame_mat.node_tree.links):
        if link.to_node==bsdf and link.to_socket==bsdf.inputs['Base Color']:
            frame_mat.node_tree.links.remove(link)
    bsdf.inputs['Base Color'].default_value=(.008,.008,.008,1)
    bsdf.inputs['Metallic'].default_value=.15
    bsdf.inputs['Roughness'].default_value=.65
    materials['Mat_09_ScoreboardFrame'].pop('base_color_texture',None)
    materials['Mat_09_ScoreboardFrame'].pop('base_color_texture_size',None)
    materials['Mat_09_ScoreboardFrame'].update({'base_color_factor':[.008,.008,.008,1],
        'metallic_factor':.15,'roughness_factor':.65,
        'modification':'Removed original scoreboard lettering texture; retained author cabinet geometry.'})
    brand_files=['sign_horizontal_4x1_white.png','sign_horizontal_4x1_black.png',
                 'sign_vertical_1x2_white.png','sign_vertical_1x2_black.png']
    for filename in brand_files:
        origin=branding/filename
        assert origin.is_file(), origin
        shutil.copy2(origin,output/'branding'/filename)
    brand_mats={}
    for key,filename,aspect in (
        ('BrandMainHorizontal','sign_horizontal_4x1_white.png',4.0),
        ('BrandMainVertical','sign_vertical_1x2_black.png',.5),
    ):
        brand_mats[key]=new_brand_material(key,output/'branding'/filename)
        materials[key]={'base_color_texture':'branding/'+filename,
            'base_color_factor':[1,1,1,1],'metallic_factor':0,'roughness_factor':.8,
            'normal_scale':1,'emissive_texture':'branding/'+filename,
            'emissive_factor':[.15,.15,.15], 'alpha_mode':'OPAQUE','alpha_cutoff':.5,
            'double_sided':True,'semantic':'official_brand_panel',
            'physical_aspect_ratio':aspect,
            'pbr_workflow':'metallic_roughness',
            'modification':'Uses supplied official DJI and RoboMaster sign artwork at its native aspect ratio.'}
    sign_specs=[
        ('BrandScoreboard',(cx,mx[1]+.001,cz),(-1,0,0),4.0,1.0,'BrandMainHorizontal'),
        ('BrandStandWest',(-11.12,-3.3,3.10),(0,1,0),8.0,2.0,'BrandMainHorizontal'),
        ('BrandStandEast',(11.10,3.3,3.10),(0,-1,0),8.0,2.0,'BrandMainHorizontal'),
        ('BrandEndPoster',(3.3,8.94,2.75),(1,0,0),1.25,2.50,'BrandMainVertical'),
    ]
    brand_objects=[]
    for name,center,h,w,hgt,key in sign_specs:
        brand_objects.append(plane(name,center,h,w/args.scale,hgt/args.scale,brand_mats[key]))
    s=args.scale
    # Rotate original source Y to ROS -X, then reflect ROS Y to canonical UE Y.
    # Source -> ROS: (-s*y, s*x, s*z+tz); source -> UE: (-s*y,-s*x,s*z+tz).
    tz=args.floor_z-s*floor_surface_z
    source_to_ue=Matrix(((0,-s,0,0),(-s,0,0,0),(0,0,s,tz),(0,0,0,1)))
    source_to_ros=Matrix(((0,-s,0,0),(s,0,0,0),(0,0,s,tz),(0,0,0,1)))
    all_meshes=[ob for ob in bpy.context.scene.objects if ob.type=='MESH']
    for ob in all_meshes:
        bake_matrix(ob,source_to_ue)
    normal_repairs=[]
    for ob in all_meshes:
        repaired=align_winding_to_authored_normals(ob)
        if repaired:normal_repairs.append({'object':ob.name,'triangles':repaired})
    bpy.context.view_layer.update()
    # Conservative triangle-AABB proof for the low airspace over the 28 x 16 m field.
    intrusion=[]
    for ob in all_meshes:
        if ob.name=='Object_117':
            continue
        ob.data.calc_loop_triangles()
        verts=np.empty(len(ob.data.vertices)*3,dtype=np.float32)
        ob.data.vertices.foreach_get('co',verts);verts=verts.reshape((-1,3))
        indices=np.empty(len(ob.data.loop_triangles)*3,dtype=np.int32)
        ob.data.loop_triangles.foreach_get('vertices',indices)
        tris=verts[indices.reshape((-1,3))]
        lo=tris.min(axis=1);hi=tris.max(axis=1)
        hit=(lo[:,0]<14)&(hi[:,0]>-14)&(lo[:,1]<8)&(hi[:,1]>-8)&(lo[:,2]<5)&(hi[:,2]>args.floor_z+.001)
        if hit.any():
            intrusion.append({'object':ob.name,'candidate_triangles':int(hit.sum()),
                              'bounds_min_m':bounds(ob)[0],'bounds_max_m':bounds(ob)[1]})
    if intrusion:
        (output/'intrusion_candidates.json').write_text(json.dumps(intrusion,indent=2))
        raise RuntimeError('Potential geometry intrusion in field below 5 m; inspect intrusion_candidates.json')
    sign_records=[]
    for ob,spec in zip(brand_objects,sign_specs):
        mn,mx=bounds(ob)
        sign_records.append({'name':ob.name,'material':spec[5],
            'width_m':spec[3],'height_m':spec[4],
            'bounds_min_m':mn,'bounds_max_m':mx})
    groups=group_for_lumen(all_meshes,brand_objects)
    for ob in list(bpy.context.scene.objects):
        if ob.type!='MESH':
            bpy.data.objects.remove(ob,do_unlink=True)
    bpy.context.view_layer.update()
    mesh_records=[]
    for ob in groups:
        bpy.ops.object.select_all(action='DESELECT');ob.select_set(True)
        bpy.context.view_layer.objects.active=ob
        dest=output/(ob.name+'.obj')
        if hasattr(bpy.ops.wm,'obj_export'):
            bpy.ops.wm.obj_export(filepath=str(dest),export_selected_objects=True,
                forward_axis='Y',up_axis='Z',global_scale=1.0,
                apply_modifiers=True,export_uv=True,export_normals=True,
                export_materials=True,export_triangulated_mesh=True,path_mode='RELATIVE')
        else:
            bpy.ops.export_scene.obj(filepath=str(dest),use_selection=True,
                axis_forward='Y',axis_up='Z',global_scale=1.0,
                use_materials=True,use_triangles=True,path_mode='RELATIVE')
        mn,mx=bounds(ob)
        # Independently verify the actual exported coordinates, not just Blender bounds.
        objmn=[float('inf')]*3;objmx=[-float('inf')]*3;objslots=set();nfaces=0
        with dest.open('r',encoding='utf-8') as f:
            for line in f:
                if line.startswith('v '):
                    xyz=list(map(float,line.split()[1:4]))
                    for k in range(3):objmn[k]=min(objmn[k],xyz[k]);objmx[k]=max(objmx[k],xyz[k])
                elif line.startswith('usemtl '):objslots.add(line.strip().split(' ',1)[1])
                elif line.startswith('f '):nfaces+=1
        assert max(abs(a-b) for a,b in zip(mn+mx,objmn+objmx))<.0001,(ob.name,mn,mx,objmn,objmx)
        assert objslots.issubset(materials), (ob.name,objslots-set(materials))
        mesh_records.append({'file':dest.name,'asset_name':ob.name,
            'bounds_min_m':objmn,'bounds_max_m':objmx,'triangles':nfaces,
            'material_slots':sorted(objslots),
            'cast_shadow':not ('Glass' in ob.name or 'Branding' in ob.name),
            'nanite_enabled':'Glass' not in ob.name,
            'collision_hint':'No collision on branding or glass; preserve existing competition collision.' if ('Branding' in ob.name or 'Glass' in ob.name) else 'Building only; never replace existing competition collision.'})
    used_slots=set().union(*(set(m['material_slots']) for m in mesh_records))
    materials={k:v for k,v in materials.items() if k in used_slots}
    world_min,world_max=all_bounds(groups)
    attribution=('Indoor Basketball Court by nguyennghiakk / Nguyen Trong Nghia, '
                 'https://sketchfab.com/nguyennghiakk, licensed CC BY 4.0 '
                 '(https://creativecommons.org/licenses/by/4.0/). '
                 'Source: '+SOURCE_URL+'. Changes: uniform venue scale and rotation; '
                 'basketball apparatus removed; same-source clean wood floor; '
                 'scoreboard text replaced and supplied official DJI/RoboMaster signs added; '
                 'coordinate conversion and mesh grouping.')
    (output/'ATTRIBUTION.txt').write_text(attribution+'\n',encoding='utf-8')
    (output/'source_objects.json').write_text(json.dumps(source_object_records,indent=2),encoding='utf-8')
    (output/'source_materials.json').write_text(json.dumps(gltf['materials'],indent=2),encoding='utf-8')
    if args.save_blend:
        bpy.ops.wm.save_as_mainfile(filepath=str(output/'prepared_inspection.blend'))
    file_records=[]
    for path in sorted(output.rglob('*')):
        if path.is_file() and path.name not in ('prepared.json',):
            file_records.append({'path':path.relative_to(output).as_posix(),'bytes':path.stat().st_size,'sha256':sha(path)})
    manifest={
        'schema_version':1,
        'source':{'title':'Indoor Basketball Court','author':'nguyennghiakk / Nguyen Trong Nghia',
            'author_url':'https://sketchfab.com/nguyennghiakk','source_url':SOURCE_URL,
            'download_url':DOWNLOAD_URL,'license':'CC-BY-4.0',
            'license_url':'https://creativecommons.org/licenses/by/4.0/',
            'bytes':source.stat().st_size,'sha256':SOURCE_SHA256,
            'asset_extras':gltf['asset'].get('extras',{}),
            'bounds_min_m':source_world_bounds[0],'bounds_max_m':source_world_bounds[1],
            'original_triangles':sum(x['triangles'] for x in source_object_records),
            'original_meshes':len(source_object_records),
            'all_3_images_embedded':True},
        'coordinate_system':{'name':'canonical_UE_XYZ','units':'metres',
            'equation':'UE=(ROS.x,-ROS.y,ROS.z)',
            'source_to_ros_matrix':[list(row) for row in source_to_ros],
            'source_to_ue_matrix':[list(row) for row in source_to_ue],
            'negative_determinant_winding_repaired':True,
            'obj_forward_axis':'Y','obj_up_axis':'Z','obj_coordinate_bounds_verified':True,
            'ue_import_uniform_scale':100.0,'runtime_scale':1.0},
        'uniform_venue_scale':s,
        'normal_repair':{'method':'Align triangle winding to the transformed authored per-corner normals. Preserve UVs and authored smooth normals.',
            'repaired_triangles':sum(x['triangles'] for x in normal_repairs),
            'objects':normal_repairs},
        'lumen_layout':{'mesh_count':len(groups),'max_native_asset_ids':32,
            'method':'Partition original triangles by wall, roof slope, floor, gallery, rail, seating bank, structural bay and luminaire row. No architectural geometry rebuilt.',
            'preserved_all_retained_triangles':True,'enable_nanite_on_opaque_meshes':True},
        'source_floor_top_z_m':floor_surface_z,'target_floor_top_z_m':args.floor_z,
        'bounds_min_m':world_min,'bounds_max_m':world_max,
        'ros_bounds_min_m':[world_min[0],-world_max[1],world_min[2]],
        'ros_bounds_max_m':[world_max[0],-world_min[1],world_max[2]],
        'field_clearance':{'field_dimensions_m':[28,16],
            'tested_ue_box_min_m':[-14,-8,args.floor_z+.001],
            'tested_ue_box_max_m':[14,8,5],
            'method':'Conservative AABB overlap of every retained triangle, excluding floor below the competition field.',
            'intersecting_triangle_count':0,
            'floor_max_z_m':args.floor_z,
            'competition_geometry_was_modified':False},
        'removed_objects':removed,
        'signs':sign_records,'meshes':mesh_records,'materials':materials,
        'lighting_hint':{'highest_roof_z_m':world_max[2],
            'suggested_area_light_z_m':10.8,
            'authored_main_light_rows_ue_y_m':[-12,0,12],
            'use_existing_cc0_pbr_for_close_views':True,
            'source_texture_resolution_note':'Original floor 1024x1024, clean wood 1024x512, scoreboard 512x512. Most other surfaces have solid PBR colours.'},
        'review_cameras':[
            {'name':'CompleteGym','coordinate_system':'UE metres','position':[0,-18,6.4],'target':[0,1,3.0],'horizontal_fov_degrees':90},
            {'name':'DriverView','coordinate_system':'UE metres','position':[-11,-6,1.1],'target':[4,0,1.8],'horizontal_fov_degrees':90},
        ],
        'attribution':attribution,'files':file_records,
        'preparation_blender_version':bpy.app.version_string,
    }
    (output/'prepared.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    assert sha(source)==SOURCE_SHA256,'Source file was unexpectedly changed'
    print('PREPARED',json.dumps({'output':str(output),'meshes':mesh_records,
        'bounds_min_m':world_min,'bounds_max_m':world_max,'materials':len(materials),
        'clearance_intersections':0},ensure_ascii=False))


if __name__=='__main__':
    main()
