"""Decode selected Dagor binary BLK streams using their original shared namemap.

The on-disk schema follows pinned DagorEngine blk_serialize.cpp/blk_zstd.cpp.
Parameter order, duplicate names, types and child order are preserved.
Color values preserve the original E3DCOLOR bytes in B,G,R,A order.
"""
import math
import struct
from wt_vromfs import bounded_zstd

MAX_DECODED_BYTES = 512 * 1024 * 1024

class Reader:
    def __init__(self, data): self.data,self.pos=data,0
    def take(self,n):
        if n < 0 or self.pos+n > len(self.data): raise ValueError('Truncated BLK field')
        value=self.data[self.pos:self.pos+n];self.pos+=n;return value
    def varuint(self):
        value=0
        for shift in range(0,35,7):
            b=self.take(1)[0];value|=(b&127)<<shift
            if not b&128:
                if value>0xffffffff:raise ValueError('BLK uint32 overflow')
                return value
        raise ValueError('BLK varuint exceeds uint32')
    def names(self):
        count=self.varuint()
        if not count:return []
        size=self.varuint()
        if size>256*1024*1024 or count>size:raise ValueError('Invalid BLK names size')
        raw=self.take(size)
        if not raw.endswith(b'\0'):raise ValueError('Unterminated BLK name table')
        result=raw[:-1].decode('utf-8').split('\0')
        if len(result)!=count:raise ValueError('BLK name count differs')
        return result

def shared_context(files):
    raw=files.get('\xff?nm')
    if raw is None:return [],None,{}
    if len(raw)<40:raise ValueError('Shared BLK namemap header truncated')
    data=bounded_zstd(raw[40:],MAX_DECODED_BYTES);reader=Reader(data);names=reader.names()
    if reader.pos != len(data):raise ValueError('Trailing shared namemap data')
    key=raw[8:40].hex()+'.dict'
    dictionary=files.get(key)
    return names,dictionary,{'shared_names':len(names),'dictionary_entry':key if dictionary else None}

def decode_blk(raw,shared_names=(),dictionary=None):
    if not raw:raise ValueError('Empty BLK')
    label=raw[0]
    if label in (1,3):data=raw[1:]
    elif label==4:data=bounded_zstd(raw[1:],MAX_DECODED_BYTES)
    elif label==5:
        if dictionary is None:raise ValueError('BLK requires original zstd dictionary')
        data=bounded_zstd(raw[1:],MAX_DECODED_BYTES,dictionary)
    else:raise ValueError(f'Unsupported BLK label {label}')
    if len(data)>MAX_DECODED_BYTES:raise ValueError('BLK decoded size exceeds limit')
    if label!=1 and not shared_names:raise ValueError('BLK shared namemap missing')
    reader=Reader(data)
    local_names=reader.names()
    names=list(shared_names if label!=1 else ())+local_names
    block_count,param_count,complex_size=reader.varuint(),reader.varuint(),reader.varuint()
    if not 1<=block_count<=1000000 or param_count>4000000 or complex_size>256*1024*1024:raise ValueError('BLK limits exceeded')
    complex_data=reader.take(complex_size)
    param_data=reader.take(param_count*8)
    def complex_slice(offset,size):
        if offset+size>len(complex_data):raise ValueError('BLK complex parameter exceeds data')
        return complex_data[offset:offset+size]
    def string_at(offset):
        if offset>=len(complex_data):raise ValueError('BLK string outside data')
        end=complex_data.find(b'\0',offset)
        if end<0:raise ValueError('Unterminated BLK string')
        return complex_data[offset:end].decode('utf-8')
    params=[]
    types={1:'string',2:'int',3:'real',4:'point2',5:'point3',6:'point4',7:'ipoint2',8:'ipoint3',9:'bool',10:'color',11:'matrix',12:'int64',13:'ipoint4'}
    for i in range(param_count):
        packed,value=struct.unpack_from('<II',param_data,i*8);name_id=packed&0xffffff;kind=packed>>24
        if name_id>=len(names) or kind not in types:raise ValueError(f'Invalid BLK parameter {name_id=} {kind=}')
        if kind==1:
            if value&0x80000000:
                index=value&0x7fffffff
                if index>=len(names):raise ValueError('BLK string name index exceeds names')
                value=names[index]
            else:value=string_at(value)
        elif kind==2:value=struct.unpack('<i',struct.pack('<I',value))[0]
        elif kind==3:value=struct.unpack('<f',struct.pack('<I',value))[0]
        elif kind in (4,5,6,11):
            size={4:2,5:3,6:4,11:12}[kind]
            value=list(struct.unpack('<'+'f'*size,complex_slice(value,size*4)))
        elif kind in (7,8,13):
            size={7:2,8:3,13:4}[kind]
            value=list(struct.unpack('<'+'i'*size,complex_slice(value,size*4)))
        elif kind==9:
            if value not in (0,1):raise ValueError('Non-boolean BLK bool')
            value=bool(value)
        elif kind==10:value=list(struct.pack('<I',value))
        elif kind==12:value=struct.unpack('<q',complex_slice(value,8))[0]
        if isinstance(value,float) and not math.isfinite(value):raise ValueError('Nonfinite BLK float')
        if isinstance(value,list) and any(isinstance(x,float) and not math.isfinite(x) for x in value):raise ValueError('Nonfinite BLK vector')
        params.append({'name':names[name_id],'type':types[kind],'value':value})
    blocks=[];used=0;next_child=1
    for i in range(block_count):
        name_id,pc,bc=reader.varuint(),reader.varuint(),reader.varuint()
        first=reader.varuint() if bc else 0
        if name_id>len(names) or used+pc>len(params):raise ValueError('Invalid BLK block/name/params')
        if bc and (first!=next_child or first+bc>block_count):raise ValueError('Invalid BLK child run')
        next_child+=bc
        blocks.append({'name':names[name_id-1] if name_id else None,'params':params[used:used+pc],'children':[], '_child_run':(first,bc)})
        used+=pc
    if used!=param_count or next_child!=block_count or reader.pos!=len(data):raise ValueError('BLK table coverage differs')
    for i,b in enumerate(blocks):
        first,count=b.pop('_child_run');b['children']=blocks[first:first+count]
    return blocks[0],{'format_label':label,'decoded_bytes':len(data),'local_names':len(local_names),'shared_names':len(shared_names) if label!=1 else 0,'blocks':block_count,'parameters':param_count,'complex_bytes':complex_size}
