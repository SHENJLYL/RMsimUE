"""Install the pinned official MuJoCo SDK used by RMSim (no system Python changes)."""
from __future__ import annotations
import argparse, ctypes, hashlib, json, os, platform, re, shutil, sys, tarfile, tempfile, urllib.request, zipfile
from pathlib import Path

VERSION="4.0.1"
PACKAGES={
 "windows-x86_64":("zip","abe3104a2ae0ca18a4b50fa1e79aaf57699496c7ee78ec7214104a0d1593fc75"),
 "linux-x86_64":("tar.gz","8df1308b2900a4ed6c18717ebbc9d66d53ab3fe7a4da860a7d5b21f94607299d"),
}
def sha(path):
 h=hashlib.sha256()
 with path.open("rb") as f:
  for data in iter(lambda:f.read(1048576),b""):h.update(data)
 return h.hexdigest()
def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument("--platform",choices=PACKAGES,default="windows-x86_64" if os.name=="nt" else "linux-x86_64")
 p.add_argument("--root",type=Path,default=Path(__file__).resolve().parents[2])
 args=p.parse_args();root=args.root.resolve();ext,expected=PACKAGES[args.platform]
 archive_name=f"mujoco-{VERSION}-{args.platform}.{ext}"
 url=f"https://github.com/google-deepmind/mujoco/releases/download/{VERSION}/{archive_name}"
 dest=root/"third_party/mujoco-sdk"/VERSION/args.platform
 cache=root/"artifacts/dependencies/mujoco";cache.mkdir(parents=True,exist_ok=True)
 archive=cache/archive_name
 if not archive.exists() or sha(archive)!=expected:
  req=urllib.request.Request(url,headers={"User-Agent":"RMSim-MuJoCo-Installer"})
  partial=archive.with_suffix(archive.suffix+".partial")
  with urllib.request.urlopen(req,timeout=120) as r,partial.open("wb") as f:shutil.copyfileobj(r,f)
  if sha(partial)!=expected:raise RuntimeError("official SDK archive SHA256 mismatch")
  partial.replace(archive)
 if dest.exists():
  marker=dest/"rmsim-sdk.json"
  if not marker.exists() or json.loads(marker.read_text())["archive_sha256"]!=expected:raise RuntimeError(f"refusing to replace unrecognized SDK at {dest}")
 else:
  dest.parent.mkdir(parents=True,exist_ok=True)
  with tempfile.TemporaryDirectory(prefix="rmsim-mujoco-",dir=str(dest.parent)) as temp:
   temp=Path(temp)
   if ext=="zip":
    with zipfile.ZipFile(archive) as z:
     for item in z.infolist():
      resolved=(temp/item.filename).resolve()
      if not resolved.is_relative_to(temp.resolve()):raise RuntimeError("unsafe archive entry")
     z.extractall(temp)
   else:
    with tarfile.open(archive) as t:t.extractall(temp,filter="data")
   candidates=list(temp.rglob("include/mujoco/mujoco.h"))
   if len(candidates)!=1:raise RuntimeError("SDK archive layout differs from expected")
   source=candidates[0].parents[2]
   shutil.move(str(source),str(dest))
  record={"version":VERSION,"platform":args.platform,"url":url,"archive_sha256":expected,"license":"Apache-2.0","upstream_commit":"4ed093a9f06b170bd1564942fa37e1bb907d93e3"}
  (dest/"rmsim-sdk.json").write_text(json.dumps(record,indent=2)+"\n",encoding="utf-8")
 # The full upstream SDK includes Filament math/ headers. On Windows their
 # names collide with UE Math/ headers, so expose only the native C API tree.
 api=dest/"c-api/include/mujoco"
 api.parent.mkdir(parents=True,exist_ok=True)
 shutil.copytree(dest/"include/mujoco",api,dirs_exist_ok=True)
 for source in (dest/"include/mujoco").rglob("*"):
  if source.is_file() and sha(source)!=sha(api/source.relative_to(dest/"include/mujoco")):
   raise RuntimeError("isolated C API header copy mismatch")
 header=(api/"mujoco.h").read_text()
 match=re.search(r"#define\s+mjVERSION_HEADER\s+(\d+)",header)
 library=dest/("bin/mujoco.dll" if args.platform.startswith("windows") else f"lib/libmujoco.so.{VERSION}")
 if not library.is_file():raise RuntimeError("SDK runtime library missing")
 runtime=None
 if (os.name=="nt")==args.platform.startswith("windows"):
  scope=os.add_dll_directory(str(library.parent)) if os.name=="nt" else None
  try:
   lib=ctypes.CDLL(str(library));lib.mj_version.restype=ctypes.c_int;runtime=lib.mj_version()
   if match and runtime!=int(match.group(1)):raise RuntimeError("SDK header/runtime version mismatch")
  finally:
   if scope:scope.close()
 report={"status":"passed","sdk":str(dest),"version":VERSION,"header_version":int(match.group(1)) if match else None,"runtime_version":runtime,"runtime_sha256":sha(library),"archive_sha256":expected,"url":url}
 (cache/f"install-{args.platform}.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
 print(json.dumps(report))
if __name__=="__main__":main()
