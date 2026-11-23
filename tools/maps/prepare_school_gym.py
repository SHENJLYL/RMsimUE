"""Convert downloaded SketchUp school gym into capped native UE spatial blocks.
Source SKP/glb remains untouched. All coordinates in UE metres; groups are visuals,
and the competition field, drive, navigation and physical border remain source-owned.
"""
import bpy,sys,json,hashlib,math,collections
from pathlib import Path
from mathutils import Vector,Matrix
def sha(path):
 h=hashlib.sha256()
 with path.open("rb") as f:
  for data in iter(lambda:f.read(2**20),b""):h.update(data)
 return h.hexdigest()
def bounds(ob):
 cs=[ob.matrix_world@Vector(p) for p in ob.bound_box]
 return ([min(p[i] for p in cs) for i in range(3)],[max(p[i] for p in cs) for i in range(3)])
def base_color(mat):
 if not mat or not mat.use_nodes:return [0.8,0.8,0.8,1]
 bs=next((node for node in mat.node_tree.nodes if node.type=="BSDF_PRINCIPLED"),None)
 if bs:return [float(v) for v in bs.inputs["Base Color"].default_value]
 return [float(v) for v in mat.diffuse_color]
def color_texture(mat):
 if not mat or not mat.use_nodes:return None
 bs=next((node for node in mat.node_tree.nodes if node.type=="BSDF_PRINCIPLED"),None)
 if not bs:return None
 links=list(bs.inputs["Base Color"].links)
 if not links:return None
 node=links[0].from_node
 if node.type=="TEX_IMAGE" and node.image:return node.image
 return None
def user_name(mat):
 return mat.name
def create_logo(name,png,x,y,z,normal_positive,mat_cache):
 mat=bpy.data.materials.new(name);mat.use_nodes=True
 nodes=mat.node_tree.nodes;nodes.clear()
 bs=nodes.new("ShaderNodeBsdfPrincipled");out=nodes.new("ShaderNodeOutputMaterial");nodes_link=mat.node_tree.links
 nodes_link.new(bs.outputs["BSDF"],out.inputs["Surface"])
 tex=nodes.new("ShaderNodeTexImage");tex.image=bpy.data.images.load(str(png),check_existing=True)
 nodes_link.new(tex.outputs["Color"],bs.inputs["Base Color"]);bs.inputs["Roughness"].default_value=.6
 verts=[(-4.0,0,-.5),(4.0,0,-.5),(4.0,0,.5),(-4.0,0,.5)]
 mesh=bpy.data.meshes.new(name);mesh.from_pydata(verts,[],[(0,1,2,3)]);mesh.update()
 ob=bpy.data.objects.new(name,mesh);bpy.context.collection.objects.link(ob)
 ob.location=(x,y,z)
 if normal_positive:mesh.polygons[0].flip()
 ob.data.materials.append(mat)
 uv=mesh.uv_layers.new(name="UVMap")
 for p,uvcoord in zip(mesh.polygons[0].loop_indices,[(0,0),(1,0),(1,1),(0,1)]):uv.data[p].uv=uvcoord
 mat_cache[name]={"base_color_factor":[1,1,1,1],"base_color_texture":"textures/"+name+".png","semantic":"brand","alpha_mode":"OPAQUE"}
 return ob
