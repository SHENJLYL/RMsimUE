"""Read-only inventory of components outside the original RMI actor set."""
import json
from pathlib import Path
import re
import unreal


def argument(name):
    match = re.search(r'(?:^|\s)-' + name + r'=(?:"([^"]+)"|(\S+))', unreal.SystemLibrary.get_command_line(), re.I)
    if not match:
        raise ValueError('missing ' + name)
    return match.group(1) or match.group(2)

source_file = Path(argument('SceneSource')).resolve(strict=True)
package = source_file.parent
source = json.loads(source_file.read_text(encoding='utf-8-sig'))
instances = json.loads((package / source['instances']).read_text(encoding='utf-8-sig'))
expected = {part['id'] for part in instances['parts']}
output = Path(argument('SceneExtraAuditOutput'))
output.mkdir(parents=True, exist_ok=False)
world = unreal.EditorLoadingAndSavingUtils.load_map('/Game/RMSim/Maps/' + source['id'] + '/World')
actors = unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Actor)
extras = []
all_count = 0
source_count = 0
for actor in actors:
    components = list(actor.get_components_by_class(unreal.HierarchicalInstancedStaticMeshComponent))
    all_count += len(components)
    label = actor.get_actor_label()
    if label in expected:
        source_count += len(components)
    elif components:
        row = dict(label=label, actor=actor.get_path_name(), actor_class=actor.get_class().get_name(),
                   tags=[str(x) for x in actor.get_editor_property('tags')], components=[])
        for component in components:
            mesh = component.get_editor_property('static_mesh')
            row['components'].append(dict(name=component.get_name(), class_name=component.get_class().get_name(),
                path=component.get_path_name(), tags=[str(x) for x in component.get_editor_property('component_tags')],
                mesh=mesh.get_path_name() if mesh else None, instance_count=component.get_instance_count(),
                collision=str(component.get_collision_enabled()),
                hidden_in_game=bool(component.get_editor_property('hidden_in_game')),
                visible=bool(component.get_editor_property('visible'))))
        extras.append(row)
report = dict(schema='wt_scene_extra_component_inventory_v1', status='passed', complete=True,
              mode='read_only_NullRHI', world=world.get_path_name(), all_hism_components=all_count,
              source_hism_components=source_count, extra_hism_components=all_count-source_count,
              extra_actors=extras, native_assets_written=[])
(output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
unreal.log('WT_SCENE_EXTRA_COMPONENT_INVENTORY ' + str(output / 'verification.json'))
