"""Prepare a detailed CC0 university-campus exterior as four native UE meshes.
Generated geometry is visual-only and deliberately extends behind the sports-hall
window openings; competition CAD, navigation, and vehicle meshes remain unchanged.
Blender: blender -b --python prepare_campus_exterior.py -- --root <asset-dir>
"""
import bpy
import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from mathutils import Vector

def sha(path):
 h=hashlib.sha256()
 with path.open("rb") as f:
  for b in iter(lambda:f.read(1024*1024),b""):h.update(b)
 return h.hexdigest()

def mat(name,color,metal=0,rough=.65):
 m=bpy.data.materials.new(name);m.diffuse_color=(*color,1);m.use_nodes=True
 bs=m.node_tree.nodes.get("Principled BSDF")
 if bs is None:
  bs=m.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
  output=m.node_tree.nodes.get("Material Output") or m.node_tree.nodes.new("ShaderNodeOutputMaterial")
  m.node_tree.links.new(bs.outputs["BSDF"],output.inputs["Surface"])
 bs.inputs["Base Color"].default_value=(*color,1)
 bs.inputs["Metallic"].default_value=metal
 bs.inputs["Roughness"].default_value=rough
 return m
materials={
 "ExtGrass":mat("ExtGrass",(.24,.32,.13)),
 "ExtAsphalt":mat("ExtAsphalt",(.075,.087,.09)),
 "ExtPaving":mat("ExtPaving",(.47,.49,.46)),
 "ExtCurbs":mat("ExtCurbs",(.56,.57,.52)),
 "ExtFacade":mat("ExtFacade",(.70,.72,.68)),
 "ExtBrick":mat("ExtBrick",(.39,.25,.19)),
 "ExtGlass":mat("ExtGlass",(.21,.37,.48),.18,.15),
 "ExtFrames":mat("ExtFrames",(.12,.17,.20),.62,.25),
 "ExtRoof":mat("ExtRoof",(.21,.26,.31),.55,.43),
 "ExtTrim":mat("ExtTrim",(.73,.75,.69),.10,.45),
 "ExtBenchWood":mat("ExtBenchWood",(.44,.27,.14),0,.44),
 "ExtMetal":mat("ExtMetal",(.24,.28,.32),.72,.27),
 "ExtLeaves":mat("ExtLeaves",(.13,.37,.18))
}
collections={k:[] for k in ("Ground","Buildings","Furnishings")}

def box(group,name,location,size,material):
 bpy.ops.mesh.primitive_cube_add(size=1,location=location)
 obj=bpy.context.object;obj.name=name
 obj.dimensions=size
 bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
 obj.data.materials.append(materials[material])
 collections[group].append(obj)
 return obj

def cylinder(group,name,location,radius,depth,material,vertices=12,rotation=None):
 bpy.ops.mesh.primitive_cylinder_add(vertices=vertices,radius=radius,depth=depth,location=location)
 ob=bpy.context.object;ob.name=name;ob.data.materials.append(materials[material])
 if rotation is not None:ob.rotation_euler=rotation
 collections[group].append(ob)
 return ob

# The local, repeatable 80 x 70 m courtyard tile. Four instances form
# a 160 x 140 m setting without any >100m geometry-group instance.
box("Ground","Lawn",-0.0, (1,1,1),"ExtGrass") if False else None
box("Ground","ContinuousGrass",(0,0,-.69),(80,70,.14),"ExtGrass")
for y in (-27,27):
 box("Ground","LongPath",(0,y,-.600),(80,2.8,.04),"ExtPaving")
 for yy in (y-1.46,y+1.46):
  box("Ground","LongCurb",(0,yy,-.577),(80,.09,.095),"ExtCurbs")
for x in (-32,32):
 box("Ground","CrossPath",(x,0,-.594),(2.6,70,.045),"ExtPaving")
for x in (-29.7,29.7):
 box("Ground","ParkingLane",(x,0,-.623),(4.2,70,.028),"ExtAsphalt")
for x in (-30,30):
 for y in range(-30,31,10):
  box("Ground","LaneDash",(x,y,-.603),(.09,2,.012),"ExtTrim")
