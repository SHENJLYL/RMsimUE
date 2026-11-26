"""Blender background: convert checksum-pinned, source-derived RMUC2027 GLBs to UE FBX.

The upstream GLB declares Z-up in asset.extras; read buffers directly instead of applying
a glTF importer's conventional Y-up conversion. No source CAD or archive is modified.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import bpy
import numpy as np


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()


def read_glb(path):
    raw=path.read_bytes()
    magic,version,total=struct.unpack_from('<4sII',raw)
    assert magic==b'glTF' and version==2 and total==len(raw)
    jlen,jkind=struct.unpack_from('<II',raw,12)
    assert jkind==0x4e4f534a
    document=json.loads(raw[20:20+jlen])
    blen,bkind=struct.unpack_from('<II',raw,20+jlen)
    assert bkind==0x004e4942 and not document.get('extensionsRequired')
    return document,memoryview(raw)[28+jlen:28+jlen+blen]


def array(doc,blob,index):
    a=doc['accessors'][index]; view=doc['bufferViews'][a['bufferView']]
    assert 'sparse' not in a and view.get('buffer',0)==0
    dtype={5123:'<u2',5125:'<u4',5126:'<f4'}[a['componentType']]
    count={'SCALAR':1,'VEC3':3}[a['type']]
    item=np.dtype(dtype).itemsize
    offset=view.get('byteOffset',0)+a.get('byteOffset',0)
    return np.ndarray((a['count'],count),dtype=dtype,buffer=blob,offset=offset,
        strides=(view.get('byteStride',count*item),item))


# Match the original blue road marking, including on mirrored assets that
# reuse the red paint in the source assembly.
BLUE_STRIPE_OCCURRENCES = {
    'blue-fortress_0_source_4059143_0',
    'blue-resource-zone_0_source_3998852_0',
    'centre-platform_0_source_1754789_5',
    'blue-ramp-dart-station_0_source_2721188_6',
}


def rmuc_visual_material(occurrence, material):
    return 4 if material == 7 and occurrence in BLUE_STRIPE_OCCURRENCES else material


def world_primitives(doc,blob,visual_materials=False):
    def visit(index,parent):
        node=doc['nodes'][index]
        assert not any(k in node for k in ('rotation','translation','scale'))
        transform=parent@np.array(node.get('matrix',np.eye(4).ravel(order='F')),dtype=float).reshape(4,4,order='F')
        if 'mesh' in node:
            match=re.match(r'(.+)_\d+_source_',node['name'])
            assert match,node['name']
            group=match.group(1).replace('-','_')
            for primitive in doc['meshes'][node['mesh']]['primitives']:
                assert primitive.get('mode',4)==4
                positions=array(doc,blob,primitive['attributes']['POSITION'])
                normals=array(doc,blob,primitive['attributes']['NORMAL'])
                vertices=positions@transform[:3,:3].T+transform[:3,3]
                normals=normals@np.linalg.inv(transform[:3,:3]);normals/=np.linalg.norm(normals,axis=1)[:,None]
                indices=array(doc,blob,primitive['indices']).reshape(-1,3).astype(np.int32)
                if np.linalg.det(transform[:3,:3])<0:indices=indices[:,[0,2,1]]
                material=primitive.get('material',0)
                if visual_materials:
                    material=rmuc_visual_material(node['name'],material)
                yield group,vertices,normals,indices,material
        for child in node.get('children',[]):yield from visit(child,transform)
    for root in doc['scenes'][doc.get('scene',0)]['nodes']:
        yield from visit(root,np.eye(4))


def export_groups(source,out,translation,collision=False,only=None):
    doc,blob=read_glb(source)
    assert doc['asset']['extras']['source_sha256']=='a5ad3ede3b7674fc98f2e73b71717216383cb2b4b5dcdbe900c2038918bea8e0'
    groups={}
    for group,vertices,normals,indices,material in world_primitives(doc,blob,visual_materials=not collision):
        if only is not None and group not in only:
            continue
        groups.setdefault(group,[]).append((vertices+translation,normals,indices,material))
    materials=[]
    specs=[]
    for i,spec in enumerate(doc['materials']):
        label=f'RM27_M{i:02d}'
        pbr=spec.get('pbrMetallicRoughness',{})
        color=pbr.get('baseColorFactor',[.5,.5,.5,1])
        mat=bpy.data.materials.get(label) or bpy.data.materials.new(label)
        mat.use_nodes=True;node=mat.node_tree.nodes.get('Principled BSDF')
        node.inputs['Base Color'].default_value=color
        node.inputs['Metallic'].default_value=pbr.get('metallicFactor',0)
        node.inputs['Roughness'].default_value=pbr.get('roughnessFactor',.8)
        mat.diffuse_color=color
        materials.append(mat)
        specs.append({'name':label,'color_linear':color,'roughness':pbr.get('roughnessFactor',.8),'metallic':pbr.get('metallicFactor',0),'source_name':spec.get('name','')})
    rows=[]
    out.mkdir(parents=True,exist_ok=True)
    for name,items in groups.items():
        verts=[]; normals=[]; faces=[]; mat_ids=[];offset=0
        for v,n,f,m in items:
            verts.append(v);normals.append(n);faces.append(f+offset);mat_ids.append(np.full(len(f),m,dtype=np.int32));offset+=len(v)
        v=np.concatenate(verts).astype(np.float32);n=np.concatenate(normals).astype(np.float32);f=np.concatenate(faces)
        mesh=bpy.data.meshes.new(name)
        mesh.vertices.add(len(v));mesh.vertices.foreach_set('co',v.ravel())
        mesh.loops.add(f.size);mesh.loops.foreach_set('vertex_index',f.ravel())
        mesh.polygons.add(len(f));mesh.polygons.foreach_set('loop_start',np.arange(len(f),dtype=np.int32)*3)
        mesh.polygons.foreach_set('loop_total',np.full(len(f),3,dtype=np.int32))
        for m in materials:mesh.materials.append(m)
        mesh.polygons.foreach_set('material_index',np.concatenate(mat_ids))
        mesh.update();mesh.normals_split_custom_set_from_vertices(n.tolist())
        obj=bpy.data.objects.new(name,mesh);bpy.context.collection.objects.link(obj)
        bpy.ops.object.select_all(action='DESELECT');obj.select_set(True);bpy.context.view_layer.objects.active=obj
        target=out/(name+'.fbx')
        bpy.ops.export_scene.fbx(filepath=str(target),use_selection=True,object_types={'MESH'},
            axis_forward='X',axis_up='Z',apply_unit_scale=True,apply_scale_options='FBX_SCALE_UNITS',
            use_mesh_modifiers=True,mesh_smooth_type='OFF',add_leaf_bones=False,bake_anim=False,path_mode='AUTO')
        rows.append({'id':name,'file':target.name,'bounds_m':{'lo':v.min(axis=0).tolist(),'hi':v.max(axis=0).tolist()},
            'vertices':len(v),'triangles':len(f),'sha256':digest(target),'material_slots':[m.name for m in materials]})
        if collision:bpy.data.objects.remove(obj,do_unlink=True)
        print('EXPORTED',name,len(f),'triangles',flush=True)
    return rows,specs


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source',required=True);ap.add_argument('--output',required=True)
    args=ap.parse_args(sys.argv[sys.argv.index('--')+1:])
    source=Path(args.source).resolve();out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
    doc,blob=read_glb(source/'full-map.glb')
    floor=[v for g,v,n,f,m in world_primitives(doc,blob) if g=='floor']
    minimum=np.min([v.min(axis=0) for v in floor],axis=0);maximum=np.max([v.max(axis=0) for v in floor],axis=0)
    translation=np.array([-(minimum[0]+maximum[0])/2,-(minimum[1]+maximum[1])/2,-maximum[2]])
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.unit_settings.system='METRIC';bpy.context.scene.unit_settings.scale_length=1
    collisions,_=export_groups(source/'full-map-collision.glb',out/'collision',translation,True)
    visuals,materials=export_groups(source/'full-map.glb',out/'visual',translation)
    manifest={'schema':'rm_mesh_map_source_v1','id':'rmuc2027','display_name':'RMUC 2027 V1.0.0',
        'units':'m','coordinate_system':'ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP',
        'source_step_sha256':doc['asset']['extras']['source_sha256'],'source_glb_sha256':digest(source/'full-map.glb'),
        'source_to_sim_translation_m':translation.tolist(),'frame_note':'Internal centered frame; floor central crest is z=0, not referee coordinates.',
        'visuals':visuals,'collisions':collisions,'materials':materials,
        'source_url':'https://bbs.robomaster.com/article/1970956','conversion_release':'https://github.com/hxyulin/rm-map-tools/releases/tag/rm2027-assets-20261015',
        'limitations':['static CAD rest pose','source tessellation and simplification, not certified CAD identity','no match mechanisms or calibrated physics','no official surface artwork in CAD']}
    (out/'mesh-source.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    bpy.ops.wm.save_as_mainfile(filepath=str(out/'preview.blend'))
    print('RMUC2027_PREPARED',json.dumps({'visual_parts':len(visuals),'triangles':sum(r['triangles'] for r in visuals),'translation':translation.tolist()}),flush=True)


if __name__=='__main__':main()
