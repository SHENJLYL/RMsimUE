"""Build a validated, separate RMUC chassis contact-response model for native A/B tests."""
import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from model_contract import binding, digest, verify_manifest
from verify_model import compile_check


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    repo=Path(__file__).resolve().parents[2]
    base=args.base.resolve()
    result=args.output.resolve()
    if not result.is_relative_to(repo/'artifacts') or result.exists():
        raise ValueError('Use an unused model directory under artifacts')
    verify_manifest(base,repo)
    manifest=json.loads(base.read_text(encoding='utf-8-sig'))
    tree=ET.parse(base.parent/manifest['model_xml'])
    root=tree.getroot()
    body=root.find(".//body[@name='body']")
    if body is None:raise ValueError('Chassis body not found')
    geoms=body.findall('./geom')
    changed=[]
    for geom in geoms:
        name=geom.get('name','')
        if not name.startswith('g_body_') or geom.get('type')!='mesh':
            raise ValueError('Unexpected chassis geometry '+name)
        before=list(map(float,geom.attrib['solref'].split()))
        if before!=[.002,1.]:raise ValueError('Unexpected baseline contact response')
        geom.set('solref','0.006 1')
        geom.set('solimp','0.9 0.99 0.002 0.5 2')
        changed.append(name)
    if not changed:raise ValueError('Expected nonempty chassis hulls')
    result.mkdir(parents=True)
    xml=result/'model.xml'
    tree.write(xml,encoding='utf-8',xml_declaration=True)
    manifest=copy.deepcopy(manifest)
    manifest.update(created_utc=datetime.now(timezone.utc).isoformat(),model_xml=xml.name,
        model_sha256=digest(xml),chassis_contact_response={
            'base_manifest_sha256':digest(base),'base_xml_sha256':digest(base.parent/'model.xml'),
            'scope':'chassis convex hull contacts only','hulls':changed,'solref':[.006,1.],
            'solimp':[.9,.99,.002,.5,2.]})
    manifest['source_sha256'][Path(__file__).resolve().relative_to(repo).as_posix()]=digest(Path(__file__))
    manifest['runtime_bindings']=[binding(xml,'model_xml',repo) if entry['role']=='model_xml' else entry
                                  for entry in manifest['runtime_bindings']]
    _,_,compiled=compile_check(manifest,xml)
    manifest['variant_compile']=compiled
    dest=result/'manifest.json'
    dest.write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf8')
    verify_manifest(dest,repo)
    print(json.dumps({'chassis_hulls':len(changed),'manifest':str(dest),'xml':digest(xml),'compile':compiled}))


if __name__=='__main__':main()
