"""Derive an instanced CC0 seat row with one mesh and two material primitives.

The input is the unchanged Innerscene CC0 source. All surviving geometry is
translated to a common bounding-box centre. glTF remains standard X-right,
Y-up, +Z seat-facing. OBJ fallback uses X-width, Y-rear, Z-up (faces -Y).
No source geometry is resized; concrete risers and their texture are omitted.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import struct
from pathlib import Path

SOURCE_SHA256 = "155d3ee28b2c7bd4b9fca28bddf4e6869c8cc0cbc506e2be920645c5f5b7a7ff"
SOURCE_URL = "https://www.innerscene.com/tools/library/3d-parts/stadium-seats-on-a-concrete-riser-row-of-6-000f8e6d"
COMPONENTS = {"VEC3": 3, "VEC2": 2, "SCALAR": 1}
FORMATS = {5126: "f", 5123: "H", 5125: "I"}


def prepare(source: Path, output: Path, obj_output: Path | None = None) -> dict:
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError("Source hash differs from the verified CC0 model")
    if raw[:4] != b"glTF" or struct.unpack_from("<I", raw, 4)[0] != 2:
        raise ValueError("Expected binary glTF 2")
    length, kind = struct.unpack_from("<II", raw, 12)
    assert kind == 0x4E4F534A
    src = json.loads(raw[20:20+length])
    bin_length, kind = struct.unpack_from("<II", raw, 20+length)
    assert kind == 0x004E4942
    binary = raw[28+length:28+length+bin_length]

    def values(index):
        a = src["accessors"][index]
        v = src["bufferViews"][a["bufferView"]]
        fmt = "<" + FORMATS[a["componentType"]] * COMPONENTS[a["type"]]
        stride = v.get("byteStride", struct.calcsize(fmt))
        offset = v.get("byteOffset", 0) + a.get("byteOffset", 0)
        return [struct.unpack_from(fmt, binary, offset+i*stride) for i in range(a["count"])]

    groups = [src["meshes"][i]["primitives"][0] for i in [1, 2]]
    all_positions = [p for group in groups for p in values(group["attributes"]["POSITION"])]
    lower = [min(p[k] for p in all_positions) for k in range(3)]
    upper = [max(p[k] for p in all_positions) for k in range(3)]
    center = [(lower[k]+upper[k])/2 for k in range(3)]
    dimensions = [upper[k]-lower[k] for k in range(3)]
    dst = {"asset": {"version": "2.0", "generator": "RMSim CC0 seat-row reduction"},
           "scene": 0, "scenes": [{"nodes": [0]}],
           "nodes": [{"mesh": 0, "name": "SeatRow"}],
           "meshes": [{"name": "SeatRow", "primitives": []}],
           "accessors": [], "bufferViews": [], "materials": [],
           "images": [], "textures": [], "samplers": copy.deepcopy(src["samplers"])}
    blob = bytearray()

    def view(data, target=None):
        blob.extend(b"\0" * ((-len(blob)) % 4))
        v = {"buffer": 0, "byteOffset": len(blob), "byteLength": len(data)}
        if target is not None:
            v["target"] = target
        blob.extend(data)
        dst["bufferViews"].append(v)
        return len(dst["bufferViews"])-1

    obj = ["# CC0 Innerscene derivative; metres; X width, Y rear, Z up; faces -Y", "mtllib SeatRow.mtl"]
    mtl = []
    vertex_base = 1
    for group_index, group in enumerate(groups):
        primitive = {"attributes": {}, "material": group_index}
        decoded = {}
        for key, index in list(group["attributes"].items()) + [("indices", group["indices"])]:
            a = copy.deepcopy(src["accessors"][index])
            rows = values(index)
            if key == "POSITION":
                rows = [tuple(p[k]-center[k] for k in range(3)) for p in rows]
                a["min"] = [min(p[k] for p in rows) for k in range(3)]
                a["max"] = [max(p[k] for p in rows) for k in range(3)]
            decoded[key] = rows
            fmt = "<" + FORMATS[a["componentType"]] * COMPONENTS[a["type"]]
            data = b"".join(struct.pack(fmt, *row) for row in rows)
            a["bufferView"] = view(data, 34963 if key == "indices" else 34962)
            a.pop("byteOffset", None)
            dst["accessors"].append(a)
            accessor_id = len(dst["accessors"])-1
            if key == "indices":
                primitive[key] = accessor_id
            else:
                primitive["attributes"][key] = accessor_id
        dst["meshes"][0]["primitives"].append(primitive)
        material = copy.deepcopy(src["materials"][group["material"]])
        texid = material["pbrMetallicRoughness"]["baseColorTexture"]["index"]
        texture = copy.deepcopy(src["textures"][texid])
        image = copy.deepcopy(src["images"][texture["source"]])
        v = src["bufferViews"][image["bufferView"]]
        payload = binary[v["byteOffset"]:v["byteOffset"]+v["byteLength"]]
        image["bufferView"] = view(payload)
        texture["source"] = group_index
        material["pbrMetallicRoughness"]["baseColorTexture"]["index"] = group_index
        dst["images"].append(image); dst["textures"].append(texture); dst["materials"].append(material)
        if obj_output:
            name = "SeatRow_Frame" if group_index == 0 else "SeatRow_Plastic"
            obj.extend(["g " + name, "usemtl " + name])
            for x,y,z in decoded["POSITION"]:
                obj.append(f"v {x:.10g} {-z:.10g} {y:.10g}")
            for u,vv in decoded["TEXCOORD_0"]:
                obj.append(f"vt {u:.10g} {1-vv:.10g}")
            for x,y,z in decoded["NORMAL"]:
                obj.append(f"vn {x:.10g} {-z:.10g} {y:.10g}")
            indices = [row[0] + vertex_base for row in decoded["indices"]]
            for i in range(0,len(indices),3):
                obj.append("f " + " ".join(f"{j}/{j}/{j}" for j in indices[i:i+3]))
            vertex_base += len(decoded["POSITION"])
            texture_file = name + ".png"
            (obj_output.parent / texture_file).write_bytes(payload)
            mtl.extend(["newmtl " + name, "Kd 1 1 1", "Ks 0.04 0.04 0.04", "Ns 32", "map_Kd " + texture_file, ""])
    blob.extend(b"\0"*((-len(blob))%4))
    dst["buffers"] = [{"byteLength":len(blob)}]
    dst["extras"] = {"license":"CC0-1.0", "source_url":SOURCE_URL, "source_sha256":SOURCE_SHA256,
                     "modifications":["Concrete mesh and texture omitted", "Two material primitives combined in one mesh", "Shared bounding-box center baked into positions; real scale preserved"],
                     "source_center_gltf_xyz_m":center, "dimensions_gltf_xyz_m":dimensions}
    js = json.dumps(dst,separators=(",",":")).encode();js += b" "*((-len(js))%4)
    data = b"glTF"+struct.pack("<II",2,28+len(js)+len(blob))+struct.pack("<II",len(js),0x4E4F534A)+js+struct.pack("<II",len(blob),0x004E4942)+blob
    output.parent.mkdir(parents=True,exist_ok=True);output.write_bytes(data)
    if obj_output:
        obj_output.write_text("\n".join(obj)+"\n",encoding="utf-8")
        (obj_output.parent/"SeatRow.mtl").write_text("\n".join(mtl)+"\n",encoding="utf-8")
    return {**dst["extras"], "bytes":len(data), "sha256":hashlib.sha256(data).hexdigest(),
            "mesh_count":1, "primitive_count":2, "indexed_triangles":1380,
            "canonical_dimensions_xyz_m":[dimensions[0],dimensions[2],dimensions[1]],
            "canonical_facing":"-Y", "canonical_bbox_center_m":[0,0,0]}


if __name__ == "__main__":
    p=argparse.ArgumentParser();p.add_argument("--source",type=Path,required=True);p.add_argument("--output",type=Path,required=True);p.add_argument("--obj-output",type=Path);p.add_argument("--report",type=Path)
    args=p.parse_args();report=prepare(args.source,args.output,args.obj_output)
    result=json.dumps(report,indent=2)+"\n"
    if args.report:args.report.write_text(result,encoding="utf-8")
    print(result)
