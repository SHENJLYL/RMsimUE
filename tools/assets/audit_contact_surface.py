"""Read original Blender triangles to check recorded chassis contact points."""
import argparse
import json
import sys
from pathlib import Path
import bpy
import bmesh
from mathutils import Vector, Quaternion
from mathutils.bvhtree import BVHTree

p=argparse.ArgumentParser()
p.add_argument('--report',required=True)
p.add_argument('--output',required=True)
p.add_argument('--collision',help='Native convex preparation report to test against the same contact points')
a=p.parse_args(sys.argv[sys.argv.index('--')+1:])
d=json.loads(Path(a.report).read_text(encoding='utf-8-sig'))
vertices=[];triangles=[]
for obj in bpy.data.collections['visible_lod00'].objects:
    if obj.name not in ('body','linear_guides'):continue
    offset=len(vertices);obj.data.calc_loop_triangles()
    vertices.extend(obj.matrix_world @ v.co / 2 for v in obj.data.vertices)
    triangles.extend(tuple(offset+i for i in t.vertices) for t in obj.data.loop_triangles)
bvh=BVHTree.FromPolygons(vertices,triangles,all_triangles=True)
records=[]
for sample in d['samples']:
    c=sample['state']['contact'];b=c['chassis'];x,y,z,w=b['rotation_xyzw']
    inverse=Quaternion((w,x,y,z)).inverted()
    for hit in c.get('last_contact_per_body',[]):
        if hit['body']!='Contact_body' or hit['age_seconds']>.1:continue
        local=inverse @ (Vector(hit['point_cm'])-Vector(b['position_cm'])) / 100
        local.y=-local.y
        nearest,normal,face,distance=bvh.find_nearest(local)
        records.append(dict(phase=sample['phase'],time=sample['phase_time'],local_ros_m=list(local),
                            nearest_source_m=list(nearest),distance_mm=distance*1000,triangle=face))
result=dict(source=bpy.data.filepath,source_untouched=True,triangles=len(triangles),contacts=records)
if a.collision:
    body=next(p for p in json.loads(Path(a.collision).read_text(encoding='utf-8'))['parts'] if p['name']=='body')
    hull_planes=[]
    for hull in body['hulls']:
        bm=bmesh.new()
        for v in hull['vertices']:bm.verts.new(v)
        bmesh.ops.convex_hull(bm,input=list(bm.verts),use_existing_faces=False);bm.normal_update()
        center=sum((v.co for v in bm.verts),Vector())/len(bm.verts)
        planes=[]
        for face in bm.faces:
            n=face.normal.copy();origin=face.verts[0].co
            if n.dot(center-origin)>0:n=-n
            planes.append((n,n.dot(origin)))
        hull_planes.append(planes);bm.free()
    for record in records:
        x,y,z=record['local_ros_m'];point=Vector((x*100,-y*100,z*100))
        record['inside_convex_with_0_5mm_tolerance']=any(max(n.dot(point)-offset for n,offset in planes)<=.05 for planes in hull_planes)
    result['collision_asset']=body['collision_asset'];result['hull_count']=len(hull_planes)
out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
out.write_text(json.dumps(result,indent=2),encoding='utf-8')
print('CONTACT_SURFACE_AUDIT',len(records),'last',records[-1:] or None,flush=True)
