"""Preserve prepared mission objects across a full base-scene reimport.

Call preflight before new_blank_map, then restore after saving the base World and
after the last report operation that reads the old world's UObjects. Preflight is
read-only and needs only Python's standard library. Restore runs inside the
caller's existing editor session and project mutex; it never launches Unreal.
"""
from argparse import Namespace
import hashlib
import json
import math
from pathlib import Path
import re
import struct

MAP_ID = "wt_vlaanderen"
SUPPLEMENT_ID = "wt_vlaanderen_zeppelin"
FOLDER = "/Game/RMSim/Maps/" + MAP_ID + "/MissionObjects"
WORLD = "/Game/RMSim/Maps/" + MAP_ID + "/World"


def _require(condition, message):
    if not condition:
        raise ValueError("Flanders mission supplement: " + message)


def _read(path):
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    _require(isinstance(value, dict), "expected a JSON object: " + str(path))
    return value


def _digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def _local(root, relative):
    _require(isinstance(relative, str) and bool(relative), "missing relative file")
    part = Path(relative)
    _require(not part.is_absolute() and not part.drive and ".." not in part.parts,
             "file escapes supplement: " + relative)
    result = (root / part).resolve(strict=True)
    _require(result.is_relative_to(root) and result.is_file(),
             "file escapes supplement or is not a file: " + relative)
    return result


def _check_hash(path, expected):
    _require(isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected),
             "missing SHA256 for " + str(path))
    actual = _digest(path)
    _require(actual == expected, "SHA256 changed: " + str(path))
    return actual


def _indexed(rows, label, key="id"):
    _require(isinstance(rows, list), "missing " + label + " list")
    result = {}
    for row in rows:
        _require(isinstance(row, dict) and isinstance(row.get(key), str) and row[key],
                 "invalid " + label + " identity")
        _require(row[key] not in result, "duplicate " + label + ": " + row[key])
        result[row[key]] = row
    return result


def _texture_inputs(root, scene):
    """Check both packaged PNGs and the extraction inventory used by Textures."""
    local = _read(_local(root, "textures/source-textures.json"))
    _require(local.get("status") == "passed", "texture preparation did not pass")
    packaged = _indexed(local.get("exported"), "packaged textures", "name")
    extraction = Path(scene["extraction_root"]).resolve(strict=True)
    inventories = sorted(extraction.glob("textures-*/result.json"))
    _require(bool(inventories), "extracted texture inventory is absent")
    inventory_path = inventories[-1]
    external_report = _read(inventory_path)
    _require(external_report.get("status") == "passed", "extracted texture inventory did not pass")
    external = _indexed(external_report.get("exported"), "extracted textures", "name")
    required = {name.split("*")[0].split("$")[0] for material in scene["materials"]
                for name in material["texture_slots"] if name}
    _require(required <= packaged.keys() and required <= external.keys(),
             "an original material texture is absent")
    hashes = {}
    for name, entry in packaged.items():
        _require(name in external and entry["sha256"] == external[name]["sha256"],
                 "packaged and extracted texture identities differ: " + name)
        hashes[entry["relative_file"]] = _check_hash(
            _local(root, entry["relative_file"]), entry["sha256"])
        original = Path(external[name]["file"]).resolve(strict=True)
        _require(original.is_file() and original.suffix.lower() == ".png",
                 "original decoded texture is unavailable: " + name)
        _check_hash(original, entry["sha256"])
    return hashes, dict(file=str(inventory_path), sha256=_digest(inventory_path))