# Small quadrilateral flower-bed edges are deliberately repeated in the tiled campus.
for x,y in ((-20,-20),(20,20),(-20,20),(20,-20)):
 for delta in (-2.2,2.2):
  box("Ground","PlantBedEdgeLong",(x,y+delta,-.54),(5.2,.12,.20),"ExtCurbs")
  box("Ground","PlantBedEdgeShort",(x+delta,y,-.54),(.12,4.4,.20),"ExtCurbs")
 box("Ground","PlantBedSoil",(x,y,-.555),(4.95,4.05,.08),"ExtBrick")

# A two/three-storey university teaching wing. The facade is genuine 3D:
# individual recessed windows, metal mullions, concrete sills and entrance.
box("Buildings","MainBuilding",(0,0,5.4),(31,13.4,10.8),"ExtFacade")
box("Buildings","BrickPlinth",(0,0,.85),(31.5,13.8,1.7),"ExtBrick")
box("Buildings","FlatRoof",(0,0,11.1),(32,14.2,.65),"ExtRoof")
box("Buildings","ParapetN",(0,7.12,11.8),(32,.45,1),"ExtTrim")
box("Buildings","ParapetS",(0,-7.12,11.8),(32,.45,1),"ExtTrim")
for side in (-1,1):
 # two tall facades with over 80 distinct high-quality modelled windows
 for floor in range(3):
  z=2.55+floor*2.75
  for bay in range(11):
   x=(bay-5)*2.65
   y=side*6.76
   box("Buildings","DarkWindowReveal",(x,y,z),(2.1,.16,1.88),"ExtFrames")
   box("Buildings","WindowGlass",(x,y+side*.095,z),(1.89,.04,1.67),"ExtGlass")
   box("Buildings","WindowHorizontalBar",(x,y+side*.121,z),(1.89,.075,.045),"ExtFrames")
   box("Buildings","WindowVerticalBar",(x,y+side*.121,z),(.050,.076,1.67),"ExtFrames")
   box("Buildings","WindowSill",(x,y+side*.175,z-1),(2.32,.32,.12),"ExtTrim")
 for floor in range(1,3):
  box("Buildings","HorizontalBand",(0,side*6.89,floor*3.15+.5),(31.4,.26,.20),"ExtTrim")
# Distinct main entrance with transparent doors and a cantilevered rain canopy
box("Buildings","EntranceReveal",(0,-6.89,1.54),(5.1,.28,3.08),"ExtFrames")
box("Buildings","DoorGlassLeft",(-1.25,-7.06,1.54),(2.35,.07,2.82),"ExtGlass")
box("Buildings","DoorGlassRight",(1.25,-7.06,1.54),(2.35,.07,2.82),"ExtGlass")
box("Buildings","DoorFrame",(0,-7.10,1.54),(.09,.1,2.89),"ExtFrames")
box("Buildings","Awning",(0,-8.4,3.12),(7,3,.22),"ExtRoof")
for x in (-3.25,3.25):
 cylinder("Buildings","AwningSupport",(x,-9.62,1.54),.07,2.95,"ExtMetal")

# Street furniture modular prefab; repeat near trees and approach roads.
for x in (-1.95,1.95):
 for y in (-.5,.6):
  box("Furnishings","BenchPlank",(x,y,.43),(1.65,.20,.10),"ExtBenchWood")
 box("Furnishings","BenchBack",(x,.90,.78),(1.65,.08,.66),"ExtBenchWood")
 for dx in (-.6,.6):
  box("Furnishings","BenchSupport",(x+dx,.1,.22),(.09,.7,.42),"ExtMetal")
for x in (-3.8,3.8):
 cylinder("Furnishings","CampusLampPole",(x,0,2.3),.052,4.6,"ExtMetal")
 box("Furnishings","Luminaire",(x,0,4.61),(.7,.42,.16),"ExtTrim")
 cylinder("Furnishings","LampFoundation",(x,0,.1),.22,.2,"ExtCurbs")
for x in (-3,0,3):
 box("Furnishings","LowHedge",(x,-2.95,.39),(1.8,.73,.75),"ExtLeaves")
box("Furnishings","WayfindingPillar",(0,2.25,1.0),(.63,.31,2),"ExtFrames")
box("Furnishings","WayfindingFace",(0,2.065,1.24),(.48,.025,1.12),"ExtTrim")

