"""Blender: prepare a sourced War Thunder world without flattening instance transforms.

Original inputs are read only. Non-TRS matrices are baked into spatial mesh batches;
orthogonal transforms remain instanced. Static surfaces preserve source positions.
"""
import argparse
import collections
import hashlib
import json
import math
from pathlib import Path
import shutil
import struct
import sys
import bpy
import numpy as np
from mathutils import Matrix

CONVERSION=np.array([[1,0,0],[0,0,-1],[0,1,0]],dtype=np.float64)
MAX_MATERIALS=64
MAX_TRIANGLES=200000


def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def save(path,data):path.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def bounds(vertices):return dict(lo=np.min(vertices,axis=0).tolist(),hi=np.max(vertices,axis=0).tolist())
def material_id(spec):
    data={k:spec[k] for k in ('shader','parameters','texture_slots')}
    return 'WT_'+hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:20]


def matrix_instance(entry,values):
    if not entry['pos_inst']:
        matrix=np.asarray(values,dtype=np.float64).reshape(3,4)
        return matrix[:,:3],matrix[:,3]
    x,y,z,word=values
    if entry['palette_rotation']:
        size=entry['palette_rotation_count'];index=int(word)&7
        if not 1<=size<=8 or index>=size:raise ValueError('invalid rotation palette index')
        angle=index/size*2*math.pi;scale=(int(word)&~7)/256
    else:angle=0;scale=np.int16(word)/256
    c,s=math.cos(angle),math.sin(angle)
    return np.array([[c,0,-s],[0,1,0],[s,0,c]])*scale,np.array([x,y,z])


def decompose(matrix):
    u,_,v=np.linalg.svd(matrix);rotation=u@v
    scale=np.diag(rotation.T@matrix)
    if np.linalg.det(rotation)<=0 or np.any(scale<=0):return None
    return rotation,scale,matrix-rotation@np.diag(scale)


def transform_row(matrix,position,model_bounds):
    decomposition=decompose(matrix)
    if decomposition is None:return None,None
    rotation,scale,residual=decomposition
    radius=np.maximum(np.abs(model_bounds['lo']),np.abs(model_bounds['hi']))
    error=float(np.linalg.norm(residual,axis=1)@radius)
    if error>1e-6:return None,error
    quaternion=Matrix((CONVERSION@rotation@CONVERSION.T).tolist()).to_quaternion()
    # ROS rotation, with scale axes permuted along the same basis conversion.
    return [*(CONVERSION@position).tolist(),quaternion.x,quaternion.y,quaternion.z,quaternion.w,
            scale[0],scale[2],scale[1]],error


def instances_file(path,rows):
    array=np.asarray(rows,dtype='<f4').reshape(-1,10)
    if not np.all(np.isfinite(array)):raise ValueError('nonfinite output transforms')
    path.write_bytes(b'RMI1'+struct.pack('<I',len(array))+array.tobytes())