def preflight(package, source):
    """Validate a present Flanders supplement before replacing the saved World.

    Other maps and a completely absent supplement return None. An existing but
    incomplete/corrupt supplement raises before the caller creates a blank map.
    """
    if source.get("id") != MAP_ID:
        return None
    root = Path(package) / "mission-objects"
    if not root.exists() and not root.is_symlink():
        return None
    _require(root.is_dir(), "mission-objects exists but is not a directory")
    root = root.resolve(strict=True)
    scene_path = _local(root, "scene-source.json")
    mission_path = _local(root, "mission-source.json")
    verification_path = _local(root, "preparation-verification.json")
    provenance = _read(_local(root, "source-provenance.json"))
    scene, mission, verification = map(_read, (scene_path, mission_path, verification_path))
    _require(provenance.get("schema") == "wt_original_zeppelin_provenance_v1" and
             provenance.get("status") == "offline_preparation_verified",
             "original-source provenance is unverified")
    scene_hash = _check_hash(scene_path, provenance.get("scene_manifest_sha256"))
    mission_hash = _check_hash(mission_path, provenance.get("source_manifest_sha256"))
    verification_hash = _check_hash(
        verification_path, provenance.get("preparation_verification_sha256"))
    _require(scene.get("schema") == "rm_scene_map_source_v1" and
             scene.get("id") == SUPPLEMENT_ID and scene.get("damage_policy") == "fixed_landmark" and
             scene.get("source_manifest") == "mission-source.json" and
             scene.get("instances") == "instances.json",
             "scene identity or fixed-landmark contract changed")
    _require(mission.get("schema") == "rm_wt_mission_objects_v1" and
             mission.get("id") == SUPPLEMENT_ID and mission.get("source_level") == "avg_vlaanderen",
             "original mission identity changed")
    _require(verification.get("status") == "passed" and
             verification.get("scene_manifest_sha256") == scene_hash,
             "offline preparation verification is stale or failed")

    visual = _indexed(scene.get("meshes"), "visual meshes")
    collision = _indexed(scene.get("collision_meshes"), "collision meshes")
    originals = _indexed(mission.get("instances"), "source instances")
    bindings_path = _local(root, scene["instances"])
    bindings = _indexed(_read(bindings_path).get("parts"), "instance bindings")
    _require(bool(visual) and bool(collision) and set(bindings) == set(visual),
             "visual/collision meshes or instance bindings are incomplete")
    geometry_hashes = {}
    for spec in [*visual.values(), *collision.values()]:
        _require(re.fullmatch(r"[A-Za-z0-9_]+", spec["id"]), "invalid prepared mesh id")
        _require(spec["file"] not in geometry_hashes, "duplicate geometry file: " + spec["file"])
        geometry_hashes[spec["file"]] = _check_hash(_local(root, spec["file"]), spec.get("sha256"))

    instance_hashes, seen, used_collision = {}, set(), set()
    colliding_instances = 0
    for identity, spec in visual.items():
        binding = bindings[identity]
        col_id = "col_" + identity
        expected_collision = FOLDER + "/Collision/" + col_id + "." + col_id if col_id in collision else ""
        _require(binding.get("visual_asset") == FOLDER + "/Meshes/" + identity + "." + identity and
                 binding.get("collision_asset", "") == expected_collision and
                 binding.get("instances_file") == spec["instances_file"] and
                 binding.get("damage_policy") == spec.get("damage_policy") == "fixed_landmark",
                 "instance asset binding changed: " + identity)
        ids = spec.get("source_instances")
        _require(isinstance(ids, list) and len(ids) == spec.get("instance_count") and bool(ids),
                 "instance count changed: " + identity)
        expected = bytearray(b"RMI1" + struct.pack("<I", len(ids)))
        for source_id in ids:
            _require(source_id in originals and source_id not in seen,
                     "missing/duplicate original instance: " + str(source_id))
            original = originals[source_id]
            _require(original.get("prepared_mesh_id") == identity and
                     original.get("resource") == spec["source"], "source instance mesh changed: " + source_id)
            position = original.get("position_ros_m")
            _require(isinstance(position, list) and len(position) == 3 and
                     all(isinstance(v, (int, float)) and math.isfinite(v) for v in position),
                     "invalid source position: " + source_id)
            expected.extend(struct.pack("<10f", *position, 0, 0, 0, 1, 1, 1, 1))
            seen.add(source_id)
        instance_hashes[spec["instances_file"]] = _check_hash(
            _local(root, spec["instances_file"]), hashlib.sha256(expected).hexdigest())
        if expected_collision:
            used_collision.add(col_id)
            colliding_instances += len(ids)
    _require(seen == set(originals) and used_collision == set(collision),
             "original mission instance or collision coverage changed")
    _require(len(originals) == scene["source_coverage"]["rendinst_instances"] ==
             verification.get("original_instances") and
             len(visual) == verification.get("visual_assets") and
             len(collision) == verification.get("collision_assets"),
             "verified preparation counts changed")

    probes_path = _local(root, "collision-probes.json")
    probes = _read(probes_path)
    _require(probes.get("schema") == "wt_mission_collision_probes_v1" and
             probes.get("source_manifest_sha256") == mission_hash and
             bool(probes.get("hits")) and bool(probes.get("misses")),
             "native collision probe source is missing or stale")
    texture_hashes, texture_inventory = _texture_inputs(root, scene)
    _require(len(texture_hashes) == verification.get("textures"), "verified texture count changed")
    return dict(schema="wt_mission_supplement_preflight_v1", status="passed", package=str(root),
                source_manifest_sha256=mission_hash, scene_manifest_sha256=scene_hash,
                preparation_verification_sha256=verification_hash,
                instances_manifest_sha256=_digest(bindings_path), collision_probes_sha256=_digest(probes_path),
                visual_assets=len(visual), collision_assets=len(collision),
                instances=len(originals), colliding_instances=colliding_instances,
                geometry_file_sha256=geometry_hashes, instance_file_sha256=instance_hashes,
                texture_file_sha256=texture_hashes, extraction_texture_inventory=texture_inventory)