def join(objects,name):
 bpy.ops.object.select_all(action='DESELECT')
 for o in objects:o.select_set(True)
 bpy.context.view_layer.objects.active=objects[0]
 if len(objects)>1:bpy.ops.object.join()
 out=bpy.context.view_layer.objects.active;out.name=name;out.data.name=name
 return out

prepared={}
out_objects=[]
for key in ["Ground","Buildings","Furnishings"]:
 ob=join(collections[key],"RMExterior"+key)
 out_objects.append(ob)

treefile=root/"sources/polyhaven/island_tree_02/island_tree_02_1k.gltf" if False else None
def prepare_tree(root):
 p=root/"sources/polyhaven/island_tree_02/island_tree_02_1k.gltf"
 pre={id(o) for o in bpy.context.scene.objects}
 bpy.ops.import_scene.gltf(filepath=str(p))
 imported=[o for o in bpy.context.scene.objects if id(o) not in pre and o.type=="MESH"]
 if not imported:raise RuntimeError("No CC0 tree meshes imported")
 ob=join(imported,"RMExteriorTree")
 # Scaled to ~8 m to silhouette naturally through 8m gym high-side windows.
 ob.scale=(2.30,2.30,2.30)
 bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
 bpy.context.view_layer.update()
 lower=min((ob.matrix_world@Vector(c)).z for c in ob.bound_box)
 ob.location.z-=lower
 bpy.ops.object.transform_apply(location=True,rotation=False,scale=False)
 pre_count=len(ob.data.polygons)
 mod=ob.modifiers.new("GPU_Friendly_DistanceTree","DECIMATE")
 mod.ratio=.115
 bpy.context.view_layer.objects.active=ob
 try:bpy.ops.object.modifier_apply(modifier=mod.name)
 except Exception:
  ob.modifiers.remove(mod)
 print("CC0_TREE_TRIANGLES",pre_count,len(ob.data.polygons),flush=True)
 out_objects.append(ob)
 return ob
if __name__=="__main__":
 ap=argparse.ArgumentParser()
 ap.add_argument("--root",type=Path,required=True)
 ap.add_argument("--out",type=Path,required=True)
 args=ap.parse_args(sys.argv[sys.argv.index("--")+1:])
 root=args.root.resolve();out=args.out.resolve()
 if out.exists():raise FileExistsError("Export destination must be new: "+str(out))
 out.mkdir(parents=True)
 tree=prepare_tree(root)
 for ob in out_objects:
  bpy.ops.object.select_all(action='DESELECT');ob.select_set(True)
  bpy.context.view_layer.objects.active=ob
  path=out/(ob.name+".obj")
  bpy.ops.wm.obj_export(filepath=str(path),export_selected_objects=True,forward_axis="Y",up_axis="Z",global_scale=1.0,apply_modifiers=True,export_uv=True,export_normals=True,export_materials=True,export_triangulated_mesh=True,path_mode="RELATIVE")
  bmin=[min((ob.matrix_world@Vector(c))[i] for c in ob.bound_box) for i in range(3)]
  bmax=[max((ob.matrix_world@Vector(c))[i] for c in ob.bound_box) for i in range(3)]
  slots=[x.name for x in ob.data.materials]
  prepared[ob.name]={"asset_name":ob.name,"file":path.name,"bounds_min_m":bmin,"bounds_max_m":bmax,"triangles":len(ob.data.polygons),"material_slots":slots,"sha256":sha(path),"bytes":path.stat().st_size}
 manifest={"schema":"rmsim_campus_exterior_prepared_v1","units":"UE metres; source native vertex coordinates","source_license":"CC0-1.0","meshes":prepared,"asset_root":str(root),"blender_version":bpy.app.version_string,
   "geometry_note":"Visual-only campus landscaping behind windows. Competition field collision, navigation, vehicle are unchanged."}
 (out/"prepared.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
 print("CAMPUS_EXTERIOR_PREPARED",json.dumps({k:{"triangles":v["triangles"],"bounds_min_m":v["bounds_min_m"],"bounds_max_m":v["bounds_max_m"],"materials":v["material_slots"]} for k,v in prepared.items()}),flush=True)
