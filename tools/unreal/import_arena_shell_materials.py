"""Create low-cost UE matte stadium materials from the RMUC arena palette.

Only simple lit constant nodes; no texture samples, transparency, animated
spectators, depth blur or shadow-casting material features.
"""
from __future__ import annotations
from pathlib import Path
import re
import hashlib
import json
import re
import unreal

ROOT=Path(__file__).resolve().parents[2]
PALETTE=ROOT/"assets/maps/rmuc2027/arena-shell.json"
PARENT="/Game/RMSim/Maps/rmuc2027/Arena/Materials"
COLORS={
    "facade":"Mat_Facade",
    "seats":"Mat_Seats",
    "upper_seats":"Mat_UpperSeats",
    "supports":"Mat_Supports",
    "roof":"Mat_Roof",
    "accent_red":"Mat_AccentRed",
    "accent_blue":"Mat_AccentBlue",
    "accent_light":"Mat_AccentLight",
}

def main():
    data=json.loads(PALETTE.read_text(encoding="utf-8"))
    if data["schema"]!="rmsim_arena_shell_v1" or data["map_id"]!="rmuc2027":
        raise ValueError("Arena palette identity mismatch")
    tools=unreal.AssetToolsHelpers.get_asset_tools()
    editor=unreal.MaterialEditingLibrary
    report={}
    for color_key,asset_name in COLORS.items():
        values=data["colors"][color_key]
        if len(values)!=3 or any(not 0<=x<=1 for x in values):
            raise ValueError("Invalid color "+color_key)
        full=f"{PARENT}/{asset_name}"
        material=unreal.load_asset(full)
        if not material:
            material=tools.create_asset(asset_name,PARENT,unreal.Material,unreal.MaterialFactoryNew())
        if not isinstance(material,unreal.Material):
            raise TypeError("Expected a native material "+full)
        editor.delete_all_material_expressions(material)
        node=editor.create_material_expression(material,unreal.MaterialExpressionConstant3Vector,-480,0)
        node.set_editor_property("constant",unreal.LinearColor(*values))
        editor.connect_material_property(node,"",unreal.MaterialProperty.MP_BASE_COLOR)
        rough=editor.create_material_expression(material,unreal.MaterialExpressionConstant,-480,240)
        rough.set_editor_property("r",0.82)
        editor.connect_material_property(rough,"",unreal.MaterialProperty.MP_ROUGHNESS)
        metal=editor.create_material_expression(material,unreal.MaterialExpressionConstant,-480,380)
        metal.set_editor_property("r",0.04 if color_key not in ("supports","roof") else 0.22)
        editor.connect_material_property(metal,"",unreal.MaterialProperty.MP_METALLIC)
        material.set_editor_property("two_sided",True)
        editor.recompile_material(material)
        if not unreal.EditorAssetLibrary.save_loaded_asset(material):
            raise RuntimeError("Cannot save "+full)
        report[color_key]={"asset":material.get_path_name(),"color":values}
        unreal.log("RMSIM_ARENA_PALETTE_IMPORT "+color_key+" "+full)
    match=re.search(r'-ArenaMaterialReport=(?:"([^"]+)"|(\S+))',
                    unreal.SystemLibrary.get_command_line())
    if not match:raise ValueError('ArenaMaterialReport is required')
    output=Path(match.group(1) or match.group(2)).resolve()
    if output.exists():raise FileExistsError('Use a new evidence report path')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps({"status":"passed","materials":report,
        "input_source_sha256":hashlib.sha256(PALETTE.read_bytes()).hexdigest(),
        "scope":"simple lit constant nodes, 8 palette categories, no texture sampling"},
         indent=2)+"\n",encoding="utf-8")
    unreal.log("RMSIM_ARENA_PALETTE_COMPLETE "+str(output))

if __name__=="__main__":
    main()
