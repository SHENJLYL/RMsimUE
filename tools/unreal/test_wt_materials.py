"""Small actual-UE authoring check for WT adapter APIs and shader compilation.

Run as UnrealEditor-Cmd ... -run=pythonscript -script=<this file>.
Does not import the whole scene or overwrite existing verification.
"""
import json
from pathlib import Path
import sys

import unreal

sys.path.insert(0,str(Path(__file__).resolve().parent))
from wt_materials import create_materials, material_id

root=Path(__file__).resolve().parents[2]
source=root/'artifacts/maps/wt_existing/avg_vlaanderen'
output=root/'artifacts/maps/wt_existing/material-api-check'
output.mkdir(exist_ok=False)
specs=[]
for name in ('france_town_building_2_floors_c','tree_poplar_bush_medium_a'):
    data=json.loads((source/'models-all-07'/name/'model.json').read_text(encoding='utf-8'))
    for index in data['used_material_indices'][:2]:
        spec=data['materials'][index]
        specs.append(dict(spec,id=material_id(spec)))
manifest=dict(materials=specs,extraction_root=str(source))
(output/'scene-source.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
mapping,terrain=create_materials(output,'/Game/RMSim/Maps/WT_MaterialCheck')
result=dict(materials={key:value.get_path_name() for key,value in mapping.items()},terrain=terrain.get_path_name(),
            scope='actual UE graph authoring; inspect shader compiler log and rendered appearance separately')
(output/'api-result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
unreal.log('WT_MATERIAL_API_CHECK_COMPLETE '+json.dumps(result))
