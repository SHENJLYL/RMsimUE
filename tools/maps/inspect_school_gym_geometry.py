import bpy,sys,json,collections,math,statistics
from mathutils import Vector
from pathlib import Path
p=Path(sys.argv[sys.argv.index('--')+1])
bpy.ops.object.select_all(action="SELECT");bpy.ops.object.delete(use_global=False)
bpy.ops.import_scene.gltf(filepath=str(p))
bpy.context.view_layer.update()
meshes=[o for o in bpy.context.scene.objects if o.type=="MESH"]
rows=collections.defaultdict(list)
globmin=[1e30]*3;globmax=[-1e30]*3
for ob in meshes:
 bounds=[ob.matrix_world@Vector(c) for c in ob.bound_box]
 lo=[min(pt[i] for pt in bounds)*.001 for i in range(3)]
 hi=[max(pt[i] for pt in bounds)*.001 for i in range(3)]
 for i in range(3):globmin[i]=min(globmin[i],lo[i]);globmax[i]=max(globmax[i],hi[i])
 cen=[(a+b)*.5 for a,b in zip(lo,hi)]
 size=[b-a for a,b in zip(lo,hi)]
 mat=ob.data.materials[0].name if ob.data.materials and ob.data.materials[0] else "MISSING"
 rows[mat].append((ob.name,cen,size,len(ob.data.polygons),lo,hi))
print('GLOBAL_MIN',globmin,'GLOBAL_MAX',globmax,flush=True)
for name,items in sorted(rows.items(),key=lambda x:-len(x[1])):
 broad=sorted(items,key=lambda x: -x[3])[:3]
 print('MAT',name,'COUNT',len(items),'TRIANGLES',sum(x[3] for x in items),'BROAD',[(x[0],x[1],x[2],x[3]) for x in broad],flush=True)
print('MODEL_CATEGORY_TOP_SIZE')
for x in sorted([item for vv in rows.values() for item in vv],key=lambda x:-x[3])[:45]:print('LARGE',x[0],x[1],x[2],x[3],flush=True)