def verify_prepared(root,output):
    manifest=read(output/'scene-source.json');errors=[];count=0
    for part in manifest['meshes']+manifest['collision_meshes']:
        if sha(output/part['file'])!=part['sha256']:raise ValueError('prepared FBX hash mismatch: '+part['id'])
    layouts={}
    for path in sorted((root/'layout-06').glob('layer-*-instances.json')):
        for name,entry in read(path).items():
            if name in layouts:layouts[name]['instances']+=entry['instances']
            else:layouts[name]=entry
    for part in manifest['meshes']:
        raw=(output/part['instances_file']).read_bytes()
        rows=np.frombuffer(raw[8:],dtype='<f4').reshape(-1,10)
        if raw[:4]!=b'RMI1' or struct.unpack_from('<I',raw,4)[0]!=len(rows) or not np.all(np.isfinite(rows)):
            raise ValueError('invalid instance stream')
        if part['source'] not in layouts:continue
        name=part['source'];metadata=read(root/'models-all-07'/name/'model.json')
        original=[]
        for values in layouts[name]['instances']:
            m,p=matrix_instance(layouts[name],values);row,error=transform_row(m,p,metadata['bounds_dagor_m'])
            if row is not None:original.append((m,p))
        if len(original)!=len(rows):raise ValueError('source instance count differs')
        corners=np.array([[x,y,z] for x in (metadata['bounds_dagor_m']['lo'][0],metadata['bounds_dagor_m']['hi'][0])
            for y in (metadata['bounds_dagor_m']['lo'][1],metadata['bounds_dagor_m']['hi'][1])
            for z in (metadata['bounds_dagor_m']['lo'][2],metadata['bounds_dagor_m']['hi'][2])])@CONVERSION.T
        for row,(m,p) in zip(rows,original):
            x,y,z,w=map(float,row[3:7]);q=np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
                [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],[2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])
            actual=corners@(q@np.diag(row[7:])).T+row[:3]
            expected=corners@(CONVERSION@m@CONVERSION.T).T+CONVERSION@p
            errors.append(float(np.linalg.norm(actual-expected,axis=1).max()));count+=1
    if max(errors,default=0)>.003:raise ValueError('serialized instance transforms exceed 3mm')
    if sha(output/'terrain/height.u16')!=sha(root/'terrain-01/height.u16'):raise ValueError('terrain samples changed')
    representatives=[next(p for p in manifest['meshes'] if p['source']=='france_town_building_2_floors_c'),
        next(p for p in manifest['meshes'] if p['id'].startswith('affine_')),
        next(p for p in manifest['meshes'] if p['id'].startswith('world_static_')),
        next(p for p in manifest['collision_meshes'] if p['id']=='col_prop_france_town_building_2_floors_c')]
    roundtrips=[]
    for part in representatives:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.fbx(filepath=str(output/part['file']))
        meshes=[o for o in bpy.context.scene.objects if o.type=='MESH']
        points=[];triangles=0;uv_counts=[]
        for obj in meshes:
            v=np.array([obj.matrix_world@p.co for p in obj.data.vertices]);points.append(v)
            obj.data.calc_loop_triangles();triangles+=len(obj.data.loop_triangles);uv_counts.append(len(obj.data.uv_layers))
        points=np.concatenate(points);actual=bounds(points)
        delta=max(float(np.abs(np.array(actual[k])-part['bounds_m'][k]).max()) for k in ('lo','hi'))
        if delta>.003 or triangles!=part['triangles']:
            raise ValueError(f'FBX roundtrip differs: {part["id"]}; bounds {delta}m, triangles {triangles}/{part["triangles"]}, actual {actual}')
        roundtrips.append(dict(id=part['id'],triangles=triangles,uv_layers=uv_counts,bounds_error_m=delta))
    report=dict(status='passed',scope='prepared source assets and explicit instance transforms; not Unreal rendering or gameplay',
        source_instances=manifest['source_coverage']['source_instances'],serialized_source_instances_checked=count,
        affine_baked_instances=manifest['source_coverage']['affine_baked_instances'],max_serialized_corner_error_m=max(errors),
        terrain_exact_bytes=True,visual_assets=len(manifest['meshes']),collision_assets=len(manifest['collision_meshes']),
        checked_file_hashes=len(manifest['meshes'])+len(manifest['collision_meshes']),fbx_roundtrips=roundtrips)
    save(output/'preparation-verification.json',report);print('WT_PREPARATION_VERIFIED',json.dumps(report),flush=True)


