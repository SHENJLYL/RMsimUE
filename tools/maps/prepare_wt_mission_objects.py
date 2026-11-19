"""Restore the original Flanders mission's crashed zeppelin as a separate scene supplement.

Extraction reads original VROMFS/GRP/DDSx files. Blender preparation reuses the map's
LOD0, normal, UV, material-slot and FBX exporter. Complete affine linear transforms
are baked in model space; only the original translation is serialized to RMI1.
No original game, existing map package, Unreal asset, or running process is changed.
"""
import argparse
from collections import Counter, defaultdict
import contextlib
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
PREFIX = "us_zeppelin_whale_"
CHAIN = [
    "gamedata/missions/cta/tanks/vlaanderen/vlaanderen_dom.blk",
    "gamedata/missions/cta/tanks/vlaanderen/objects/template_vlaanderen_objects.blk",
    "gamedata/missions/cta/tanks/vlaanderen/objects/template_vlaanderen_object_groups.blk",
]
CONVERSION = np.array([[1., 0., 0.], [0., 0., -1.], [0., 1., 0.]])
IDENTITY = [1., 0., 0., 0., 1., 0., 0., 0., 1., 0., 0., 0.]
SCHEMA = "rm_wt_mission_objects_v1"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def params(block):
    return {p["name"]: p["value"] for p in block["params"]}


def walk(block):
    yield block
    for child in block["children"]:
        yield from walk(child)


def tm(value):
    """Dagor TMatrix stores four Point3 columns: basis X/Y/Z, then translation."""
    values = np.asarray(value, dtype=np.float64)
    if values.shape != (12,) or not np.all(np.isfinite(values)):
        raise ValueError("Invalid original TMatrix")
    result = np.eye(4)
    result[:3, :] = values.reshape(4, 3).T
    if abs(np.linalg.det(result[:3, :3])) < 1e-9:
        raise ValueError("Singular original TMatrix")
    return result


def shared_names_lossless(files):
    """Preserve unrelated non-UTF8 archive names without altering the BLK reader."""
    from wt_blk import Reader, bounded_zstd, MAX_DECODED_BYTES
    raw = files.get("\xff?nm")
    if raw is None:
        return [], None, {}
    if len(raw) < 40:
        raise ValueError("Shared name-map header truncated")
    data = bounded_zstd(raw[40:], MAX_DECODED_BYTES)
    reader = Reader(data)
    count = reader.varuint()
    if count:
        size = reader.varuint()
        if size > MAX_DECODED_BYTES or count > size:
            raise ValueError("Invalid shared name-map size")
        names_raw = reader.take(size)
        if not names_raw.endswith(b"\0"):
            raise ValueError("Unterminated shared name-map")
        names = names_raw[:-1].decode("utf-8", errors="surrogateescape").split("\0")
        if len(names) != count:
            raise ValueError("Shared name-map count differs")
    else:
        names = []
    if reader.pos != len(data):
        raise ValueError("Trailing shared name-map data")
    key = raw[8:40].hex() + ".dict"
    info = dict(shared_names=len(names), dictionary_entry=key if key in files else None,
                lossless_non_utf8_names=sum(any(0xDC80 <= ord(c) <= 0xDCFF for c in n) for n in names))
    return names, files.get(key), info


