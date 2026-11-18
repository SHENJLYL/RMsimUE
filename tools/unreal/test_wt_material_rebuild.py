"""Native regression: complete parent rebuild and repeatable saved leaf graph."""
import datetime
import json
from pathlib import Path
import re
import shutil
import sys
import traceback
import unreal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wt_materials import Textures, parent_material, terrain_material, make_instance
from verify_wt_materials import asset_file, digest, graph_record, save

root = Path(__file__).resolve().parents[2]
package = root/'assets/maps/wt_vlaanderen'
source_file = package/'scene-source.json'
source = json.loads(source_file.read_text(encoding='utf-8-sig'))
folder = '/Game/RMSim/Maps/wt_vlaanderen'
match = re.search(r'-MaterialRebuildOutput=(?:"([^"]+)"|(\S+))', unreal.SystemLibrary.get_command_line())
if not match:
    raise ValueError('pass -MaterialRebuildOutput=<fresh directory>')
output = Path(match.group(1) or match.group(2)).resolve()
output.mkdir(parents=True, exist_ok=False)
(output/'before').mkdir()
report = dict(schema='wt_native_material_rebuild_v1', status='running', complete=False,
    source_manifest_sha256=digest(source_file), original_source_sha256=source['source_sha256'],
    material_script_sha256=digest(Path(__file__).with_name('wt_materials.py')),
    engine=unreal.SystemLibrary.get_engine_version(), parents=[], leaf_rebuild=None,
    scope='native_full_graph_replacement_and_repeat_rebuild')
try:
    textures = Textures(package, Path(source['extraction_root']), folder)
    flavors = sorted({'Leaves' if s['shader']=='rendinst_facing_leaves' else
                      'Glass' if 'glass' in s['shader'] else
                      'Decal' if 'decal' in s['shader'] else 'Surface'
                      for s in source['materials']})
    leaves = None
    for flavor in [*flavors, 'Terrain']:
        name = 'WT_Terrain' if flavor=='Terrain' else 'WT_Parent_'+flavor
        path = folder+'/Materials/'+name
        filename = asset_file(path)
        old = unreal.load_asset(path)
        before_count = unreal.MaterialEditingLibrary.get_num_material_expressions(old)
        before_hash = digest(filename)
        shutil.copyfile(filename, output/'before'/filename.name)
        material = terrain_material(folder, textures) if flavor=='Terrain' else parent_material(folder, flavor, textures)
        after = graph_record(material, leaves=flavor=='Leaves')
        report['parents'].append(dict(asset=material.get_path_name(), before_count=before_count,
            before_sha256=before_hash, after_count=after['expression_count'],
            after_sha256=digest(filename), graph=after))
        if flavor=='Leaves':
            leaves = material
        save(output/'verification.json', report)
    first_graph = graph_record(leaves, leaves=True)
    leaves = parent_material(folder, 'Leaves', textures)
    second_graph = graph_record(leaves, leaves=True)
    if first_graph['expression_count'] != second_graph['expression_count']:
        raise RuntimeError('repeated leaf rebuild grew the expression collection')
    report['leaf_rebuild'] = dict(first_count=first_graph['expression_count'],
        second_count=second_graph['expression_count'], graph=second_graph,
        asset_sha256=digest(asset_file(leaves.get_path_name())))
    report['leaf_instances'] = []
    for spec in source['materials']:
        if spec['shader']=='rendinst_facing_leaves':
            identity, instance = make_instance(folder, spec, leaves, textures)
            report['leaf_instances'].append(dict(id=identity, asset=instance.get_path_name(),
                asset_sha256=digest(asset_file(instance.get_path_name()))))
    report.update(status='passed', complete=True,
        finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    unreal.log('WT_MATERIAL_REBUILD_COMPLETE '+str(output/'verification.json'))
except Exception as error:
    report.update(status='failed', complete=True, error=str(error), traceback=traceback.format_exc())
    raise
finally:
    save(output/'verification.json', report)
