"""Read-only diagnostic of saved Flanders HISM visibility and material overrides.

Run in a single coordinated UnrealEditor-Cmd process, with -NullRHI:
  -run=pythonscript -script=<this file> -SceneSource=<scene-source.json>
  -VisibilityAuditOutput=<new evidence directory>

This checks saved editor-world flags. Runtime capture readiness is separate.
"""
import datetime
from pathlib import Path
import re
import sys
import traceback

import unreal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_wt_scene import digest, package_file, read, save


def inspect_world(world):
    records, errors = [], []
    actors = unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Actor)
    for actor in actors:
        for component in actor.get_components_by_class(unreal.HierarchicalInstancedStaticMeshComponent):
            blocking = component.get_collision_enabled() != unreal.CollisionEnabled.NO_COLLISION
            mesh = component.get_editor_property("static_mesh")
            flags = {name: component.get_editor_property(name) for name in (
                "visible", "hidden_in_game", "cast_shadow", "cast_hidden_shadow", "render_in_main_pass")}
            if not all(type(value) is bool for value in flags.values()):
                raise TypeError("visibility flags must be reflected bool properties")
            overrides = list(component.get_editor_property("override_materials"))
            record = dict(actor=actor.get_path_name(), label=actor.get_actor_label(),
                          component=component.get_path_name(), collision_component=blocking,
                          mesh=mesh.get_path_name() if mesh else None,
                          instance_count=component.get_instance_count(), **flags,
                          override_materials=[material.get_path_name() if material else None for material in overrides])
            records.append(record)
            if blocking and (not flags["hidden_in_game"] or flags["visible"] or flags["cast_shadow"]):
                errors.append(dict(component=record["component"], error="collision rendering flags", **flags))
            if not blocking and (not flags["visible"] or flags["hidden_in_game"]):
                errors.append(dict(component=record["component"], error="visual component hidden", **flags))
    collision = [record for record in records if record["collision_component"]]
    visual = [record for record in records if not record["collision_component"]]
    summary = dict(visual_components=len(visual), collision_components=len(collision),
                   visible_collision_components=sum(record["visible"] for record in collision),
                   unhidden_collision_components=sum(not record["hidden_in_game"] for record in collision),
                   shadow_casting_collision_components=sum(record["cast_shadow"] for record in collision),
                   hidden_visual_components=sum(not record["visible"] or record["hidden_in_game"] for record in visual),
                   components_with_material_overrides=sum(any(record["override_materials"]) for record in records))
    return dict(summary=summary, errors=errors, components=records)


def run(source_file, output):
    evidence = Path(output).resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    report = dict(schema="wt_saved_visibility_audit_v1", status="running", complete=False,
                  started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  scope="read_only_saved_editor_world_component_flags",
                  runtime_visibility_probed=False, render_readiness_probed=False,
                  script_sha256=digest(Path(__file__).resolve()))
    save(evidence / "verification.json", report)
    try:
        source_file = Path(source_file).resolve(strict=True)
        source = read(source_file)
        if source["id"] != "wt_vlaanderen" or source["schema"] != "rm_scene_map_source_v1":
            raise ValueError("expected prepared Flanders scene package")
        parts = read(package_file(source_file.parent, source["instances"]))["parts"]
        content = Path(unreal.Paths.project_content_dir()).resolve()
        level_file = content / "RMSim/Maps/wt_vlaanderen/World.umap"
        report.update(source_manifest=str(source_file), manifest_sha256=digest(source_file),
                      map_file=str(level_file), map_file_sha256=digest(level_file),
                      engine=unreal.SystemLibrary.get_engine_version())
        world = unreal.EditorLoadingAndSavingUtils.load_map(str(level_file))
        if not isinstance(world, unreal.World):
            raise RuntimeError("saved world load did not return World")
        report.update(inspect_world(world))
        expected_visual, expected_collision = len(parts), sum(bool(part["collision_asset"]) for part in parts)
        report["expected_counts"] = dict(visual_components=expected_visual, collision_components=expected_collision)
        if (report["summary"]["visual_components"] != expected_visual
                or report["summary"]["collision_components"] != expected_collision):
            raise RuntimeError("saved HISM inventory differs from the source manifest")
        if report["errors"]:
            raise RuntimeError("saved collision/visual flags differ from the import contract")
        report["map_file_after_sha256"] = digest(level_file)
        report["manifest_after_sha256"] = digest(source_file)
        if (report["map_file_sha256"] != report["map_file_after_sha256"]
                or report["manifest_sha256"] != report["manifest_after_sha256"]):
            raise RuntimeError("saved input bytes changed during read-only visibility inspection")
        report["status"] = "passed"
        unreal.log("WT_VISIBILITY_AUDIT_COMPLETE " + str(evidence / "verification.json"))
    except Exception as error:
        report.update(status="failed", error=str(error), traceback=traceback.format_exc())
        raise
    finally:
        report.update(complete=True, finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
        save(evidence / "verification.json", report)
    return report


if __name__ == "__main__":
    command = unreal.SystemLibrary.get_command_line()
    arguments = {}
    for name in ("SceneSource", "VisibilityAuditOutput"):
        match = re.search(r'-' + name + r'=(?:"([^"]+)"|(\S+))', command)
        if not match:
            raise ValueError("pass -" + name + "=<path>")
        arguments[name] = match.group(1) or match.group(2)
    run(arguments["SceneSource"], arguments["VisibilityAuditOutput"])
