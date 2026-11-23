"""UE editor-only import of pinned Kenney CC0 mini-arena accents.

Import only the 5 coarse off-field meshes. All runtime arena instances use
shared inexpensive colors, no physics, no per-asset texture streaming.
"""
from pathlib import Path
import hashlib
import json
import re
import sys
import unreal

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/unreal"))
from import_vehicle_assets import import_asset

def get_argument(name):
    match = re.search(r'-' + re.escape(name) + r'=(?:"([^"]+)"|(\S+))', unreal.SystemLibrary.get_command_line())
    if match is None:
        raise ValueError(name+" is required")
    return Path(match.group(1) or match.group(2)).resolve()

def main():
    source=get_argument("ArenaAssetSource")
    report=get_argument("ArenaImportReport")
    root=source.parent
    doc=json.loads(source.read_text(encoding="utf-8"))
    if doc["schema"]!="rmsim_cc0_arena_pack_v1" or doc["license"]!="CC0-1.0" or "Kenney" not in doc["author"]:
        raise ValueError("Invalid arena source/license")
    dest="/Game/RMSim/Maps/rmuc2027/Arena/Kenney"
    results={}
    for name in ("wall","stairs","column","border-straight","wall-gate"):
        file=root/(name+".fbx")
        if hashlib.sha256(file.read_bytes()).hexdigest()!=doc["selected_files"][file.name]:
            raise ValueError("CC0 arena mesh changed: "+file.name)
        options=unreal.FbxImportUI()
        for field,value in dict(import_mesh=True,import_as_skeletal=False,
                                mesh_type_to_import=unreal.FBXImportType.FBXIT_STATIC_MESH,
                                import_materials=False,import_textures=False).items():
            options.set_editor_property(field,value)
        settings=options.get_editor_property("static_mesh_import_data")
        for field,value in dict(combine_meshes=True,convert_scene=True,convert_scene_unit=True,
                                force_front_x_axis=True,transform_vertex_to_absolute=True,
                                bake_pivot_in_vertex=False,auto_generate_collision=False,
                                reorder_material_to_fbx_order=True).items():
            settings.set_editor_property(field,value)
        settings.set_editor_property("normal_import_method",unreal.FBXNormalImportMethod.FBXNIM_IMPORT_NORMALS)
        imported=import_asset(file,dest,name.replace("-","_"),options)
        candidates=[x for x in imported if isinstance(x,unreal.StaticMesh)]
        if len(candidates)!=1:
            raise RuntimeError("Exactly one static mesh required for "+name)
        mesh=candidates[0]
        unreal.EditorAssetLibrary.save_loaded_asset(mesh)
        bounds=mesh.get_bounding_box()
        if not all(v>0.01 for v in (bounds.max.x-bounds.min.x,bounds.max.y-bounds.min.y,bounds.max.z-bounds.min.z)):
            raise ValueError("Invalid imported bounds "+name)
        results[name]={"path":mesh.get_path_name(),"bounds_cm":{"lo":[bounds.min.x,bounds.min.y,bounds.min.z],
                       "hi":[bounds.max.x,bounds.max.y,bounds.max.z]},
                       "source_sha256":doc["selected_files"][file.name]}
        unreal.log("RMSIM_CC0_ARENA_MESH "+name+" "+mesh.get_path_name())
    report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text(json.dumps({"status":"passed","license":"CC0-1.0","assets":results},indent=2)+"\n",encoding="utf-8")
    unreal.log("RMSIM_CC0_ARENA_IMPORT_COMPLETE "+str(report))

if __name__=="__main__":
    main()