class Builder:
    def __init__(self,root,output):
        self.root=root;self.output=output;self.materials={};self.parts=[];self.instanced=[];self.collisions=[]
        self.bounds=[];self.data_cache={};self.audit=collections.Counter();self.approx_error=0.
        self.known={};self.journal=output/'parts-journal.jsonl'
        if self.journal.exists():
            for line in self.journal.read_text(encoding='utf-8').splitlines():
                data=json.loads(line);self.known[data['id']]=data
        self.model_metadata={p.parent.name:read(p) for p in (root/'models-all-07').glob('*/model.json')}
        for m in self.model_metadata.values():
            for i in m['used_material_indices']:self.register(m['materials'][i])
        self.land=read(root/'landmesh-01/landmesh.json');self.static=read(root/'static-02/scene.json')
        for data in (self.land,self.static):
            for material in data['materials']:self.register(material)

    def register(self,spec):
        key=material_id(spec)
        self.materials[key]=dict(spec,id=key)
        if key not in bpy.data.materials:
            material=bpy.data.materials.new(key);material.diffuse_color=(.5,.5,.5,1)
        return key

    def model(self,name):
        if name not in self.data_cache:
            f=self.root/'models-all-07'/name/'mesh.npz'
            with np.load(f) as source:d={key:source[key].copy() for key in source.files}
            # Packed DAE indices oppose its decoded normals. Correct per nondegenerate
            # triangle using the original vertex normals, without changing source data.
            f=d['triangles'];v=d['vertices'];n=d['normals']
            dot=np.sum(np.cross(v[f[:,1]]-v[f[:,0]],v[f[:,2]]-v[f[:,0]])*n[f].mean(axis=1),axis=1)
            flip=dot < -1e-10
            d['triangles'][flip]=f[flip][:,[0,2,1]]
            self.audit['faces_reoriented_to_source_normals']+=int(flip.sum())
            self.data_cache[name]=d
        return self.data_cache[name]

    def mesh(self,identifier,data,slots,collision=False):
        folder='collision' if collision else 'meshes';target=self.output/folder/(identifier+'.fbx')
        # FBX/Blender validation removes repeated polygons with identical vertex IDs.
        # Some original SCN draws intentionally repeat a triangle (e.g. material layers).
        # Give only those repeats independent vertices, preserving every source draw.
        _,first=np.unique(np.sort(data['triangles'],axis=1),axis=0,return_index=True)
        repeated=np.setdiff1d(np.arange(len(data['triangles'])),first,assume_unique=True)
        if len(repeated):
            data=dict(data);duplicates=data['triangles'][repeated].ravel();base=len(data['vertices'])
            for key,value in list(data.items()):
                if key not in ('triangles','material_ids'):data[key]=np.concatenate([value,value[duplicates]])
            data['triangles']=data['triangles'].copy();data['triangles'][repeated]=np.arange(base,base+len(duplicates)).reshape(-1,3)
        old=self.known.get(identifier)
        if old and old.get('duplicate_faces_preserved',0)==len(repeated) and old.get('file')==f'{folder}/{identifier}.fbx' and target.exists() and sha(target)==old['sha256']:
            return old
        vertices=data['vertices'].astype(np.float32);faces=data['triangles'].astype(np.int32)
        if len(slots)>MAX_MATERIALS:raise ValueError('material batch limit exceeded')
        if not len(faces):raise ValueError('empty geometry')
        mesh=bpy.data.meshes.new(identifier);mesh.vertices.add(len(vertices));mesh.vertices.foreach_set('co',vertices.ravel())
        mesh.loops.add(faces.size);mesh.loops.foreach_set('vertex_index',faces.ravel())
        mesh.polygons.add(len(faces));mesh.polygons.foreach_set('loop_start',np.arange(len(faces),dtype=np.int32)*3)
        mesh.polygons.foreach_set('loop_total',np.full(len(faces),3,dtype=np.int32))
        for slot in slots:mesh.materials.append(bpy.data.materials[slot])
        mesh.polygons.foreach_set('material_index',data['material_ids'].astype(np.int32))
        for uv_index in range(3):
            key=f'uv{uv_index}'
            uv=data.get(key,data.get('uvs') if uv_index==0 else None)
            if uv is None:continue
            layer=mesh.uv_layers.new(name=f'UV{uv_index}')
            layer.data.foreach_set('uv',uv[faces.ravel(),:2].astype(np.float32).ravel())
        mesh.update()
        normals=data.get('normals')
        if normals is not None:
            mesh.normals_split_custom_set_from_vertices(normals.tolist())
        obj=bpy.data.objects.new(identifier,mesh);bpy.context.collection.objects.link(obj)
        bpy.ops.object.select_all(action='DESELECT');obj.select_set(True);bpy.context.view_layer.objects.active=obj
        bpy.ops.export_scene.fbx(filepath=str(target),use_selection=True,object_types={'MESH'},
            axis_forward='X',axis_up='Z',apply_unit_scale=True,apply_scale_options='FBX_SCALE_UNITS',
            use_mesh_modifiers=True,mesh_smooth_type='OFF',add_leaf_bones=False,bake_anim=False,path_mode='AUTO')
        result=dict(id=identifier,file=f'{folder}/{identifier}.fbx',sha256=sha(target),vertices=len(vertices),
            triangles=len(faces),material_slots=slots,bounds_m=bounds(vertices),duplicate_faces_preserved=len(repeated))
        bpy.data.objects.remove(obj,do_unlink=True);bpy.data.meshes.remove(mesh)
        with self.journal.open('a',encoding='utf-8') as log:log.write(json.dumps(result)+'\n')
        self.known[identifier]=result
        return result

    def converted(self,data,matmap,matrix=None,position=None):
        output={}
        matrix=np.eye(3) if matrix is None else matrix;position=np.zeros(3) if position is None else position
        linear=CONVERSION@matrix
        output['vertices']=data['vertices']@linear.T+(CONVERSION@position)
        output['triangles']=data['triangles'].copy()
        if np.linalg.det(linear)<0:output['triangles']=output['triangles'][:,[0,2,1]]
        output['material_ids']=np.asarray([matmap[int(i)] for i in data['material_ids']],dtype=np.int32)
        for key in ('uvs','uv0','uv1','uv2'):
            if key in data:output[key]=data[key]
        if 'normals' in data:
            normals=data['normals']@np.linalg.inv(linear)
            lengths=np.linalg.norm(normals,axis=1,keepdims=True)
            output['normals']=np.divide(normals,lengths,out=np.zeros_like(normals),where=lengths>1e-8)
        return output

    def source_slots(self,metadata,data):
        used=sorted(set(map(int,data['material_ids'])));slots=[self.register(metadata['materials'][i]) for i in used]
        return slots,{src:i for i,src in enumerate(used)}

    def record_part(self,part,rows,collision_policy,source):
        instance_path=self.output/'instances'/(part['id']+'.rmi')
        instances_file(instance_path,rows)
        part=dict(part,instances_file='instances/'+instance_path.name,instance_count=len(rows),collision_policy=collision_policy,source=source)
        self.parts.append(part)
        self.instanced.append(dict(id=part['id'],visual_asset=f'/Game/RMSim/Maps/wt_vlaanderen/Meshes/{part["id"]}.{part["id"]}',
            collision_asset='',instances_file=part['instances_file']))

    def emit_batch(self,key,items):
        slots=[];lookup={};parts=[];vertices_count=0;triangles_count=0
        serial=0
        def flush():
            nonlocal slots,lookup,parts,vertices_count,triangles_count,serial
            if not parts:return
            combined={}
            allkeys=set().union(*(p.keys() for p in parts))
            for k in allkeys:
                values=[]
                for p in parts:
                    if k in p:values.append(p[k])
                    elif k.startswith('uv'):values.append(np.zeros((len(p['vertices']),2),dtype=np.float32))
                    elif k=='normals':values.append(np.zeros_like(p['vertices']))
                combined[k]=np.concatenate(values)
            name=f'{key}_{serial:03d}'
            part=self.mesh(name,combined,slots)
            self.record_part(part,[[0,0,0,0,0,0,1,1,1,1]],'source_pending','baked_source_world')
            self.bounds.append(bounds(combined['vertices']))
            serial+=1;slots=[];lookup={};parts=[];vertices_count=0;triangles_count=0
        for data,material_slots in items:
            additional=set(material_slots)-set(lookup)
            if parts and (len(lookup)+len(additional)>MAX_MATERIALS or triangles_count+len(data['triangles'])>MAX_TRIANGLES):flush()
            for slot in material_slots:
                if slot not in lookup:lookup[slot]=len(slots);slots.append(slot)
            part=dict(data);part['triangles']=part['triangles']+vertices_count
            part['material_ids']=np.array([lookup[material_slots[int(i)]] for i in part['material_ids']],dtype=np.int32)
            parts.append(part);vertices_count+=len(part['vertices']);triangles_count+=len(part['triangles'])
        flush()

    def props(self):
        layouts={}
        for file in sorted((self.root/'layout-06').glob('layer-*-instances.json')):
            for name,entry in read(file).items():
                if name in layouts:
                    if any(layouts[name][key]!=entry[key] for key in ('pos_inst','palette_rotation','palette_rotation_count')):raise ValueError('pool metadata differs')
                    layouts[name]['instances']+=entry['instances']
                else:layouts[name]=entry
        batches=collections.defaultdict(list)
        for number,(name,entry) in enumerate(sorted(layouts.items())):
            data=self.model(name);metadata=self.model_metadata[name];slots,matmap=self.source_slots(metadata,data)
            rows=[]
            local=self.converted(data,matmap)
            for values in entry['instances']:
                matrix,pos=matrix_instance(entry,values)
                row,error=transform_row(matrix,pos,metadata['bounds_dagor_m'])
                if row is not None:
                    rows.append(row);self.approx_error=max(self.approx_error,error);self.audit['rigid_scaled_instances']+=1
                    # World bounds from all eight source corners enclose original placement.
                    corners=np.array([[x,y,z] for x in [metadata['bounds_dagor_m']['lo'][0],metadata['bounds_dagor_m']['hi'][0]]
                        for y in [metadata['bounds_dagor_m']['lo'][1],metadata['bounds_dagor_m']['hi'][1]]
                        for z in [metadata['bounds_dagor_m']['lo'][2],metadata['bounds_dagor_m']['hi'][2]]])
                    self.bounds.append(bounds(corners@(CONVERSION@matrix).T+CONVERSION@pos))
                else:
                    key=(math.floor(pos[0]/256),math.floor(pos[2]/256))
                    batches[key].append((name,matrix,pos));self.audit['affine_baked_instances']+=1
            if rows:
                part=self.mesh('prop_'+name,local,slots);self.record_part(part,rows,'source_pending',name)
            if (number+1)%50==0:print('PROPS_PREPARED',number+1,len(layouts),flush=True)
        for key,instances in sorted(batches.items()):
            def geometry():
                for name,matrix,pos in instances:
                    data=self.model(name);slots,matmap=self.source_slots(self.model_metadata[name],data)
                    yield self.converted(data,matmap,matrix,pos),slots
            self.emit_batch(f'affine_{key[0]+128:03d}_{key[1]+128:03d}',geometry())
        self.audit['source_instances']=sum(len(v['instances']) for v in layouts.values())
        self.affine_layouts=batches
        self.layout_metadata=layouts
        save(self.output/'affine-bake-map.json',dict(scope='exact original Dagor transforms baked into spatial visual/collision batches',
            batches=[dict(tile=list(key),instances=[dict(model=name,matrix3x3=matrix.tolist(),translation_dagor_m=pos.tolist())
                for name,matrix,pos in values]) for key,values in sorted(batches.items())]))

    def static_world(self):
        hm=read(self.root/'terrain-01/terrain.json')
        exclude=hm['exclude_bounding'];w=self.land['map_size'][0]
        for kind in ('land','decal','combined'):
            entries=[]
            for item in self.land['cells']:
                if item['kind']!=kind:continue
                x,y=item['cell']%w,item['cell']//w
                if kind=='land' and exclude[0]<=x<exclude[2] and exclude[1]<=y<exclude[3]:
                    self.audit['excluded_coarse_land_triangles']+=item['triangles'];continue
                entries.append(item)
            def geometry():
                for item in entries:
                    with np.load(self.root/'landmesh-01'/item['file']) as source:d={k:source[k] for k in source.files}
                    slots,matmap=self.source_slots(self.land,d)
                    yield self.converted(d,matmap),slots
            self.emit_batch('world_'+kind,geometry())
        def geometry():
            for item in self.static['objects']:
                with np.load(self.root/'static-02'/item['file']) as source:d={k:source[k] for k in source.files}
                slots,matmap=self.source_slots(self.static,d)
                yield self.converted(d,matmap),slots
        self.emit_batch('world_static',geometry())

    def terrain(self):
        source=read(self.root/'terrain-01/terrain.json')
        shutil.copyfile(self.root/'terrain-01/height.u16',self.output/'terrain/height.u16')
        descriptor=dict(width=source['width'],height=source['height'],cell_m=source['cell_m'],
            minimum_m=source['hmin_m'],range_m=source['hscale_m'],height_file='terrain/height.u16',
            material='/Game/RMSim/Maps/wt_vlaanderen/Materials/WT_Terrain.WT_Terrain',
            origin_ros_m=[source['origin_dagor_xz_m'][0],-source['origin_dagor_xz_m'][1],0],
            source_sha256=source['sha256'],source_samples_sha256=sha(self.output/'terrain/height.u16'))
        save(self.output/'heightfield.json',descriptor)

    def original_collisions(self):
        source=self.root/'collision-all-04'
        if not (source/'result.json').exists():raise ValueError('final original collision extraction is missing')
        dummy='WT_SourceCollision'
        if dummy not in bpy.data.materials:bpy.data.materials.new(dummy)
        visuals={p['id']:p for p in self.parts};instances={p['id']:p for p in self.instanced}
        missing=[];empty=[];bound=[]
        cache={}
        def collision(name):
            if name in cache:return cache[name]
            file=source/name/'collision.npz'
            if not file.exists():cache[name]=None;return None
            with np.load(file) as data:
                result={key:data[key].copy() for key in ('vertices','triangles')}
            result['material_ids']=np.zeros(len(result['triangles']),dtype=np.int32)
            cache[name]=result;return result
        for name,layout in sorted(self.layout_metadata.items()):
            data=collision(name);visual_id='prop_'+name
            if data is None:missing.append(name);continue
            if not len(data['triangles']):empty.append(name);continue
            if visual_id not in visuals:continue
            part=self.mesh('col_'+visual_id,self.converted(data,{0:0}),[dummy],collision=True)
            self.collisions.append(part);instances[visual_id]['collision_asset']=f'/Game/RMSim/Maps/wt_vlaanderen/Collision/{part["id"]}.{part["id"]}'
            visuals[visual_id]['collision_policy']='source_PHYS_COLLIDABLE';bound.append(name)
        for key,items in sorted(self.affine_layouts.items()):
            prefix=f'affine_{key[0]+128:03d}_{key[1]+128:03d}'
            targets=sorted(p for p in visuals if p.startswith(prefix+'_'))
            verts=[];faces=[];count=0;serial=0
            def flush():
                nonlocal verts,faces,count,serial
                if not faces:return
                if serial>=len(targets):raise ValueError('more collision batches than matching visual batches')
                data=dict(vertices=np.concatenate(verts),triangles=np.concatenate(faces))
                data['material_ids']=np.zeros(len(data['triangles']),dtype=np.int32)
                part=self.mesh(f'col_{prefix}_{serial:03d}',data,[dummy],collision=True)
                self.collisions.append(part);target=targets[serial]
                instances[target]['collision_asset']=f'/Game/RMSim/Maps/wt_vlaanderen/Collision/{part["id"]}.{part["id"]}'
                visuals[target]['collision_policy']='source_affine_batch_PHYS_COLLIDABLE'
                verts=[];faces=[];count=0;serial+=1
            for name,matrix,pos in items:
                data=collision(name)
                if data is None or not len(data['triangles']):continue
                converted=self.converted(data,{0:0},matrix,pos)
                # Collider batches are spatially paired at world identity; do not need
                # identical visual/material splitting, but cannot exceed available anchors.
                verts.append(converted['vertices']);faces.append(converted['triangles']+count);count+=len(converted['vertices'])
            flush()
        world=self.root/'collision-world-01/SCol/collision.npz'
        with np.load(world) as source_data:data={k:source_data[k] for k in ('vertices','triangles')}
        data['material_ids']=np.zeros(len(data['triangles']),dtype=np.int32)
        part=self.mesh('col_world_static',self.converted(data,{0:0}),[dummy],collision=True)
        self.collisions.append(part)
        target=next(p for p in self.instanced if p['id'].startswith('world_static_'))
        target['collision_asset']=f'/Game/RMSim/Maps/wt_vlaanderen/Collision/{part["id"]}.{part["id"]}'
        visuals[target['id']]['collision_policy']='original_world_SCol'
        # Terrain is a source surface, while paint/decal meshes are nonblocking.
        for part in self.parts:
            if part['id'].startswith(('world_land_','world_combined_')):
                instances[part['id']]['collision_asset']=instances[part['id']]['visual_asset']
                part['collision_policy']='original_lmap_triangle_surface'
            elif part['collision_policy']=='source_pending':part['collision_policy']='no_attached_source_collider'
        save(self.output/'collision-coverage.json',dict(source='collision-all-04',matched_resources=616,
            intentionally_nonphysical=empty,missing_collision_resource=missing,
            base_visuals_with_original_collider=len(bound),collision_mesh_assets=len(self.collisions),
            world_SCol=True,world_WCol='TRACEABLE water, not PHYS_COLLIDABLE; not made solid',
            note='source physical material names retained in extraction; UE physical response calibration remains separate'))

    def write(self):
        lows=[v['lo'] for v in self.bounds];highs=[v['hi'] for v in self.bounds]
        manifest=dict(schema='rm_scene_map_source_v1',id='wt_vlaanderen',display_name='War Thunder - Flanders (source map)',
            coordinate_system='ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP',units='m',
            source_sha256=self.land['sha256'],extraction_root=str(self.root),
            source_to_ros_matrix=CONVERSION.tolist(),source_translation_m=[0,0,0],
            bounds_m=dict(lo=np.min(lows,axis=0).tolist(),hi=np.max(highs,axis=0).tolist()),
            materials=list(self.materials.values()),meshes=self.parts,collision_meshes=self.collisions,terrain='heightfield.json',instances='instances.json',
            source_coverage=dict(self.audit),max_instanced_vertex_transform_error_m=self.approx_error,
            limitations=['source stored layout only; procedural landclass-generated entities not regenerated',
                '43 source visual types have no matched original collision resource;13 matched resources intentionally nonphysical',
                'legacy camera-facing leaf quads require vertex shader displacement adaptation',
                'material conversion is independently tracked; original game shading is not certified equivalent'])
        save(self.output/'scene-source.json',manifest);save(self.output/'materials.json',list(self.materials.values()))
        save(self.output/'instances.json',dict(parts=self.instanced))
        print('WT_SCENE_PREPARED',json.dumps(dict(parts=len(self.parts),materials=len(self.materials),counts=dict(self.audit),
            max_instanced_error_m=self.approx_error)),flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--output',required=True)
    p.add_argument('--verify',action='store_true')
    a=p.parse_args(sys.argv[sys.argv.index('--')+1:]);root=Path(a.source).resolve();out=Path(a.output).resolve()
    if a.verify:verify_prepared(root,out);return
    out.mkdir(exist_ok=True,parents=True)
    for name in ('meshes','collision','instances','terrain'):(out/name).mkdir(exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.unit_settings.system='METRIC';bpy.context.scene.unit_settings.scale_length=1
    builder=Builder(root,out);save(out/'materials.json',list(builder.materials.values()))
    builder.terrain();builder.props();builder.static_world();builder.original_collisions();builder.write()


if __name__=='__main__':main()
