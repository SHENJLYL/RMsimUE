"""Exercise stale-input rejection and permitted presentation/spawn changes."""
from __future__ import annotations
import argparse
import copy
import datetime
import json
import os
from pathlib import Path
import sys
from model_contract import digest,load_json,verify_manifest

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo",type=Path,default=Path(__file__).resolve().parents[2])
    p.add_argument("--manifest",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args();repo=a.repo.resolve();output=a.output.resolve()
    output.mkdir(parents=True,exist_ok=False)
    source_manifest=a.manifest.resolve();original=load_json(source_manifest)
    source_xml=source_manifest.parent/original["model_xml"]
    original_map=load_json(repo/"assets/maps/rmuc2027/map.json")
    original_shell=load_json(repo/"assets/maps/rmuc2027/arena-shell.json")
    results=[]
    def run(name,mutate=None,map_mutate=None,shell_mutate=None,expected_error=None):
        directory=output/name;directory.mkdir()
        xml=directory/"model.xml"
        # A hard link is read-only in this test. XML contents are never mutated.
        os.link(source_xml,xml)
        manifest=copy.deepcopy(original)
        for entry in manifest["runtime_bindings"]:
            if entry["role"]=="model_xml":entry["path"]=str(xml)
        if mutate:mutate(manifest)
        path=directory/"manifest.json";path.write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
        map_path=None
        if map_mutate or shell_mutate:
            spec,shell=copy.deepcopy(original_map),copy.deepcopy(original_shell)
            if map_mutate:map_mutate(spec)
            if shell_mutate:shell_mutate(shell)
            map_path=directory/"map.json"
            map_path.write_text(json.dumps(spec,indent=2)+"\n",encoding="utf-8")
            (directory/"arena-shell.json").write_text(json.dumps(shell,indent=2)+"\n",encoding="utf-8")
        item={"case":name,"expected":"rejected" if expected_error else "passed","manifest":str(path)}
        try:
            result=verify_manifest(path,repo,map_file=map_path)
            item["observed"]="passed";item["passed"]=expected_error is None
            item["physics_identity_sha256"]=result["physics_identity_sha256"]
        except Exception as error:
            item["observed"]="rejected";item["error"]=str(error)
            item["passed"]=expected_error is not None and expected_error in str(error)
        results.append(item);print(json.dumps(item),flush=True)
    run("unchanged_package")
    def spawn_change(spec):
        spec["spawns"][0]["position_m"][0]+=.75
        spec["spawns"][0]["yaw_rad"]+=.25
    run("spawn_only_allowed",map_mutate=spawn_change)
    def display_change(spec):
        spec["display_name"]="Temporary display identity for contract verification"
    def lighting_change(shell):
        shell["lighting"]["exposure_ev100"]+=.25
        shell["notes"]="Temporary presentation metadata for contract verification"
    run("display_and_lighting_allowed",map_mutate=display_change,shell_mutate=lighting_change)
    run("xml_hash_changed",mutate=lambda m:m.update(model_sha256="0"*64),
        expected_error="XML byte identity changed")
    def bad_profile(m):
        next(r for r in m["runtime_bindings"] if r["role"]=="drive_profile")["md5"]="0"*32
    run("stale_profile_hash",mutate=bad_profile,expected_error="physical input changed: drive_profile")
    run("missing_collision_binding",mutate=lambda m:m["runtime_bindings"].pop(),
        expected_error="Expected sealed vehicle and map collision runtime bindings")
    run("diagnostic_without_opt_in",mutate=lambda m:m.update(diagnostic_plane_only=True),
        expected_error="requires explicit diagnostic opt-in")
    run("map_units_changed",map_mutate=lambda m:m.update(units="cm"),
        expected_error="Unsupported physical map units")
    run("map_source_changed",map_mutate=lambda m:m.update(source_sha256="0"*64),
        expected_error="physical map/boundary differs")
    run("boundary_thickness_changed",shell_mutate=lambda s:s["perimeter"].update(
        collision_thickness_m=s["perimeter"]["collision_thickness_m"]+.01),
        expected_error="physical map/boundary differs")
    run("boundary_clearance_changed",shell_mutate=lambda s:s["perimeter"].update(
        outside_clearance_m=s["perimeter"]["outside_clearance_m"]+.001),
        expected_error="physical map/boundary differs")
    report={"schema":"rmsim_mujoco_contract_tests_v1","status":"passed" if all(r["passed"] for r in results) else "failed",
        "created_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),"command":sys.argv,
        "test_sha256":digest(Path(__file__)),"manifest_sha256":digest(source_manifest),
        "scope":"Copied fixtures only; canonical map, arena, profile, XML and collision assets were not modified",
        "case_count":len(results),"passed_count":sum(r["passed"] for r in results),"results":results}
    (output/"report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:report[k] for k in ("status","case_count","passed_count")}),flush=True)
    raise SystemExit(0 if report["status"]=="passed" else 1)

if __name__=="__main__":main()