def extract(args):
    from wt_vromfs import read_vromfs
    from wt_blk import decode_blk
    game = Path(args.game_root).resolve(strict=True)
    work = Path(args.work).resolve()
    if work == game or game in work.parents:
        raise ValueError("Output must be outside the read-only source game")
    work.mkdir(parents=True, exist_ok=False)
    archives = {}
    decoded = {}
    source_records = []
    for name in ("mis.vromfs.bin", "aces.vromfs.bin"):
        files, info = read_vromfs(game / name)
        names, dictionary, context = shared_names_lossless(files)
        archives[name] = (files, names, dictionary)
        source_records.append(dict(file=str(game / name), sha256=info["sha256"],
                                   bytes=(game / name).stat().st_size, namemap=context))

    def decode(archive, entry):
        key = archive + ":" + entry
        if key not in decoded:
            files, names, dictionary = archives[archive]
            block, meta = decode_blk(files[entry], names, dictionary)
            # Selected source fields must remain exact, readable ASCII/Unicode.
            json.dumps(block, ensure_ascii=False).encode("utf-8")
            decoded[key] = block
            source_records.append(dict(archive=archive, entry=entry,
                                       packed_sha256=hashlib.sha256(files[entry]).hexdigest(),
                                       decoded_sha256=hashlib.sha256(json.dumps(block, sort_keys=True).encode()).hexdigest(),
                                       metadata=meta))
            save(work / "decoded" / archive / (entry + ".json"), block)
        return decoded[key]

    chain_blocks = [decode("mis.vromfs.bin", entry) for entry in CHAIN]
    chain_links = []
    for index in range(len(CHAIN) - 1):
        wanted = Path(CHAIN[index + 1]).stem
        links = [str(p["value"]) for b in walk(chain_blocks[index]) for p in b["params"]
                 if wanted in str(p["value"])]
        if not links:
            raise ValueError("Original mission template chain changed: " + wanted)
        chain_links.append(dict(source=CHAIN[index], target=CHAIN[index + 1], references=links))
    groups = []
    instances = []
    rejected = []
    template = chain_blocks[-1]
    for block_index, container in enumerate(template["children"]):
        if container["name"] != "units":
            continue
        for unit_index, unit in enumerate(container["children"]):
            values = params(unit)
            identity = values.get("unit_class", "")
            source_path = f"/children/{block_index}/children/{unit_index}"
            if PREFIX not in identity:
                if PREFIX in values.get("name", ""):
                    rejected.append(dict(source_path=source_path, instance_name=values["name"],
                                         unit_class=identity, tm=values.get("tm"),
                                         reason="Instance label mentions zeppelin, actual object group is unrelated"))
                continue
            if unit["name"] != "objectGroups" or not identity.startswith(PREFIX):
                raise ValueError("Unexpected zeppelin mission unit type")
            parent = tm(values["tm"])
            group_entry = "gamedata/objectgroups/" + identity + ".blk"
            group = decode("aces.vromfs.bin", group_entry)
            group_class = params(group).get("className", params(group).get("classname"))
            # The source rear cabin uses the legacy lowercase "$%composit" spelling.
            if group_class not in ("composit", "$%composit"):
                raise ValueError("Expected original composite object group: " + str(group_class))
            group_record = dict(instance_name=values["name"], group=identity, source_path=source_path,
                                mission_tm=values["tm"], object_layer=values.get("objLayer"),
                                original_props=[params(b) for b in unit["children"] if b["name"] == "props"])
            groups.append(group_record)
            for node_index, node in enumerate(group["children"]):
                child = params(node)
                if node["name"] != "node" or child.get("type") != "rendInst" or node["children"]:
                    raise ValueError("Unsupported original composite hierarchy")
                if child.get("toGround", False):
                    raise ValueError("Terrain-snapped original node requires an explicit source terrain resolver")
                resource = child["name"]
                if not resource.startswith("%" + PREFIX):
                    raise ValueError("Unexpected original zeppelin child resource: " + resource)
                resource = resource[1:]
                local_values = child.get("tm", IDENTITY)
                local = tm(local_values)
                world = parent @ local
                # Verify composition independently by applying two stages to basis points.
                probes = np.vstack([np.eye(3), np.zeros(3)])
                sequential = (probes @ local[:3, :3].T + local[:3, 3]) @ parent[:3, :3].T + parent[:3, 3]
                composed = probes @ world[:3, :3].T + world[:3, 3]
                if np.max(np.abs(sequential - composed)) > 1e-9:
                    raise ValueError("Original transform composition failed")
                instances.append(dict(id=f"zeppelin_{unit_index:03d}_{node_index:02d}",
                                      resource=resource, mission_instance=values["name"], group=identity,
                                      mission_source_path=source_path, group_node_index=node_index,
                                      parent_tm=values["tm"], child_tm=local_values,
                                      world_matrix_dagor_3x4=world[:3, :].tolist(),
                                      position_ros_m=(CONVERSION @ world[:3, 3]).tolist(),
                                      to_ground=child.get("toGround", False)))
    if not instances:
        raise ValueError("Original mission contains no zeppelin")
    names = sorted({i["resource"] for i in instances})
    sought = set(names) | {n + "_collision" for n in names}
    sys.path.insert(0, str(REPO / "third_party/dagor_asset_explorer/src/dae"))
    from parse.gameres import GameResourcePack
    from parse.realres import RendInst
    import util.log as log
    log.log = lambda *a, **k: None
    matches = defaultdict(list)
    failures = []
    group_paths = sorted((game / "content/base/res").glob("*.grp"))
    with (work / "resource-scan.log").open("w", encoding="utf-8") as logfile, contextlib.redirect_stdout(logfile):
        for path in group_paths:
            try:
                pack = GameResourcePack(str(path))
                for number in range(pack.getRealResEntryCnt()):
                    entry = pack.getRealResEntry(number)
                    if entry.getName() in sought:
                        matches[entry.getName()].append(dict(group=str(path), resource_id=number,
                                                            class_id=entry._RealResEntry__classId,
                                                            offset=entry._RealResEntry__offset))
            except Exception as exc:
                failures.append(dict(file=str(path), error=str(exc)))
    if failures:
        save(work / "resource-scan-failures.json", failures)
        raise ValueError("Source resource inventory was incomplete")
    resource_index = {}
    collision_index = {}
    for name in names:
        visual = [m for m in matches[name] if m["class_id"] == RendInst.staticClassId]
        if len(visual) != 1:
            raise ValueError("Original LOD0 resource is ambiguous or missing: " + name)
        resource_index[name] = dict(matches=visual)
        collisions = matches.get(name + "_collision", [])
        if len(collisions) > 1:
            raise ValueError("Original collision resource is ambiguous: " + name)
        if collisions:
            if collisions[0]["class_id"] != 2900688896:
                raise ValueError("Unexpected original collision class")
            collision_index[name] = collisions[0]
    save(work / "resource-index.json", dict(resources=resource_index))
    save(work / "collision-index.json", collision_index)
    for path in sorted({m["group"] for v in resource_index.values() for m in v["matches"]}):
        source_records.append(dict(file=path, bytes=Path(path).stat().st_size, sha256=sha(path)))
    source_records.append(dict(file=str(game / "content/base/res/riDesc.bin"),
                               sha256=sha(game / "content/base/res/riDesc.bin")))
    manifest = dict(schema=SCHEMA, id="wt_vlaanderen_zeppelin", source_game_root=str(game),
                    source_level="avg_vlaanderen", original_mission_chain=CHAIN, chain_links=chain_links,
                    source_files=source_records, matrix_convention="Dagor TMatrix: four Point3 columns; parent @ child",
                    source_to_ros_matrix=CONVERSION.tolist(), groups=groups, instances=instances,
                    excluded_misnamed_units=rejected, resources=names,
                    original_collision_resources=sorted(collision_index),
                    no_original_collision_resource=sorted(set(names) - set(collision_index)),
                    counts=dict(mission_groups=len(groups), rendinst_instances=len(instances), resources=len(names),
                                collision_resources=len(collision_index), scanned_grp_files=len(group_paths)))
    save(work / "mission-source.json", manifest)
    print("MISSION_INDEXED", json.dumps(manifest["counts"]), flush=True)
    history = []

    def run(tool, values):
        command = [sys.executable, str(REPO / "tools/maps" / tool)] + values
        result = subprocess.run(command, text=True, encoding="utf-8", errors="replace",
                                capture_output=True, cwd=REPO)
        target = work / (Path(tool).stem + ".log")
        target.write_text(result.stdout + result.stderr, encoding="utf-8")
        history.append(dict(command=command, exit_code=result.returncode, log=str(target)))
        save(work / "commands.json", history)
        if result.returncode:
            raise RuntimeError(tool + " failed; see " + str(target))
        print(tool, "passed", flush=True)

    run("export_wt_models.py", ["--index", str(work / "resource-index.json"),
        "--game-root", str(game), "--output", str(work / "models-all-07"),
        "--oodle-library", args.oodle_library])
    run("extract_wt_collisions.py", ["--index", str(work / "collision-index.json"),
        "--output", str(work / "collision-original"), "--oodle-library", args.oodle_library])
    slots = set()
    for name in names:
        model = read(work / "models-all-07" / name / "model.json")
        for index in model["used_material_indices"]:
            slots.update(t for t in model["materials"][index]["texture_slots"] if t)
    save(work / "required-textures.json", sorted(slots))
    run("export_wt_textures.py", ["--index", str(work / "resource-index.json"),
        "--game-root", str(game), "--output", str(work / "textures-01"),
        "--level", "mission_zeppelin_only", "--extra-textures", str(work / "required-textures.json"),
        "--oodle-library", args.oodle_library])
    texture_result = read(work / "textures-01/result.json")
    for path in sorted({v["source"] for v in texture_result["exported"]}):
        manifest["source_files"].append(dict(file=path, bytes=Path(path).stat().st_size, sha256=sha(path)))
    manifest["extraction"] = dict(models=read(work / "models-all-07/result.json"),
                                 collisions=read(work / "collision-original/result.json"),
                                 textures={k: v for k, v in texture_result.items() if k != "exported"})
    save(work / "mission-source.json", manifest)
    print("MISSION_EXTRACTED", json.dumps(manifest["counts"]), flush=True)


