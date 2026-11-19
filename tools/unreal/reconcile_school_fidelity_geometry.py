"""CPU-only reconciliation of saved native triangle evidence using proven UE UV welding.

The original two-ULP comparison remains immutable. A remaining face may be paired
only at identical material, exact float32 positions and identical winding. Every
changed native UV must equal an earlier, already matched source corner with the
same material, exact position and exact source normal, within MeshBuild.h's
fixed UVsEqual threshold. Counts and all source/native identities must close.
"""
from pathlib import Path
from collections import Counter
import argparse, hashlib, importlib.util, json, time, traceback

ROOT = Path(__file__).resolve().parents[2]
ENGINE = Path(r"E:\simulator-tools\UE_5.8\Engine\Source")
UV_WELD_THRESHOLD = 1.0 / 1024.0
G_PATH = ROOT / "tools/unreal/verify_school_fidelity_geometry.py"
spec = importlib.util.spec_from_file_location("strict_geometry", G_PATH)
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)

def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))

def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

def require(condition, message):
    if not condition:
        raise ValueError(message)

def prove_uv_welds(detail):
    source = G.read_source_obj(detail["source_obj"],
                               reverse_winding=detail["coordinate_contract"]["reverse_source_winding"])
    require(len(source) == detail["source_triangles"], "OBJ triangle count changed")
    missing = detail["missing_source_faces"]
    unexpected = detail["unmatched_native_faces"]
    missing_ids = {m["source_triangle"] for m in missing}
    absent_ids = {m["source_triangle"] for m in detail["per_face_exceptions"]}
    require(len(missing_ids) == len(missing), "Repeated missing face identity")
    require(len(absent_ids) == len(detail["per_face_exceptions"]), "Repeated exemption identity")
    require(not (missing_ids & absent_ids), "Missing/exempt source overlap")
    require(len(source) - len(missing_ids) - len(absent_ids) == detail["matched_triangles"],
            "Original matched-source complement does not close")
    used_native = set()
    proofs = []
    for item in missing:
        index = item["source_triangle"]
        face = source[index]
        require(face["line"] == item["obj_line"] and face["material"] == item["material"],
                "Missing record differs from the unchanged OBJ")
        require(face["corners"] == tuple(tuple(c) for c in item["corners_cm_uv0"]),
                "Missing corner data differs from the unchanged OBJ")
        candidates = []
        for ni, native in enumerate(unexpected):
            if ni in used_native or native["material"] != face["material"]:
                continue
            corners = tuple(tuple(c) for c in native["corners_cm_uv0"])
            for shift in range(3):
                aligned = corners[shift:] + corners[:shift]
                if tuple(c[:3] for c in aligned) == tuple(c[:3] for c in face["corners"]):
                    candidates.append((ni, native, aligned, shift))
        require(len(candidates) == 1, "Absent or ambiguous exact geometric/winding correspondence")
        ni, native, aligned, shift = candidates[0]
        replacements = []
        for ci, (expected, actual) in enumerate(zip(face["corners"], aligned)):
            if expected == actual:
                continue
            require(expected[:3] == actual[:3], "UV welding cannot alter positions")
            delta = tuple(G.f32(actual[k] - expected[k]) for k in (3, 4))
            require(all(abs(v) <= UV_WELD_THRESHOLD for v in delta), "UVsEqual threshold exceeded")
            normal = face["normals"][ci]
            require(normal is not None, "Missing source normal cannot prove a weld")
            representatives = []
            for ri, other in enumerate(source[:index]):
                if ri in missing_ids or ri in absent_ids or other["material"] != face["material"]:
                    continue
                for rci, other_corner in enumerate(other["corners"]):
                    if other_corner == actual and other["normals"][rci] == normal:
                        representatives.append({
                            "source_triangle":ri, "corner":rci, "obj_line":other["line"],
                            "vertex_index_1based":other["vertex_indices_1based"][rci],
                            "normal":normal, "already_matched_under_original_2ulp":True,
                            "earlier_source_face":True, "exact_native_position_and_uv":True})
            require(bool(representatives), "Native UV has no earlier matched source representative")
            replacements.append({
                "corner":ci, "source_vertex_index_1based":face["vertex_indices_1based"][ci],
                "source_normal":normal, "source_corner_cm_uv0":expected,
                "native_corner_cm_uv0":actual, "uv_delta_float32":delta,
                "uv_ulp_distance":[G.ulp_distance(expected[k],actual[k]) for k in (3,4)],
                "engine_uvs_equal_threshold":UV_WELD_THRESHOLD,
                "representatives":representatives})
        require(bool(replacements), "No UV representative substitution to explain")
        used_native.add(ni)
        proofs.append({
            "source_triangle":index, "source_obj_line":face["line"],
            "native_triangle":native["native_triangle"], "section":native["section"],
            "material":face["material"], "native_corner_rotation":shift,
            "exact_geometry_material_and_oriented_winding":True,
            "proof":"UE StaticMeshBuilder reuses an earlier overlapping source vertex with UVsEqual",
            "replacements":replacements})
    require(len(used_native) == len(unexpected) == len(missing), "UV pair accounting does not close")
    return proofs

