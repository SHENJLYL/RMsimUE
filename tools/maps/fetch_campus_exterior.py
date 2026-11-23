"""Fetch CC0 Poly Haven tree and ambientCG grass for the RMUC campus exterior."""
from pathlib import Path
import argparse
import hashlib
import json
import requests
import zipfile

TREE="island_tree_02"
GRASS="Grass005"
def digest(p):
 h=hashlib.sha256()
 with p.open("rb") as f:
  for b in iter(lambda:f.read(2**20),b""): h.update(b)
 return h.hexdigest()

def fetch(url,path,size=None):
 path.parent.mkdir(parents=True,exist_ok=True)
 if path.exists():
  if size is not None and path.stat().st_size!=size: raise RuntimeError("Existing file differs: "+str(path))
  return {"url":url,"bytes":path.stat().st_size,"sha256":digest(path)}
 temp=path.with_suffix(path.suffix+".partial")
 try:
  with requests.get(url,headers={"User-Agent":"RMSimUE5 CC0 asset importer"},stream=True,timeout=120) as resp:
   resp.raise_for_status()
   with temp.open("wb") as f:
    for data in resp.iter_content(2**20):
     if data:f.write(data)
  if size is not None and temp.stat().st_size!=size: raise RuntimeError("Incomplete download: "+str(path))
  temp.replace(path)
 finally:
  if temp.exists():temp.unlink()
 return {"url":url,"bytes":path.stat().st_size,"sha256":digest(path)}

def main():
 ap=argparse.ArgumentParser()
 ap.add_argument("--root",type=Path,default=Path("D:/simulator/assets/maps/rmuc2027/arena/campus-exterior"))
 a=ap.parse_args();root=a.root.resolve();root.mkdir(parents=True,exist_ok=True)
 api=requests.get("https://api.polyhaven.com/files/"+TREE,timeout=30);api.raise_for_status()
 meta=api.json()["gltf"]["1k"]["gltf"]
 tree=root/"sources/polyhaven"/TREE
 refs={}
 for rel,rec in [(TREE+"_1k.gltf",meta),*meta["include"].items()]:
  print("FETCH",rel,flush=True)
  refs["sources/polyhaven/"+TREE+"/"+rel]=fetch(rec["url"],tree/rel,rec["size"])
 url="https://ambientcg.com/get?file="+GRASS+"_1K-JPG.zip"
 path=root/"sources/ambientcg"/(GRASS+"_1K-JPG.zip")
 print("FETCH",GRASS,flush=True)
 refs["sources/ambientcg/"+path.name]=fetch(url,path)
 with zipfile.ZipFile(path) as z:
  names=z.namelist()
  for kind in ["Color","NormalDX","Roughness"]:
   matches=[n for n in names if n.endswith("_"+kind+".jpg")]
   if len(matches)!=1:raise ValueError("Missing exactly one "+kind+" in "+str(names))
   dest=root/"textures"/(GRASS+"_"+kind+".jpg");dest.parent.mkdir(exist_ok=True)
   dest.write_bytes(z.read(matches[0]))
   refs["textures/"+dest.name]={"source_archive":str(path.relative_to(root)),"sha256":digest(dest),"bytes":dest.stat().st_size}
 license_record={"schema":"rmsim_campus_exterior_sources_v1","license":"CC0-1.0","tree":{"page":"https://polyhaven.com/a/island_tree_02","author":"Poly Haven","file":TREE+"_1k.gltf"},"grass":{"page":"https://ambientcg.com/view?id=Grass005","archive":path.name},"files":refs}
 (root/"SOURCE.json").write_text(json.dumps(license_record,indent=2)+"\n",encoding="utf-8")
 print("CC0_EXTERIOR_FETCH_PASSED",len(refs),flush=True)
if __name__=="__main__":main()
