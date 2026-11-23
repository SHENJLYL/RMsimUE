"""Native asset/config-only RVT smoke; never loads or saves the source World.

UnrealEditor-Cmd RMSim.uproject -run=pythonscript -script=<this file>
  -RVTSmokeOutput=<fresh evidence directory> -NullRHI -NoShaderWorker
"""
import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
import traceback

import unreal


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    verification = output / "verification.json"
    if verification.exists():
        raise FileExistsError("use fresh RVT smoke evidence")
    report = dict(schema="wt_native_rvt_asset_smoke_v1", status="running", complete=False,
                  started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  world_loaded_by_script=False, world_saved_by_script=False,
                  material_graphs_modified=False, rendered_projection_verified=False)

    def write():
        temp = verification.with_suffix(".tmp")
        temp.write_text(json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")
        temp.replace(verification)

    write()
    try:
        project = Path(unreal.Paths.project_dir()).resolve()
        here = Path(__file__).resolve().parent
        sys.path.insert(0, str(here))
        report["engine"] = unreal.SystemLibrary.get_engine_version()
        report["r_virtual_textures"] = unreal.SystemLibrary.get_console_variable_int_value("r.VirtualTextures")
        report["file_hashes"] = {str(p): sha(p) for p in (
            Path(__file__).resolve(), here / "wt_runtime_virtual_texture.py",
            project / "Config/DefaultEngine.ini", project / "Binaries/Win64/UnrealEditor-RMSim.dll")}
        world_file = project / "Content/RMSim/Maps/wt_vlaanderen/World.umap"
        report["world_sha256_before"] = sha(world_file)
        report["api_classes_present"] = {name: hasattr(unreal, name) for name in (
            "RuntimeVirtualTexture", "RuntimeVirtualTextureFactory", "RuntimeVirtualTextureMaterialType",
            "RuntimeVirtualTextureVolume", "RuntimeVirtualTextureComponent", "VirtualTexturePoolConfig")}
        from wt_runtime_virtual_texture import ensure_rvt, _asset_state, _pool_state
        rvt = ensure_rvt()
        report["asset"] = _asset_state(rvt)
        report["physical_pool"] = _pool_state()
        asset_file = project / "Content/RMSim/Maps/wt_vlaanderen/Materials/WT_GroundDecalRVT.uasset"
        report["saved_rvt_asset_file"] = str(asset_file)
        report["saved_rvt_asset_sha256"] = sha(asset_file)
        report["world_sha256_after"] = sha(world_file)
        if report["world_sha256_after"] != report["world_sha256_before"]:
            raise RuntimeError("source World changed during the asset-only smoke")
        report.update(status="passed", complete=True)
        unreal.log("WT_RVT_ASSET_SMOKE_PASSED " + str(verification))
    except Exception as error:
        report.update(status="failed", complete=True, error=str(error), traceback=traceback.format_exc())
        unreal.log_error("WT_RVT_ASSET_SMOKE_FAILED " + str(error))
        raise
    finally:
        report["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        write()


if __name__ == "__main__":
    command = unreal.SystemLibrary.get_command_line()
    match = re.search(r'-RVTSmokeOutput=(?:"([^"]+)"|(\S+))', command)
    if not match:
        raise ValueError("pass -RVTSmokeOutput=<fresh directory>")
    run(match.group(1) or match.group(2))