def restore(package, source, evidence, report):
    """Restore owned actors after the base World is saved; update its final hash.

    The verified author replaces only RMSim.WTMissionObjects.Zeppelin actors,
    preserves all other actors, saves/reloads the World, and checks native
    geometry, materials, poses, collision and an original frame hit/opening miss.
    """
    checked = preflight(package, source)
    if checked is None:
        return None
    destination = Path(evidence).resolve() / "mission-objects"
    suffix = 1
    while destination.exists():
        destination = Path(evidence).resolve() / ("mission-objects-" + str(suffix).zfill(2))
        suffix += 1
    destination.mkdir(parents=True, exist_ok=False)
    # Lazy import keeps preflight usable without Unreal and avoids import cycles.
    from author_wt_mission_objects import author
    author(Namespace(package=checked["package"], evidence=str(destination), verify_only=False))
    native_path = destination / "native-authoring.json"
    native = _read(native_path)
    _require(native.get("schema") == "wt_mission_authoring_v1" and
             native.get("status") == "passed" and native.get("world") == WORLD and
             native.get("saved_world_reloaded") is True and
             native.get("native_collision_probes", {}).get("status") == "passed",
             "native restore did not pass saved-world verification: " + str(native_path))
    for key in ("source_manifest_sha256", "scene_manifest_sha256"):
        _require(native.get(key) == checked[key], "restored source identity changed: " + key)
    _require(native.get("native_readback", {}).get("visual_instances") == checked["instances"] and
             native.get("native_readback", {}).get("collision_instances") == checked["colliding_instances"],
             "restored native instance coverage changed")
    world_file = Path(__file__).resolve().parents[2] / (
        "unreal/RMSim/Content/RMSim/Maps/" + MAP_ID + "/World.umap")
    world_hash = _check_hash(world_file, native.get("world_file_sha256"))
    native["preflight"] = checked
    native["native_report"] = str(native_path)
    native["native_report_sha256"] = _digest(native_path)
    report["mission_objects"] = native
    report["map_file_sha256"] = world_hash
    return native
