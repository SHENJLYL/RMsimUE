import bpy,sys,json,collections,math,traceback,time
from pathlib import Path
from mathutils import Vector
source=Path(sys.argv[sys.argv.index("--")+1])
start=time.perf_counter()
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
bpy.ops.import_scene.gltf(filepath=str(source))
print("IMPORT_SECONDS",round(time.perf_counter()-start,3),flush=True)
meshes=[x for x in bpy.context.scene.objects if x.type=="MESH"]
print("OBJECTS",len(bpy.context.scene.objects),"MESH",len(meshes),"INSTANCED_COLLECTIONS",sum(x.instance_type=="COLLECTION" for x in bpy.context.scene.objects),flush=True)
types=collections.Counter(x.type for x in bpy.context.scene.objects)
print("TYPES",types,flush=True)
materials=collections.Counter()
for x in meshes:
 for mat in x.data.materials:
  if mat: materials[mat.name]+=1
print("MATERIALS",len(materials),materials.most_common(100),flush=True)
from collections import Counter
names=Counter(x.name.split(".")[0] for x in meshes)
print("NAME_PREFIX",names.most_common(70),flush=True)
print("FIRST_OBJECTS",[(x.name,x.type,x.instance_type,[(round(v,2)) for v in x.dimensions]) for x in list(bpy.context.scene.objects)[:40]],flush=True)
print("MATERIAL_DETAIL",[(x.name, x.blend_method if hasattr(x,'blend_method') else '-',[(k.name,k.type) for k in x.node_tree.nodes][:12] if x.node_tree else []) for x in list(bpy.data.materials)[:15]],flush=True)
for o in bpy.context.scene.objects:
 if o.type=="MESH" and any(t in o.name.lower() for t in ['basket','court','stand','seat','window']):
  print("POSSIBLE",o.name,[(round(v,2)) for v in o.dimensions],flush=True)
print("DONE",flush=True)
