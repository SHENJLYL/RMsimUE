"""Resolve a decoded map's actual resource names against installed GRP metadata."""
import argparse
import collections
import contextlib
import json
from pathlib import Path
import sys


def main():
    p=argparse.ArgumentParser();p.add_argument('--game-root',type=Path,required=True)
    p.add_argument('--layout',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    root=Path(__file__).resolve().parents[2]
    sys.path.insert(0,str(root/'third_party/dagor_asset_explorer/src/dae'))
    from parse.gameres import GameResourcePack
    import util.log as log
    log.log=lambda *args,**kwargs:None
    required=collections.Counter()
    for f in a.layout.glob('layer-*-instances.json'):
        for name,entry in json.loads(f.read_text(encoding='utf-8')).items():required[name]+=len(entry['instances'])
    matches=collections.defaultdict(list);failures=[];paths=sorted((a.game_root/'content/base/res').glob('*.grp'))
    with (out/'scan.log').open('w',encoding='utf-8') as f,contextlib.redirect_stdout(f):
        for path in paths:
            try:
                group=GameResourcePack(str(path))
                for i in range(group.getRealResEntryCnt()):
                    entry=group.getRealResEntry(i);name=entry.getName()
                    if name in required:
                        # This pinned reader exposes name publicly but not class/offset; preserve exact parsed metadata.
                        matches[name].append(dict(group=str(path.resolve()),resource_id=i,
                            class_id=entry._RealResEntry__classId,offset=entry._RealResEntry__offset))
            except Exception as e:failures.append(dict(path=str(path),error=str(e)))
    missing=sorted(set(required)-set(matches))
    report=dict(required_types=len(required),stored_instances=sum(required.values()),scanned_groups=len(paths),
        resolved_types=len(matches),missing=missing,failures=failures,
        resources={k:dict(instances=required[k],matches=matches[k]) for k in sorted(matches)})
    (out/'resource-index.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='resources'},indent=2))


if __name__=='__main__':main()
