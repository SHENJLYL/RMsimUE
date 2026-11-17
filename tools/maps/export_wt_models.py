"""Export referenced RendInst geometry and original material slots, without baking map transforms."""
import argparse
import contextlib
import hashlib
import json
from pathlib import Path
import re
import sys
import traceback
import numpy as np
from dagor_codec import configure_oodle


def main():
    p=argparse.ArgumentParser();p.add_argument('--index',type=Path,required=True)
    p.add_argument('--game-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--model',action='append');p.add_argument('--oodle-library',required=True)
    a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    configure_oodle(a.oodle_library)
    root=Path(__file__).resolve().parents[2]
    sys.path.insert(0,str(root/'third_party/dagor_asset_explorer/src/dae'))
    from parse.gameres import GameResourcePack,GameResDesc
    from parse.realres import RendInst
    from util.assetcacher import AssetCacher
    import util.log as log
    log.log=lambda *args,**kwargs:None
    index=json.loads(a.index.read_text(encoding='utf-8'))['resources']
    names=a.model or sorted(index)
    report=dict(status='running',requested=len(names),models=[],failures=[])
    groups={}
    with (out/'decoder.log').open('w',encoding='utf-8') as logfile,contextlib.redirect_stdout(logfile):
        desc=GameResDesc(str(a.game_root/'content/base/res/riDesc.bin'));desc.loadDataBlock();AssetCacher.appendGameResDesc(desc)
        for name in names:
            try:
                if not re.fullmatch('[A-Za-z0-9_-]+',name):raise ValueError('unsafe asset name')
                candidates=[c for c in index[name]['matches'] if c['class_id']==RendInst.staticClassId]
                if len(candidates)!=1:raise ValueError('expected unique RendInst resource')
                entry=candidates[0];groupfile=entry['group']
                if groupfile not in groups:groups[groupfile]=GameResourcePack(groupfile)
                resource=groups[groupfile].getRealResource(entry['resource_id'])
                model=resource.getModel(0)
                vertices=np.array([model.getVertex(i) for i in range(model.vertCnt)],dtype='<f4')
                uvs=np.array([model.getUV(i) for i in range(model.vertCnt)],dtype='<f4')
                materials=[dict(name=m.getName(),shader=getattr(m,'cls',''),parameters=getattr(m,'par',''),
                    texture_slots=list(m.getTextureSlots())) for m in model.materials or []]
                lookup={m['name']:i for i,m in enumerate(materials)}
                faces=[];material_ids=[]
                for obj in model:
                    mat=None;source_mat=None
                    for i,face in enumerate(obj.faces):
                        if i in obj.materials:mat=obj.materials[i]
                        if i in getattr(obj,'source_material_ids',{}):source_mat=obj.source_material_ids[i]
                        if mat not in lookup:raise ValueError('unresolved material '+str(mat))
                        faces.append(face);material_ids.append(source_mat if source_mat is not None else lookup[mat])
                triangles=np.array(faces,dtype='<i4')
                if not np.all(np.isfinite(vertices)) or triangles.min()<0 or triangles.max()>=len(vertices):
                    raise ValueError('invalid decoded geometry')
                lo,hi=np.array(resource.sourceBounds[0]),np.array(resource.sourceBounds[1])
                outside=max(float((lo-vertices.min(axis=0)).max()),float((vertices.max(axis=0)-hi).max()))
                if outside>0.05:raise ValueError(f'decoded vertices exceed declared model bounds by {outside}m')
                folder=out/name;folder.mkdir()
                uv_channels={f'uv{key}':np.array(value,dtype='<f4') for key,value in model.extraUVs.items()}
                np.savez_compressed(folder/'mesh.npz',vertices=vertices,uvs=uvs,triangles=triangles,
                    normals=np.array(model.vertexNormals,dtype='<f4'),
                    material_ids=np.array(material_ids,dtype='<i4'),**uv_channels)
                metadata=dict(name=name,source=entry,vertices=len(vertices),triangles=len(triangles),lod=0,
                    bounds_dagor_m=dict(lo=vertices.min(axis=0).tolist(),hi=vertices.max(axis=0).tolist()),
                    declared_bounds_dagor_m=dict(lo=list(resource.sourceBounds[0]),hi=list(resource.sourceBounds[1])),
                    uv_channels=list(uv_channels),
                    used_material_indices=sorted(set(material_ids)),
                    materials=materials,coordinates='unmodified Dagor model axes; Y up',
                    mesh_sha256=hashlib.sha256((folder/'mesh.npz').read_bytes()).hexdigest())
                (folder/'model.json').write_text(json.dumps(metadata,indent=2)+'\n',encoding='utf-8')
                report['models'].append(dict(name=name,triangles=len(triangles),vertices=len(vertices),material_count=len(materials)))
            except Exception as e:
                traceback.print_exc(file=logfile);report['failures'].append(dict(name=name,error=str(e)))
            (out/'result.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
            completed=len(report['models'])+len(report['failures'])
            if completed%50==0:print(f'Models {completed}/{len(names)}, failures {len(report["failures"])}',file=sys.__stdout__,flush=True)
    report['status']='passed' if not report['failures'] else 'partial_or_failed'
    (out/'result.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status=report['status'],requested=len(names),exported=len(report['models']),
        triangles=sum(m['triangles'] for m in report['models']),failures=report['failures']),indent=2))
    return 1 if report['failures'] else 0


if __name__=='__main__':sys.exit(main())
