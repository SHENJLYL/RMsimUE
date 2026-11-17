"""Bind verified derived collision assets; keep the previous rig and hash evidence."""
import argparse
import hashlib
import json
from pathlib import Path

def digest(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('--rig',type=Path,required=True)
    p.add_argument('--project',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True)
    p.add_argument('--evidence',type=Path,required=True)
    a=p.parse_args(argv);raw=a.rig.read_bytes();rig=json.loads(raw.decode('utf-8-sig'))
    report=json.loads(a.report.read_text(encoding='utf-8-sig'))
    if not report['status'].startswith('prepared_'):raise ValueError('collision preparation did not finish')
    parts={x['name']:x for x in rig['parts']};content=(a.project/'Content').resolve()
    def asset_file(asset):
        if not asset.startswith('/Game/') or '..' in asset:raise ValueError('invalid asset path')
        path=(content/(asset.split('.')[0][6:]+'.uasset')).resolve()
        if not path.is_relative_to(content):raise ValueError('asset escapes Content')
        return path
    for item in report['parts']:
        part=parts[item['name']]
        if item['source_fbx_sha256']!=part['source_fbx_sha256']:raise ValueError('source FBX changed')
        if digest(asset_file(part['asset']))!=item['visual_uasset_sha256']:raise ValueError('visual asset changed')
        if digest(asset_file(item['collision_asset']))!=item['collision_uasset_sha256']:raise ValueError('collision asset changed')
        part.update(collision_asset=item['collision_asset'],collision_source_fbx_sha256=item['source_fbx_sha256'],
                    collision_asset_sha256=item['collision_uasset_sha256'],collision_method=item['method'])
        part['collision_geometry_sha256']=item.get('collision_geometry_sha256','')
    a.evidence.mkdir(parents=True,exist_ok=False)
    (a.evidence/'rig-before.json').write_bytes(raw)
    a.rig.write_text(json.dumps(rig,indent=2)+'\n',encoding='utf-8')
    (a.evidence/'verification.json').write_text(json.dumps(dict(status='bound',parts=[x['name'] for x in report['parts']],
        report=str(a.report.resolve()),report_sha256=digest(a.report),rig_sha256=digest(a.rig)),indent=2),encoding='utf-8')
    print('Bound collision assets:',len(report['parts']))

if __name__=='__main__':main()
