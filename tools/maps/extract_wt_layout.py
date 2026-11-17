"""Inspect original game-map instance layouts through a pinned, headless DAE checkout."""
import argparse
import contextlib
import hashlib
import json
from pathlib import Path
import sys
import time
import struct
from dagor_codec import configure_oodle
from extract_wt_terrain import chunks


def extract_cell(layer,cell,entities):
    data=layer.riDataRelFiles[cell.riDataRelOfs];data.seek(0)
    cell_size=layer.cellSz*layer.grid2world
    origin=[layer.world0Vxz[0]+cell.id%layer.numCellW*cell_size,cell.htMin,
            layer.world0Vxz[1]+cell.id//layer.numCellW*cell_size]
    factors=[cell_size/32767,(cell.htDelta or 8192)/32767,cell_size/32767]
    for subcell in range(64):
        start,end=cell.entCnt[subcell:subcell+2]
        if not 0<=start<=end<=len(layer.entCnt):raise ValueError('invalid cell counter range')
        for counter in layer.entCnt[start:end]:
            pool=layer.pregenEnts[counter.riResIdx]
            extra=0 if pool.zeroInstSeeds else layer.perInstDataDwords*4
            target=entities.setdefault(pool.riName,dict(pos_inst=bool(pool.posInst),
                palette_rotation=bool(pool.paletteRotation),palette_rotation_count=pool.paletteRotationCount,instances=[]))
            for _ in range(counter.riCount):
                if pool.posInst:
                    values=struct.unpack('<4h',data.read(8));data.read(extra)
                    position=[origin[i]+values[i]*factors[i] for i in range(3)]
                    target['instances'].append([*position,values[3]&0xffff])
                else:
                    values=struct.unpack('<12h',data.read(24));data.read(extra)
                    matrix=[v/256 for v in values]
                    for i in range(3):matrix[i*4+3]=origin[i]+values[i*4+3]*factors[i]
                    target['instances'].append(matrix)
    if data.tell()!=data.getSize():
        raise ValueError(f'cell {cell.id}: consumed {data.tell()} of {data.getSize()} instance bytes')


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--map',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--oodle-library',required=True)
    a=p.parse_args()
    source=a.map.resolve(strict=True);out=a.output.resolve()
    if out==source.parent or source.is_relative_to(out):raise ValueError('output must be separate from source')
    out.mkdir(parents=True,exist_ok=False)
    configure_oodle(a.oodle_library)
    checkout=Path(__file__).resolve().parents[2]/'third_party/dagor_asset_explorer'
    sys.path.insert(0,str(checkout/'src/dae'))
    from parse.dbld import DagorBinaryLevelData
    from util.fileread import BinFile
    class MapReader(DagorBinaryLevelData):
        # The upstream GUI supplies these display descriptors outside the parser class.
        classIconName=''
        classNiceName='Dagor binary level'
        fileExtension='bin'
        def computeData(self):
            # Use the bounds-checked current container reader; legacy DAE misaligns the
            # padded editor-version metadata before it even reaches the RIGz payload.
            blocks=list(chunks(Path(self.filePath).read_bytes()))
            rig=[(offset,payload) for tag,offset,payload in blocks if tag=='RIGz']
            if len(rig)!=1:raise ValueError('expected one RIGz block')
            self.processBin('RIGz',BinFile(rig[0][1]),rig[0][0])
            if not self.riGenLayers:raise ValueError('no RIGz layers decoded')
    result=dict(source=str(source),sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        tool_commit='1f0d1e04ded04bf9b28e8d5be93cb79714f505d3',status='running',layers=[])
    start=time.monotonic()
    try:
        with (out/'decoder.log').open('w',encoding='utf-8') as log,contextlib.redirect_stdout(log):
            level=MapReader(str(source));level.computeData()
            for i,layer in enumerate(level.riGenLayers):
                entities={}
                for cell in layer.cells:
                    if cell.riDataRelOfs in layer.riDataRelFiles:
                        extract_cell(layer,cell,entities)
                (out/f'layer-{i}-instances.json').write_text(json.dumps(entities,separators=(',',':')),encoding='utf-8')
                result['layers'].append(dict(index=i,grid=[layer.numCellW,layer.numCellH],cell_m=layer.cellSz*layer.grid2world,
                    populated_cells=len(layer.riDataRelFiles),asset_types=len(entities),instances=sum(len(v['instances']) for v in entities.values()),
                    note='raw Dagor row-major matrices / compact position-scale words; palette and procedural landclasses still require audit'))
        result['status']='decoded_pending_transform_audit'
    except Exception as e:
        result['status']='failed';result['error']=str(e)
        raise
    finally:
        result['elapsed_seconds']=time.monotonic()-start
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(result,indent=2))


if __name__=='__main__':main()