def reconcile(native_report, output_dir):
    report_path = output_dir / "report.json"
    require(not report_path.exists(), "Final report already exists; preserve existing audit evidence")
    report = {"schema":"strict_school_geometry_cpu_reconciliation_v1", "status":"running",
              "meshes":[], "original_native_report":str(native_report),
              "original_native_report_sha256":sha(native_report),
              "scope":"Unchanged saved native LODResources[0] geometry; immutable native per-face evidence plus explicit source-representative UV welding proof. Nanite clusters and visual QA are separate.",
              "base_scalar_ulp_tolerance":2, "nanite_cluster_geometry_audited":False,
              "reconciliation_script":str(Path(__file__)), "reconciliation_script_sha256":sha(__file__)}
    try:
        native = read(native_report)
        execution = read(native_report.parent / "execution.json")
        require(execution["status"] == "exited" and execution["exit_code"] == 0, "Native audit not completed")
        require(native["checker_sha256"] == sha(G_PATH), "Native checker changed after saved audit")
        imported_path = Path(native["import_report"])
        require(sha(imported_path) == native["import_report_sha256"], "Import report identity changed")
        imported = read(imported_path)
        asset_root = Path(imported["asset_root"])
        prepared_path = asset_root / "prepared.json"
        prepared_sha = sha(prepared_path)
        require(prepared_sha == native["prepared_sha256"] == imported["prepared_sha256"],
                "Prepared identity mismatch")
        report.update(prepared_sha256=prepared_sha, import_report=str(imported_path),
                      import_report_sha256=sha(imported_path), checker_sha256=sha(G_PATH),
                      original_native_execution_sha256=sha(native_report.parent / "execution.json"))
        entries = {e["asset_name"]:e for e in read(prepared_path)["meshes"]}
        require(len(entries) == len(native["meshes"]) == len(imported["meshes"]) == 23,
                "Expected exactly all 23 mesh identities")
        by_asset = {m["asset"]:m for m in imported["meshes"]}
        native_root = ROOT / "unreal/RMSim/Content" / imported["native_base"].removeprefix("/Game/")
        identity = {}
        for relative, recorded in imported["native_files"].items():
            path = native_root / relative
            require(path.is_file(), "Missing native file " + relative)
            actual = {"bytes":path.stat().st_size, "sha256":sha(path)}
            require(actual == recorded, "Native file changed since import: " + relative)
            identity[relative] = actual
        write(output_dir / "native-file-identity.json",
              {"native_root":str(native_root), "import_report_sha256":sha(imported_path),
               "status":"all_current_files_match_import_record", "files":identity})
        report["native_file_identity"] = {"path":str(output_dir/"native-file-identity.json"),
                                         "sha256":sha(output_dir/"native-file-identity.json"),
                                         "verified_files":len(identity)}
        names_seen = set()
        for row in native["meshes"]:
            asset = row["asset"]
            name = asset.rsplit("/",1)[-1].split(".")[0]
            require(name in entries and name not in names_seen and asset in by_asset,
                    "Mesh identity missing, duplicated or changed")
            names_seen.add(name)
            entry = entries[name]
            path = Path(row["details"])
            require(sha(path) == row["details_sha256"], "Native per-mesh evidence changed")
            detail = read(path)
            source_path = asset_root / entry["file"]
            require(sha(source_path) == entry["sha256"] == detail["source_sha256"] ==
                    by_asset[asset]["source_sha256"], "Source OBJ identity changed")
            require(Path(detail["source_obj"]).resolve() == source_path.resolve(), "Source OBJ path mismatch")
            require(detail["asset"] == asset and detail["checker_sha256"] == native["checker_sha256"],
                    "Native detail identity mismatch")
            require(detail["allowed_scalar_ulp"] == 2 and detail["max_matched_scalar_ulp"] <= 2,
                    "Original comparison scalar tolerance changed")
            require(len(detail["missing_source_faces"]) == detail["unknown_missing_triangles"] and
                    len(detail["unmatched_native_faces"]) == detail["unexpected_native_triangles"] and
                    len(detail["per_face_exceptions"]) == detail["proven_absent_degenerate_or_duplicate_triangles"],
                    "Original detail counts do not match records")
            require(detail["source_triangles"] == entry["triangles"] ==
                    detail["matched_triangles"] + detail["proven_absent_degenerate_or_duplicate_triangles"] +
                    detail["unknown_missing_triangles"], "Original source counts do not close")
            require(detail["native_render_triangles"] == detail["matched_triangles"] +
                    detail["unexpected_native_triangles"], "Original native counts do not close")
            proofs = prove_uv_welds(detail) if detail["status"] == "failed" else []
            require(detail["status"] in ("passed","failed"), "Incomplete per-mesh status")
            require(detail["unknown_missing_triangles"] == detail["unexpected_native_triangles"] == len(proofs),
                    "Remaining non-weld mismatch")
            final_detail = dict(detail)
            final_detail.update(status="passed", original_native_detail=str(path),
                                original_native_detail_sha256=sha(path),
                                original_matched_under_2ulp=detail["matched_triangles"],
                                proven_uv_weld_triangles=len(proofs),
                                proven_uv_weld_corner_replacements=sum(len(p["replacements"]) for p in proofs),
                                per_face_uv_weld_proofs=proofs,
                                matched_triangles=detail["matched_triangles"]+len(proofs),
                                unknown_missing_triangles=0, unexpected_native_triangles=0,
                                missing_source_faces=[], unmatched_native_faces=[],
                                reconciliation_script_sha256=sha(__file__))
            require(final_detail["matched_triangles"] == final_detail["native_render_triangles"] ==
                    final_detail["source_triangles"] -
                    final_detail["proven_absent_degenerate_or_duplicate_triangles"], "Final count closure failed")
            target = output_dir / "geometry" / (name+".json")
            write(target,final_detail)
            final_row = {k:v for k,v in final_detail.items() if k in (
                "status","asset","source_triangles","native_render_triangles","matched_triangles",
                "proven_absent_degenerate_or_duplicate_triangles","unknown_missing_triangles",
                "unexpected_native_triangles","original_matched_under_2ulp","proven_uv_weld_triangles",
                "proven_uv_weld_corner_replacements","max_matched_scalar_ulp")}
            final_row.update(details=str(target), details_sha256=sha(target), source_sha256=entry["sha256"],
                             original_native_details_sha256=sha(path),
                             native_mesh_sha256=identity["Meshes/"+name+".uasset"]["sha256"])
            report["meshes"].append(final_row)
        require(names_seen == set(entries), "Prepared mesh inventory not closed")
        keys=("source_triangles","native_render_triangles","matched_triangles",
              "proven_absent_degenerate_or_duplicate_triangles","unknown_missing_triangles",
              "unexpected_native_triangles","original_matched_under_2ulp","proven_uv_weld_triangles",
              "proven_uv_weld_corner_replacements")
        report["totals"]={k:sum(row[k] for row in report["meshes"]) for k in keys}
        report["engine_source_evidence"] = {}
        for label, relative, lines in [
            ("fixed_uv_comparison","Runtime/Engine/Public/MeshBuild.h",[61,65]),
            ("vertex_attribute_comparison","Developer/MeshBuilder/Private/StaticMeshBuilder.cpp",[1508,1556]),
            ("earlier_wedge_representative","Developer/MeshBuilder/Private/StaticMeshBuilder.cpp",[1719,1736])]:
            engine_path=ENGINE/relative
            report["engine_source_evidence"][label]={"path":str(engine_path),"sha256":sha(engine_path),"lines":lines}
        report["status"]="passed"
    except Exception as error:
        report.update(status="failed",error=str(error),traceback=traceback.format_exc())
        write(report_path,report)
        raise
    write(report_path,report)
    print(json.dumps({"status":report["status"],"meshes":len(report["meshes"]),
                      "totals":report["totals"],"path":str(report_path),"sha256":sha(report_path)}))

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--native-report",type=Path,required=True)
    parser.add_argument("--output-dir",type=Path,required=True)
    args=parser.parse_args()
    reconcile(args.native_report,args.output_dir)
