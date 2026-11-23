"""Recover original four-component facing-leaf payloads without changing source assets.

Run with CPython and the existing patched DAE checkout. The decoder is observed in
memory: its established position/UV/normal interpretation remains unchanged.
"""
import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import numpy as np
import dagor_vertex
from dagor_codec import configure_oodle

MODELS = {
    "kh_gol_euraspen_fall_03_rendinst": (153, 104, 31, 593),
    "kh_gol_liveoak_03_rendinst": (166, 84, 40, 1171),
    "kh_gol_tree_simple_a": (98, 64, 20, 605),
}
LEAF_DECLARATION = ((16, 0, 0, 0), (6, 3, 0, 0), (16, 3, 1, 0), (4, 1, 0, 0))


def sha(path):
    return hashlib.file_digest(path.open("rb"), "sha256").hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--dae-root", type=Path, required=True)
    parser.add_argument("--game-root", type=Path, required=True)
    parser.add_argument("--oodle-library", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    configure_oodle(args.oodle_library)
    captures = []
    original = dagor_vertex.decode_vertices

    def capture(raw, declarations, stride):
        result = original(raw, declarations, stride)
        position = np.asarray(result[0], dtype=np.float32)
        count = len(position)
        declaration = tuple((int(t) >> 16, int(u), int(i), int(s)) for t, u, i, s in declarations)
        delta = np.zeros((count, 4), dtype=np.float32)
        packed_normal = np.zeros((count, 4), dtype=np.uint8)
        offset = 0
        channels = []
        is_leaf = declaration == LEAF_DECLARATION
        for type_value, usage, index, stream in declarations:
            kind = int(type_value) >> 16
            dtype, n = dagor_vertex.TYPES[kind]
            item = np.dtype(dtype).itemsize
            values = np.ndarray((count, n), dtype=dtype, buffer=raw,
                                offset=offset, strides=(stride, item)).copy()
            channels.append(dict(type_value=int(type_value), type=kind, usage=int(usage),
                                 index=int(index), stream=int(stream), offset=offset,
                                 components=n, dtype=dtype))
            if is_leaf and (usage, index) == (3, 1):
                delta[:] = values
            if (usage, index, kind) == (1, 0, 4):
                packed_normal[:] = values
            offset += n * item
        if offset != stride or (is_leaf and stride != 24):
            raise ValueError("typed declaration / stride disagreement")
        captures.append(dict(positions=position, delta=delta, packed_normal=packed_normal,
                             leaf_mask=np.full(count, is_leaf, dtype=bool), raw=bytes(raw),
                             evidence=dict(stride=stride, vertices=count, leaf=is_leaf,
                                           channels=channels,
                                           raw_sha256=hashlib.sha256(raw).hexdigest())))
        return result

    dagor_vertex.decode_vertices = capture
    sys.path.insert(0, str(args.dae_root.resolve()))
    with contextlib.redirect_stdout(io.StringIO()):
        from parse.gameres import GameResourcePack, GameResDesc
        from util.assetcacher import AssetCacher
        import util.log as log
        log.log = lambda *a, **k: None
        desc = GameResDesc(str(args.game_root / "content/base/res/riDesc.bin"))
        desc.loadDataBlock()
        AssetCacher.appendGameResDesc(desc)
    records = []
    groups = {}
    source_hashes = {}
    for name, expected in MODELS.items():
        source = args.source_root / "models-all-07" / name
        metadata = read(source / "model.json")
        if sha(source / "mesh.npz") != metadata["mesh_sha256"]:
            raise ValueError("original NPZ hash differs: " + name)
        group_path = Path(metadata["source"]["group"])
        if str(group_path) not in groups:
            groups[str(group_path)] = GameResourcePack(str(group_path))
            source_hashes[str(group_path)] = sha(group_path)
        captures.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            resource = groups[str(group_path)].getRealResource(metadata["source"]["resource_id"])
            model = resource.getModel(0)
        with np.load(source / "mesh.npz") as archive:
            original_data = {key: archive[key].copy() for key in archive.files}
        position = np.concatenate([c["positions"] for c in captures])
        final_position = np.asarray([model.getVertex(i) for i in range(model.vertCnt)], dtype=np.float32)
        if not np.array_equal(position, original_data["vertices"]) or not np.array_equal(position, final_position):
            raise ValueError("raw declaration order/position differs from original NPZ: " + name)
        if not np.array_equal(np.asarray([model.getUV(i) for i in range(model.vertCnt)], dtype=np.float32),
                              original_data["uv0"]):
            raise ValueError("original UV0 mismatch: " + name)
        delta = np.concatenate([c["delta"] for c in captures])
        packed_normal = np.concatenate([c["packed_normal"] for c in captures])
        leaf_mask = np.concatenate([c["leaf_mask"] for c in captures])
        if not np.all(np.isfinite(delta)) or set(np.unique(delta[leaf_mask, 3])) != {0., 3., 6., 9.}:
            raise ValueError("invalid source leaf payload")
        legacy_xy = delta[leaf_mask, :2].copy()
        legacy_xy[:, 1] = 1 - legacy_xy[:, 1]
        if not np.array_equal(legacy_xy, original_data["uv1"][leaf_mask]):
            raise ValueError("raw delta XY does not reproduce preserved original UV1")
        faces = original_data["triangles"]
        leaf_materials = [i for i in metadata["used_material_indices"]
                          if metadata["materials"][i]["shader"] == "rendinst_facing_leaves"]
        leaf_faces = np.isin(original_data["material_ids"], leaf_materials)
        if not np.array_equal(np.all(leaf_mask[faces], axis=1), leaf_faces):
            raise ValueError("leaf typed vertices / source material disagreement")
        if np.any(leaf_mask[faces[~leaf_faces]]):
            raise ValueError("source trunk uses leaf payload vertices")
        centers, center_index, multiplicity = np.unique(position[leaf_mask], axis=0,
                                                       return_inverse=True, return_counts=True)
        group_id = np.full(len(position), -1, dtype=np.int32)
        group_id[leaf_mask] = center_index
        if not np.all(multiplicity == 4) or not np.all(np.ptp(group_id[faces[leaf_faces]], axis=1) == 0):
            raise ValueError("source leaves are not four vertices per center")
        if not np.all(np.bincount(group_id[faces[leaf_faces, 0]], minlength=len(centers)) == 2):
            raise ValueError("source leaves are not two triangles per card")
        vertices, triangles, quads, instances = expected
        if (len(position), len(faces), len(centers), int(leaf_faces.sum())) != (vertices, triangles, quads, quads * 2):
            raise ValueError("source count mismatch: " + name)
        original_area = np.linalg.norm(np.cross(position[faces[leaf_faces, 1]] - position[faces[leaf_faces, 0]],
                                                position[faces[leaf_faces, 2]] - position[faces[leaf_faces, 0]]), axis=1)
        if np.any(original_area != 0):
            raise ValueError("unexpected non-pivot leaf geometry")
        payload_path = output / (name + ".npz")
        arrays = dict(positions=position, delta_xyzw=delta, packed_normal_bgra=packed_normal,
                      leaf_mask=leaf_mask, leaf_face_mask=leaf_faces, center_id=group_id)
        arrays.update({"raw_draw_" + str(i): np.frombuffer(c["raw"], dtype=np.uint8)
                       for i, c in enumerate(captures)})
        np.savez_compressed(payload_path, **arrays)
        records.append(dict(name=name, source=metadata["source"],
                            source_model_json=str(source / "model.json"),
                            source_model_json_sha256=sha(source / "model.json"),
                            source_mesh_npz=str(source / "mesh.npz"), source_mesh_sha256=sha(source / "mesh.npz"),
                            vertices=vertices, source_triangles=triangles, leaf_vertices=int(leaf_mask.sum()),
                            leaf_quads=quads, leaf_triangles=quads * 2, original_instances=instances,
                            original_npz_position_error_m=0., original_uv0_exact=True, original_uv1_xy_exact=True,
                            source_pivot_bounds_dagor_m=metadata["bounds_dagor_m"],
                            source_declared_bounds_dagor_m=metadata["declared_bounds_dagor_m"],
                            raw_delta_lo=delta[leaf_mask].min(axis=0).tolist(),
                            raw_delta_hi=delta[leaf_mask].max(axis=0).tolist(),
                            wind_numbers=sorted(map(int, np.unique(delta[leaf_mask, 3]))),
                            draws=[c["evidence"] for c in captures], payload_file=payload_path.name,
                            payload_sha256=sha(payload_path)))
    report = dict(status="passed", scope="read-only raw four-component leaf payload recovery; no UE rendering",
                  source_root=str(args.source_root.resolve()), source_groups=source_hashes,
                  source_level_sha256="427c9b06b62af0635fa08f2d6238211b9850caf7717a0773f186e1835be63b8f",
                  dae_commit="1f0d1e04ded04bf9b28e8d5be93cb79714f505d3",
                  source_decoders={str(p): sha(p) for p in
                                   (Path(dagor_vertex.__file__), args.dae_root / "parse/realres.py")},
                  total_leaf_quads=sum(m["leaf_quads"] for m in records),
                  total_leaf_triangles=sum(m["leaf_triangles"] for m in records),
                  total_original_instances=sum(m["original_instances"] for m in records), models=records)
    save(output / "raw-payload-verification.json", report)
    print("WT_LEAF_PAYLOAD_VERIFIED", json.dumps({k: report[k] for k in
          ("status", "total_leaf_quads", "total_leaf_triangles", "total_original_instances")}), flush=True)


if __name__ == "__main__":
    main()