if __name__=="__main__":
 from argparse import ArgumentParser
 parser=ArgumentParser()
 parser.add_argument("--source",required=True,type=Path)
 parser.add_argument("--output",required=True,type=Path)
 parser.add_argument("--branding",required=True,type=Path)
 args=parser.parse_args(sys.argv[sys.argv.index("--")+1:])
 source=args.source.resolve();output=args.output.resolve();branding=args.branding.resolve()
 if output.exists():raise FileExistsError("Output must be new: "+str(output))
 output.mkdir(parents=True);(output/"textures").mkdir()
 bpy.ops.object.select_all(action="SELECT");bpy.ops.object.delete(use_global=False)
 bpy.ops.import_scene.gltf(filepath=str(source))
 objects=[o for o in bpy.context.scene.objects if o.type=="MESH"]
 assert len(objects)>3000,len(objects)
 print("ORIGINAL_INSTANCES",len(objects),flush=True)
 # Source GLTF exported as mm; Blender imported Z up. Long side originally Y.
 # New ROS venue X is long side and central RMUC arena remains untouched.
 # Offset original floor top 0.7112m to 3cm BELOW source field top -0.305m.
 transform=Matrix.Translation((24.6888,-15.737221,-1.0462))@Matrix.Rotation(math.pi/2,4,'Z')@Matrix.Scale(.001,4)
 selected=collections.defaultdict(list)
 names=collections.Counter();skipped=collections.Counter()
 for ob in objects:
  mats=[m.name for m in ob.data.materials if m]
  # 11..20 originate from the two basketball goals (two symmetric components).
  if ob.name.startswith(("n1592_g","n1594_g")):
   skipped["two_source_basketball_goals"]+=1;continue
  original=ob.matrix_world.copy()
  ob.parent=None
  ob.matrix_world=transform@original
  lo,hi=bounds(ob);c=[(a+b)*.5 for a,b in zip(lo,hi)]
  if max(abs(c[0]),abs(c[1]),abs(c[2]))>150:raise RuntimeError("Converted school model exceeds reasonable bounds")
  # Large source basketball markings embedded in a horizontal court mesh
  # will sit 30mm BELOW the official CAD field, avoiding flicker.
  level=0 if (hi[2]<.9 and (hi[0]-lo[0]>8 or hi[1]-lo[1]>8)) else (1 if c[2]<3.75 else (2 if c[2]<8.2 else 3))
  if any((m and m.get("surface_render_method",None)=="DITHERED") for m in []):pass
  mat_transparency=any((m and float(base_color(m)[3])<.95) for m in ob.data.materials)
  class_index=4 if mat_transparency else level
  quadrant=(0 if c[0]<0 else 1)+(0 if c[1]<0 else 2)
  group="RMSchool_%d_%d"%(class_index,quadrant)
  selected[group].append(ob);names[group]+=len(ob.data.polygons)
 if not 1<=len(selected)<=24:raise RuntimeError("Expected at most 24 asset groups")
 print("GROUPS",len(selected),"SKIPPED",skipped,"POLY_PER_GROUP",dict(names),flush=True)
 # Add brand signage inside the long building walls, placed above spectator level.
 png=branding/"sign_horizontal_4x1_white.png"
 if not png.is_file():raise FileNotFoundError(str(png))
 logo_mat={}
 for sign,x,y,z,flip in [
  ("RMBrandLongNorth",0,17.6,5.8,False),("RMBrandLongSouth",0,-17.6,5.8,True),
  ("RMBrandShortEast",25,0,5.6,False),("RMBrandShortWest",-25,0,5.6,False)
 ]:
  ob=create_logo(sign,png,x,y,z,flip,logo_mat)
  selected["RMSchool_%d_%d"%(3,(0 if x<0 else 1)+(0 if y<0 else 2))].append(ob)
 # Source alpha/UV textures are extracted as independent image files, avoiding broken links.
 info={}
 for name,oblist in selected.items():
  for obj in oblist:
   for mat in obj.data.materials:
    if not mat:continue
    mid=user_name(mat)
    if mid in info:continue
    vals={"base_color_factor":base_color(mat),"metallic_factor":0.0,"roughness_factor":.74,"semantic":"source","alpha_mode":"OPAQUE"}
    tex=color_texture(mat)
    if tex:
     target=output/"textures"/(mid+".png")
     if not target.exists():
      try:tex.save_render(str(target))
      except Exception as e: print("MATERIAL_TEXTURE_EXPORT_FAILED",mid,str(e)[:140],flush=True)
     if target.exists():vals["base_color_texture"]="textures/"+target.name
    if vals["base_color_factor"][3]<.95:
     vals["alpha_mode"]="BLEND";vals["semantic"]="glass"
    if mid in logo_mat:vals=logo_mat[mid]
    info[mid]=vals
 for sign in logo_mat:
  target=output/"textures"/(sign+".png")
  if not target.exists():
   import shutil;shutil.copy2(png,target)
  info[user_name(bpy.data.materials[sign])]=logo_mat[sign]
 # OBJ retains source materials and UVs. UE imports one merged mesh per block.
 prepared=[]
 for name,oblist in sorted(selected.items()):
  bpy.ops.object.select_all(action="DESELECT")
  for ob in oblist:ob.select_set(True)
  bpy.context.view_layer.objects.active=oblist[0]
  dest=output/(name+".obj")
  bpy.ops.wm.obj_export(filepath=str(dest),export_selected_objects=True,forward_axis="Y",up_axis="Z",global_scale=1.0,apply_modifiers=True,export_uv=True,export_normals=True,export_materials=True,export_triangulated_mesh=True,path_mode="RELATIVE")
  limits=[bounds(o) for o in oblist]
  lo=[min(x[0][i] for x in limits) for i in range(3)]
  hi=[max(x[1][i] for x in limits) for i in range(3)]
  mats=sorted(set(user_name(mat) for obj in oblist for mat in obj.data.materials if mat))
  polys=sum(1 for line in dest.open("r",encoding="utf-8",errors="replace") if line.startswith("f "))
  prepared.append({"file":dest.name,"asset_name":name,"bounds_min_m":lo,"bounds_max_m":hi,"triangles":polys,"material_slots":mats,"sha256":sha(dest),"bytes":dest.stat().st_size,"cast_shadow":True})
  print("SCHOOL_PREPARED",name,len(oblist),"polygons",polys,"file_bytes",dest.stat().st_size,flush=True)
 result={"schema":"rmsim_school_gym_prepared_v1","source_file_sha256":sha(source),"source_format":"OpenSKP -> instanced GLB -> grouped OBJ","units":"UE metre","meshes":prepared,"materials":info,"removed":dict(skipped),"instances":len(objects),"geometry_note":"Basketball goals deleted; original court face 30mm below RMUC field; source dimensions unchanged, non-collision venue shell."}
 (output/"prepared.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
 print("SCHOOL_GEOMETRY_READY",len(prepared),len(info),flush=True)
