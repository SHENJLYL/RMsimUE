"""Fetch the pinned CC0 8K sky into the school gym package."""
from pathlib import Path
import requests,hashlib,json,datetime,os
root=Path('D:/simulator')
out=root/'assets/maps/rmuc2027/arena/sketchupbox-school-gym/environment-fidelity'
out.mkdir(parents=True,exist_ok=True)
name='kloofendal_48d_partly_cloudy_puresky_8k.hdr'
target=out/name
record={'id':'kloofendal_48d_partly_cloudy_puresky','source_url':'https://polyhaven.com/a/kloofendal_48d_partly_cloudy_puresky','license':'CC0-1.0','license_url':'https://polyhaven.com/license','authors':['Greg Zaal','Jarod Guest'],'resolution':[8192,4096],'url':'https://dl.polyhaven.org/file/ph-assets/HDRIs/hdr/8k/'+name,'size':74844926,'md5':'28091ec4839b564f57f780c316d6e9fd'}
if not target.exists():
 p=target.with_suffix('.download')
 with requests.get(record['url'],stream=True,timeout=(20,90)) as response:
  response.raise_for_status()
  with p.open('wb') as f:
   for chunk in response.iter_content(2**20):
    if chunk:f.write(chunk)
 assert p.stat().st_size==record['size']
 assert hashlib.md5(p.read_bytes()).hexdigest()==record['md5']
 os.replace(p,target)
data=target.read_bytes()
assert len(data)==record['size'] and hashlib.md5(data).hexdigest()==record['md5']
record.update(file=name,sha256=hashlib.sha256(data).hexdigest(),downloaded_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
(out/'SOURCE.json').write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
print(json.dumps(record,indent=2))
