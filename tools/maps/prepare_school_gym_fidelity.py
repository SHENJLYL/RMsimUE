"""Blender CPU preparation of source-faithful school gym candidate.

Run after restore_school_source_materials.py. Preserve all original structure
except the two source basketball goals; align the source sports floor centre.
Transparent source panes use separate blocks so opaque structure keeps Nanite.
"""
import bpy,sys,json,hashlib,math,shutil,collections
from pathlib import Path
from mathutils import Vector,Matrix

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def bounds(ob):
    cs=[ob.matrix_world@Vector(p) for p in ob.bound_box]
    return ([min(p[i] for p in cs) for i in range(3)],[max(p[i] for p in cs) for i in range(3)])
def copy_file(source,target):
    target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
def load_existing_brands(bundle,old_manifest,translation):
    result=[]
    for entry in old_manifest["meshes"]:
        if not entry["asset_name"].startswith("RMSchool_3_"):continue
        v=[None];uv=[None];faces=collections.defaultdict(list);active=""
        with (bundle/entry["file"]).open(encoding="utf8") as reader:
            for line in reader:
                if line.startswith("v "):v.append(tuple(map(float,line.split()[1:4])))
                elif line.startswith("vt "):uv.append(tuple(map(float,line.split()[1:3])))
                elif line.startswith("o "):active=line.split()[1]
                elif line.startswith("f ") and active.startswith("RMBrand"):
                    faces[active].append([tuple(map(int,c.split("/")[:2])) for c in line.split()[1:]])
        for name,fs in faces.items():
            vertices=[];texture_uv=[];polys=[]
            for face in fs:
                ids=[]
                for vi,ti in face:
                    ids.append(len(vertices));p=v[vi]
                    vertices.append(tuple(p[i]+translation[i] for i in range(3)))
                    texture_uv.append(uv[ti])
                polys.append(ids)
            mesh=bpy.data.meshes.new(name);mesh.from_pydata(vertices,[],polys);mesh.update()
            ob=bpy.data.objects.new(name,mesh);bpy.context.collection.objects.link(ob)
            layer=mesh.uv_layers.new(name="UVMap")
            for polygon in mesh.polygons:
                for li,vi in zip(polygon.loop_indices,polygon.vertices):layer.data[li].uv=texture_uv[vi]
            mat=bpy.data.materials.new(name);ob.data.materials.append(mat);result.append(ob)
    assert len(result)==4,len(result)
    return result