def prepare(args):
    import bpy
    from prepare_wt_scene import Builder, instances_file, bounds
    work = Path(args.work).resolve(strict=True)
    output = Path(args.output).resolve()
    source = read(work / "mission-source.json")
    if source["schema"] != SCHEMA or "extraction" not in source:
        raise ValueError("Complete mission extraction is required")
    game = Path(source["source_game_root"]).resolve()
    if output == game or game in output.parents:
        raise ValueError("Output must be outside read-only source game")
    if output.exists() and any(output.iterdir()) and not (output / "parts-journal.jsonl").exists():
        raise ValueError("Output is not an existing mission preparation")
    output.mkdir(parents=True, exist_ok=True)
    for folder in ("meshes", "collision", "instances", "textures"):
        (output / folder).mkdir(exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.unit_settings.system = "METRIC"
    bpy.context.scene.unit_settings.scale_length = 1
    # Reuse the proven source export methods without loading the unrelated base map.
    builder = Builder.__new__(Builder)
    builder.root = work
    builder.output = output
    builder.materials = {}
    builder.parts = []
    builder.instanced = []
    builder.collisions = []
    builder.bounds = []
    builder.data_cache = {}
    builder.audit = Counter()
    builder.known = {}
    builder.journal = output / "parts-journal.jsonl"
    if builder.journal.exists():
        for line in builder.journal.read_text(encoding="utf-8").splitlines():
            part = json.loads(line)
            builder.known[part["id"]] = part
    builder.model_metadata = {name: read(work / "models-all-07" / name / "model.json") for name in source["resources"]}
    clusters = defaultdict(list)
    for instance in source["instances"]:
        world = np.asarray(instance["world_matrix_dagor_3x4"], dtype=np.float64)
        # Full affine bake, keyed by exact double bytes; no yaw/TRS rounding.
        key = (instance["resource"], world[:, :3].astype("<f8").tobytes())
        clusters[key].append(instance)
    folder = "/Game/RMSim/Maps/wt_vlaanderen/MissionObjects"
    collision_material = "WT_SourceCollision"
    bpy.data.materials.new(collision_material)
    bindings = []
    all_world_bounds = []
    collision_empty = []
    for (name, linear_bytes), members in sorted(clusters.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        linear = np.frombuffer(linear_bytes, dtype="<f8").reshape(3, 3)
        identifier = "zep_" + name.removeprefix(PREFIX) + "_" + hashlib.sha256(linear_bytes).hexdigest()[:8]
        data = builder.model(name)
        slots, remap = builder.source_slots(builder.model_metadata[name], data)
        converted = builder.converted(data, remap, linear, np.zeros(3))
        part = builder.mesh(identifier, converted, slots)
        rows = [[*member["position_ros_m"], 0, 0, 0, 1, 1, 1, 1] for member in members]
        relative_instances = "instances/" + identifier + ".rmi"
        instances_file(output / relative_instances, rows)
        collision_asset = ""
        collision_file = work / "collision-original" / name / "collision.npz"
        policy = "no_original_collision_resource"
        if collision_file.exists():
            with np.load(collision_file) as original:
                collision = {k: original[k].copy() for k in ("vertices", "triangles")}
            if len(collision["triangles"]):
                collision["material_ids"] = np.zeros(len(collision["triangles"]), dtype=np.int32)
                coldata = builder.converted(collision, {0: 0}, linear, np.zeros(3))
                colpart = builder.mesh("col_" + identifier, coldata, [collision_material], collision=True)
                colpart.update(source=name, policy="fixed_original_PHYS_COLLIDABLE")
                builder.collisions.append(colpart)
                collision_asset = f"{folder}/Collision/{colpart['id']}.{colpart['id']}"
                policy = "fixed_original_PHYS_COLLIDABLE"
            else:
                collision_empty.append(name)
                policy = "original_no_active_PHYS_COLLIDABLE"
        part = dict(part, instances_file=relative_instances, instance_count=len(rows),
                    collision_policy=policy, source=name, source_instances=[m["id"] for m in members],
                    full_affine_linear_dagor=linear.tolist(), damage_policy="fixed_landmark")
        builder.parts.append(part)
        bindings.append(dict(id=identifier, visual_asset=f"{folder}/Meshes/{identifier}.{identifier}",
                             collision_asset=collision_asset, instances_file=relative_instances,
                             damage_policy="fixed_landmark"))
        for member in members:
            member["prepared_mesh_id"] = identifier
            world = np.asarray(member["world_matrix_dagor_3x4"], dtype=np.float64)
            points = data["vertices"] @ (CONVERSION @ world[:, :3]).T + CONVERSION @ world[:, 3]
            actual = converted["vertices"] + np.array(member["position_ros_m"])
            if np.max(np.abs(points - actual)) > 1e-8:
                raise ValueError("Affine source placement changed")
            all_world_bounds.append(bounds(points))
    texture_result = read(work / "textures-01/result.json")
    texture_entries = []
    for item in texture_result["exported"]:
        original = Path(item["file"])
        target = output / "textures" / original.name
        if not target.exists() or sha(target) != item["sha256"]:
            shutil.copyfile(original, target)
        texture_entries.append(dict(item, file=str(target), relative_file="textures/" + target.name))
    save(output / "textures/source-textures.json", dict(texture_result, exported=texture_entries))
    world_bounds = dict(lo=np.min([b["lo"] for b in all_world_bounds], axis=0).tolist(),
                        hi=np.max([b["hi"] for b in all_world_bounds], axis=0).tolist())
    manifest = dict(schema="rm_scene_map_source_v1", id="wt_vlaanderen_zeppelin",
                    display_name="Flanders original crashed zeppelin",
                    coordinate_system="ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP", units="m",
                    extraction_root=str(work), source_to_ros_matrix=CONVERSION.tolist(),
                    source_translation_m=[0, 0, 0], bounds_m=world_bounds,
                    materials=list(builder.materials.values()), meshes=builder.parts,
                    collision_meshes=builder.collisions, instances="instances.json",
                    source_manifest="mission-source.json", damage_policy="fixed_landmark",
                    source_coverage=dict(source["counts"], affine_baked_assets=len(builder.parts),
                                         collision_mesh_assets=len(builder.collisions),
                                         source_LOD0_triangles=sum(builder.model_metadata[i["resource"]]["triangles"] for i in source["instances"])),
                    collision_empty_resources=sorted(set(collision_empty)),
                    conversion_notes=["Original mission group and child TMatrix composition is preserved.",
                                      "Every source linear affine transform is baked exactly; RMI1 stores translation only.",
                                      "Collision uses original active PHYS_COLLIDABLE shapes; whole-object bounding boxes are not substituted.",
                                      "Landmark collision remains fixed and is excluded from vehicle destruction."])
    save(output / "scene-source.json", manifest)
    save(output / "materials.json", list(builder.materials.values()))
    save(output / "instances.json", dict(parts=bindings))
    save(output / "mission-source.json", source)
    save(output / "preparation-summary.json", dict(schema=SCHEMA, counts=manifest["source_coverage"],
         bounds_ros_m=world_bounds, material_count=len(builder.materials), textures=len(texture_entries),
         collision_empty_resources=manifest["collision_empty_resources"],
         no_original_collision_resource=source["no_original_collision_resource"],
         source_manifest_sha256=sha(output / "mission-source.json"),
         scene_manifest_sha256=sha(output / "scene-source.json")))
    prepare_collision_probes(output, REPO / "artifacts/maps/wt_existing/avg_vlaanderen")
    print("MISSION_PREPARED", json.dumps(read(output / "preparation-summary.json")), flush=True)


def prepare_collision_probes(package, base_source):
    p=Path(package);s=read(p/'mission-source.json');w=Path(read(p/'scene-source.json')['extraction_root']);base_source=Path(base_source)
    terrain=json.loads((base_source/'terrain-01/terrain.json').read_text())
    height=np.memmap(base_source/'terrain-01/height.u16',dtype='<u2',mode='r',shape=(terrain['height'],terrain['width']))
    def ground(x,z):
     u=np.clip(x/terrain['cell_m'],0,terrain['width']-1);v=np.clip(z/terrain['cell_m'],0,terrain['height']-1)
     ix=int(u);iz=int(v);jx=min(ix+1,terrain['width']-1);jz=min(iz+1,terrain['height']-1);a=u-ix;b=v-iz
     value=(1-b)*((1-a)*height[iz,ix]+a*height[iz,jx])+b*((1-a)*height[jz,ix]+a*height[jz,jx])
     return terrain['hmin_m']+float(value)/65535*terrain['hscale_m']
    points=[];owners=[];frames=[]
    for i in s['instances']:
     f=w/'collision-original'/i['resource']/'collision.npz'
     if not f.exists():continue
     with np.load(f) as d:v=d['vertices'].copy();tri=d['triangles'].copy()
     tm=np.asarray(i['world_matrix_dagor_3x4']);v=v@tm[:,:3].T+tm[:,3];world=v[tri];points.append(world);owners.extend([i]*len(world))
     if 'frame_truss_' in i['resource']:frames.append((i,v,tri))
    alltri=np.concatenate(points);lo=alltri.min(axis=1);hi=alltri.max(axis=1)
    def ray(a,b):
     low=np.minimum(a,b)-1e-6;high=np.maximum(a,b)+1e-6
     candidates=np.flatnonzero(np.all(hi>=low,axis=1)&np.all(lo<=high,axis=1))
     if not len(candidates):return None
     t=alltri[candidates];d=b-a;e1=t[:,1]-t[:,0];e2=t[:,2]-t[:,0];h=np.cross(np.broadcast_to(d,e2.shape),e2);det=np.sum(e1*h,axis=1)
     valid=np.abs(det)>1e-10;inverse=np.divide(1,det,out=np.zeros_like(det),where=valid);rel=a-t[:,0]
     u=inverse*np.sum(rel*h,axis=1);q=np.cross(rel,e1);v=inverse*np.sum(d*q,axis=1);f=inverse*np.sum(e2*q,axis=1)
     valid&=(u>=-1e-7)&(v>=-1e-7)&(u+v<=1+1e-7)&(f>=0)&(f<=1)
     if not valid.any():return None
     indices=np.flatnonzero(valid);idx=indices[np.argmin(f[indices])];return int(candidates[idx]),float(f[idx])
    def ue(p):return [float(p[0]*100),float(p[2]*100),float(p[1]*100)]
    def record(kind,i,a,b,extra):
     return dict(kind=kind,source_instance=i['id'],resource=i['resource'],prepared_mesh_id=i['prepared_mesh_id'],start_ue_cm=ue(a),end_ue_cm=ue(b),**extra)
    hits=[];misses=[]
    for i,v,f in frames:
     tr=v[f];center=tr.mean(axis=1);normal=np.cross(tr[:,1]-tr[:,0],tr[:,2]-tr[:,0]);norm=np.linalg.norm(normal,axis=1);normal/=np.maximum(norm[:,None],1e-12)
     clearance=np.array([c[1]-ground(c[0],c[2]) for c in center]);score=np.abs(clearance-.6)
     chosen=0
     for k in np.argsort(score):
      if norm[k]<.05 or abs(normal[k,1])>.65 or not .15<clearance[k]<8:continue
      c=center[k];n=normal[k];a=c+n*1.5;b=c-n*.5
      if min(a[1]-ground(a[0],a[2]),b[1]-ground(b[0],b[2]))<.08:continue
      got=ray(a,b)
      if got is None or owners[got[0]]['id']!=i['id']:continue
      hits.append(record('hard_frame_hit',i,a,b,dict(source_expected_fraction=got[1],ground_clearance_m=float(clearance[k]),impact_ue_cm=ue(a+(b-a)*got[1]))));chosen+=1
      if chosen>=4:break
     centroid=v.mean(axis=0);_,_,basis=np.linalg.svd(v-centroid,full_matrices=False);normal=basis[-1];u=basis[0];vv=basis[1]
     span=np.ptp((v-centroid)@basis.T,axis=0);selected=0
     for fu in (0,-.2,.2,-.35,.35):
      for fv in (0,.2,-.2,.35,-.35):
       c=centroid+u*span[0]*fu+vv*span[1]*fv;a=c+normal*.8;b=c-normal*.8
       if min(a[1]-ground(a[0],a[2]),b[1]-ground(b[0],b[2]))<.7:continue
       if ray(a,b) is None:
        misses.append(record('frame_opening_miss',i,a,b,dict(source_frame_pca_plane=True,ground_clearance_m=float(c[1]-ground(c[0],c[2])))));selected+=1
        if selected>=4:break
      if selected>=4:break
    if not hits or not misses:raise ValueError('Could not find source-proven hard-frame/opening probes')
    out=dict(schema='wt_mission_collision_probes_v1',source_manifest_sha256=sha(p/'mission-source.json'),coordinate_system='UE centimetres: Dagor (x,z,y)*100',source_triangle_count=len(alltri),hits=hits,misses=misses,scope='Exact original zeppelin collision triangles; native trace includes all saved-world actors and must independently pass.')
    (p/'collision-probes.json').write_text(json.dumps(out,indent=2)+'\n')
    print('MISSION_SOURCE_PROBES',json.dumps(dict(triangles=len(alltri),hits=len(hits),misses=len(misses))),flush=True)


def verify(args):
    import bpy
    from prepare_wt_scene import bounds
    output = Path(args.output).resolve(strict=True)
    source = read(output / "mission-source.json")
    manifest = read(output / "scene-source.json")
    work = Path(manifest["extraction_root"])
    expected_instances = {i["id"]: i for i in source["instances"]}
    checked = []
    maximum = 0.
    for part in manifest["meshes"] + manifest["collision_meshes"]:
        path = output / part["file"]
        if sha(path) != part["sha256"]:
            raise ValueError("Prepared FBX changed: " + part["id"])
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.fbx(filepath=str(path))
        meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
        if len(meshes) != 1:
            raise ValueError("FBX object count changed")
        obj = meshes[0]
        points = np.array([obj.matrix_world @ vertex.co for vertex in obj.data.vertices])
        obj.data.calc_loop_triangles()
        actual = bounds(points)
        error = max(float(np.abs(np.array(actual[k]) - part["bounds_m"][k]).max()) for k in ("lo", "hi"))
        if error > .003 or len(obj.data.loop_triangles) != part["triangles"]:
            raise ValueError("FBX geometry roundtrip differs: " + part["id"])
        slots = [m.name for m in obj.data.materials]
        if slots != part["material_slots"]:
            raise ValueError("FBX material slots changed: " + part["id"])
        maximum = max(maximum, error)
        record = dict(id=part["id"], triangles=len(obj.data.loop_triangles),
                      uv_layers=len(obj.data.uv_layers), bounds_error_m=error, material_slots=slots)
        if "instances_file" in part:
            data = (output / part["instances_file"]).read_bytes()
            rows = np.frombuffer(data[8:], dtype="<f4").reshape(-1, 10)
            if data[:4] != b"RMI1" or struct.unpack_from("<I", data, 4)[0] != len(rows):
                raise ValueError("Invalid serialized mission transforms")
            if len(rows) != len(part["source_instances"]):
                raise ValueError("Mission instance count changed")
            for instance_id, row in zip(part["source_instances"], rows):
                instance = expected_instances.pop(instance_id)
                error = float(np.max(np.abs(row[:3] - instance["position_ros_m"])))
                if error > .003 or not np.array_equal(row[3:], [0, 0, 0, 1, 1, 1, 1]):
                    raise ValueError("Serialized original placement changed")
                maximum = max(maximum, error)
            metadata = read(work / "models-all-07" / part["source"] / "model.json")
            expected_uv = max([0] + [int(k[2:]) for k in metadata["uv_channels"]]) + 1
            if len(obj.data.uv_layers) != expected_uv:
                raise ValueError("Original UV channels changed")
        checked.append(record)
    if expected_instances:
        raise ValueError("Source mission instances omitted")
    texture_report = read(output / "textures/source-textures.json")
    for texture in texture_report["exported"]:
        if sha(output / texture["relative_file"]) != texture["sha256"]:
            raise ValueError("Original decoded texture changed")
    report = dict(schema=SCHEMA, status="passed", scope="source extraction, full affine placement, FBX roundtrips and texture hashes",
                  original_instances=len(source["instances"]), visual_assets=len(manifest["meshes"]),
                  collision_assets=len(manifest["collision_meshes"]), textures=len(texture_report["exported"]),
                  max_geometry_or_translation_error_m=maximum, fbx_roundtrips=checked,
                  scene_manifest_sha256=sha(output / "scene-source.json"))
    save(output / "preparation-verification.json", report)
    print("MISSION_VERIFIED", json.dumps({k: v for k, v in report.items() if k != "fbx_roundtrips"}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("extract", "prepare", "verify"))
    parser.add_argument("--game-root")
    parser.add_argument("--work")
    parser.add_argument("--output")
    parser.add_argument("--oodle-library")
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    args = parser.parse_args(arguments)
    required = {"extract": ("game_root", "work", "oodle_library"),
                "prepare": ("work", "output"), "verify": ("output",)}
    for key in required[args.stage]:
        if not getattr(args, key):
            parser.error("--" + key.replace("_", "-") + " is required")
    {"extract": extract, "prepare": prepare, "verify": verify}[args.stage](args)


if __name__ == "__main__":
    main()