def main():
    from argparse import ArgumentParser
    parser=ArgumentParser()
    parser.add_argument("--candidate",required=True,type=Path)
    parser.add_argument("--original-bundle",required=True,type=Path)
    args=parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    output=args.candidate.resolve();old=args.original_bundle.resolve()
    prepared_path=output/"prepared.json"
    if prepared_path.exists():raise FileExistsError(prepared_path)
    source=output/"Indoor_Basketball_Gym_source_fidelity_mm.glb"
    material_data=json.loads((output/"source-materials.json").read_text(encoding="utf8"))
    old_manifest=json.loads((old/"prepared.json").read_text(encoding="utf-8-sig"))
    bpy.ops.object.select_all(action="SELECT");bpy.ops.object.delete(use_global=False)
    bpy.ops.import_scene.gltf(filepath=str(source))
    objects=[ob for ob in bpy.context.scene.objects if ob.type=="MESH"]
    assert len(objects)==7626,len(objects)
    transform=Matrix.Translation((24.6729248,-22.9023125,-1.0462))@Matrix.Rotation(math.pi/2,4,"Z")@Matrix.Scale(.001,4)
    selected=collections.defaultdict(list);removed=[]
    for ob in objects:
        if ob.name.startswith(("n1592_g","n1594_g")):
            removed.append(ob.name);continue
        world=ob.matrix_world.copy();ob.parent=None;ob.matrix_world=transform@world
        lo,hi=bounds(ob);c=[(a+b)*.5 for a,b in zip(lo,hi)]
        mats=[material_data[m.name] for m in ob.data.materials if m]
        transparent=any(m["alpha_mode"]=="BLEND" for m in mats)
        level=4 if transparent else 0 if (hi[2]<.9 and (hi[0]-lo[0]>8 or hi[1]-lo[1]>8)) else 1 if c[2]<3.75 else 2 if c[2]<8.2 else 3
        quadrant=(0 if c[0]<0 else 1)+(0 if c[1]<0 else 2)
        selected[f"RMSchool_{level}_{quadrant}"].append(ob)
    delta=(-.0158752,-7.1650915,0.)
    brands=load_existing_brands(old,old_manifest,delta)
    for ob in brands:
        c=ob.data.vertices[0].co
        quadrant=(0 if c[0]<0 else 1)+(0 if c[1]<0 else 2)
        selected[f"RMSchool_3_{quadrant}"].append(ob)
        material_data[ob.name]=old_manifest["materials"][ob.name]
        relative=material_data[ob.name]["base_color_texture"]
        copy_file(old/relative,output/relative)
    entries=[];source_counts={}
    for name,obs in sorted(selected.items()):
        bpy.ops.object.select_all(action="DESELECT")
        for ob in obs:ob.select_set(True)
        bpy.context.view_layer.objects.active=obs[0]
        dest=output/(name+".obj")
        bpy.ops.wm.obj_export(filepath=str(dest),export_selected_objects=True,
          forward_axis="Y",up_axis="Z",global_scale=1.,apply_modifiers=True,
          export_uv=True,export_normals=True,export_materials=True,
          export_triangulated_mesh=True,path_mode="RELATIVE")
        boxes=[bounds(ob) for ob in obs]
        lo=[min(b[0][i] for b in boxes) for i in range(3)]
        hi=[max(b[1][i] for b in boxes) for i in range(3)]
        mats=sorted({m.name for ob in obs for m in ob.data.materials if m})
        triangles=sum(line.startswith("f ") for line in dest.open(encoding="utf8"))
        transparent=any(material_data[n]["alpha_mode"]=="BLEND" for n in mats)
        assert not(transparent and any(material_data[n]["alpha_mode"]!="BLEND" for n in mats))
        entries.append({"file":dest.name,"asset_name":name,"bounds_min_m":lo,"bounds_max_m":hi,
          "triangles":triangles,"material_slots":mats,"sha256":sha(dest),"bytes":dest.stat().st_size,
          "cast_shadow":not transparent,"source_instances":len(obs)})
        source_counts[name]=len(obs)
        print("PREPARED",name,len(obs),triangles,flush=True)
    # Keep the exterior candidate independently adjustable by the root agent.
    for entry in old_manifest["meshes"]:
        if not entry["asset_name"].startswith("RMExterior"):continue
        copy_file(old/entry["file"],output/entry["file"])
        mtl=(old/entry["file"]).with_suffix(".mtl")
        if mtl.exists():copy_file(mtl,(output/entry["file"]).with_suffix(".mtl"))
        entries.append(entry)
        for slot in entry["material_slots"]:
            material_data[slot]=old_manifest["materials"][slot]
            for key,value in material_data[slot].items():
                if key.endswith("_texture"):copy_file(old/value,output/value)
    used={s for e in entries for s in e["material_slots"]}
    material_data={k:v for k,v in material_data.items() if k in used}
    assert len(entries)<=32,len(entries)
    result={"schema":"rmsim_school_gym_prepared_v1","source_file_sha256":sha(source),
      "source_format":"Original OpenSKP GLB geometry + recovered legacy material inheritance + grouped OBJ",
      "units":"UE metre","meshes":entries,"materials":material_data,
      "removed":{"two_source_basketball_goals":removed},
      "instances":len(objects),"source_transform":{"translation_m":[24.6729248,-22.9023125,-1.0462],"rotation_z_deg":90,"source_mm_to_m":.001},
      "geometry_note":"Original source structures retained; two basketball goals omitted; no field-centroid face deletion; no added audience seats or concrete tiers.",
      "source_material_audit":"source-material-audit.json","source_group_instances":source_counts}
    prepared_path.write_text(json.dumps(result,indent=2)+"\n",encoding="utf8")
    print("SOURCE_FIDELITY_CANDIDATE_READY",len(entries),len(material_data),flush=True)
if __name__=="__main__":main()
