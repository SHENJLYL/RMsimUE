"""Offline source-pixel LC reflectance/cache and classification-boundary verification.

Uses original 23 albedo and 23 reflectance PNGs, a fixed mip0 footprint and the
source selection rules. Native anisotropic filtering, sampler count and timing
remain separate native gates. No Unreal process or assets are created.
"""
from pathlib import Path
import argparse, hashlib, importlib.util, json, sys
import numpy as np
from PIL import Image

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def noise2(p):
    pi=np.floor(p); pf=p-pi
    pt=np.concatenate((pi,pi+1),axis=-1)
    pt-=np.floor(pt/71)*71
    pt+=np.array([26,161,26,161]);pt*=pt
    h=pt[..., [0,2,0,2]]*pt[..., [1,1,3,3]]/951.135664
    h-=np.floor(h)
    b=pf*pf*pf*(pf*(pf*6-15)+10)
    return np.sum(h*np.stack(((1-b[...,0])*(1-b[...,1]),b[...,0]*(1-b[...,1]),(1-b[...,0])*b[...,1],b[...,0]*b[...,1]),axis=-1),axis=-1)

def bilinear(a,uv):
    size=np.array([a.shape[1],a.shape[0]])
    p=np.asarray(uv)*size-.5;b=np.floor(p).astype(np.int64);f=p-b
    def at(x,y):return a[(b[...,1]+y)%a.shape[0],(b[...,0]+x)%a.shape[1]].astype(np.float64)/255
    return (at(0,0)*(1-f[...,0,None])*(1-f[...,1,None])+at(1,0)*f[...,0,None]*(1-f[...,1,None])+at(0,1)*(1-f[...,0,None])*f[...,1,None]+at(1,1)*f[...,0,None]*f[...,1,None])

def unit(v):return v/np.linalg.norm(v,axis=-1,keepdims=True)
def rot(v,m):return v[...,0,None]*m[...,[0,2]]+v[...,1,None]*m[...,[1,3]]
def linear(v):return np.where(v<=.04045,v/12.92,((v+.055)/1.055)**2.4)

class Reference:
    def __init__(self,package):
        self.p=package;self.d=json.loads((package/'terrain-details/definition.json').read_text(encoding='utf-8-sig'))
        t=self.d['table'];tex=self.d['textures'];self.t=t
        self.stack=np.stack([np.asarray(Image.open(package/tex[n]['file']).convert('RGBA')) for n in t['albedo_names']])
        self.index=np.asarray(Image.open(package/tex['avg_vlaanderen_detailed_biome_tex_b']['file']))
        if self.index.ndim==3:self.index=self.index[:,:,0]
        self.flow=np.asarray(Image.open(package/tex['avg_vlaanderen_detailed_biome_tex_f']['file']).convert('RGBA'))
        self.macro=np.asarray(Image.open(package/tex['avg_vlaanderen_detailed_biome_tex_d']['file']).convert('RGBA'))
        self.slices=np.array([[l['slice'] for l in b['layers']] for b in t['biomes']])
        self.strength=np.array([[l['height_scale']*l['normal_scale'] for l in b['layers']] for b in t['biomes']])
        self.invert=np.array([[l['invert_height'] for l in b['layers']] for b in t['biomes']])
        self.mult=np.array([[l['weight_mul'] for l in b['layers']] for b in t['biomes']])
        self.amount=np.array([b['random_flowmap'][0] for b in t['biomes']])
        self.rd=json.loads((package/'terrain-reflectance/definition.json').read_text(encoding='utf-8'))
        self.rstack=np.stack([np.asarray(Image.open(package/'terrain-reflectance'/self.rd['textures'][name]['file']).convert('RGBA')) for name in self.rd['reflectance_names']])
        self.rslices=np.array([row['reflectance_slices'] for row in self.rd['biomes']])
        self.coloring=np.asarray(self.rd['microdetail_coloring']);self.idscale=self.rd['id_count_scale']

    def tex(self,uv,slice):
        p=np.asarray(uv)*1024-.5;b=np.floor(p).astype(np.int64);f=p-b
        def at(x,y):
            z=self.stack[slice,(b[...,1]+y)%1024,(b[...,0]+x)%1024].astype(np.float64)/255
            z[...,:3]=linear(z[...,:3]);return z
        return at(0,0)*(1-f[...,0,None])*(1-f[...,1,None])+at(1,0)*f[...,0,None]*(1-f[...,1,None])+at(0,1)*(1-f[...,0,None])*f[...,1,None]+at(1,1)*f[...,0,None]*f[...,1,None]

    def reftex(self,uv,slices):
        p=np.asarray(uv)*1024-.5;b=np.floor(p).astype(np.int64);f=p-b
        def at(x,y):return self.rstack[slices,(b[...,1]+y)%1024,(b[...,0]+x)%1024].astype(float)/255
        return at(0,0)*(1-f[...,0,None])*(1-f[...,1,None])+at(1,0)*f[...,0,None]*(1-f[...,1,None])+at(0,1)*(1-f[...,0,None])*f[...,1,None]+at(1,1)*f[...,0,None]*f[...,1,None]

    def evaluate(self,xz,cache=False,base_axis=None,forced_ids=None,forced_rot=None):
        n=len(xz);uv=xz*np.array([1,-1])/2048;corner=uv*4096-.5;base=np.floor(corner)
        if base_axis is not None:base[:,base_axis]-=1
        f=corner-base
        if np.min(f)<-1e-10 or np.max(f)>1+1e-10:raise ValueError('limit is not on a cell boundary')
        f=np.clip(f,0,1)
        bw=np.sqrt(np.stack(((1-f[:,0])*f[:,1],f[:,0]*f[:,1],f[:,0]*(1-f[:,1]),(1-f[:,0])*(1-f[:,1])),axis=-1))
        at=(base[:,None,:]+.5+np.array([[0,1],[1,1],[1,0],[0,0]]))/4096
        ids=self.index[np.floor(at[:,:,1]*4096).astype(np.int64)%4096,np.floor(at[:,:,0]*4096).astype(np.int64)%4096]
        if forced_ids is not None:ids=np.broadcast_to(np.asarray(forced_ids),(n,4))
        angle=np.floor(noise2(at*self.amount[ids][...,None]*self.t['source_parameters']['randomRotatePeriod'])*128)/128*(2*np.pi)
        sc=np.stack((np.sin(angle),np.cos(angle)),axis=-1);mask=self.amount[ids]<0
        if np.any(mask):sc[mask]=bilinear(self.flow,at[mask])[...,:2]*-2+1
        rotation=np.stack((sc[...,1],sc[...,0],-sc[...,0],sc[...,1]),axis=-1)
        if forced_rot is not None:rotation=np.broadcast_to(np.asarray(forced_rot),(n,4,4))
        ld=[];ln=[];lm=[];calls=0;lookups=[];local_normals=[];smooth=[];micro=[];reflection_calls=0
        macro=linear_bilinear(self.macro,uv)[:,:3]
        for layer in range(2):
            meters=self.t['source_parameters']['detailMul'+str(layer+1)]
            tc=xz/meters;dx=np.array([.01,0])/meters;dy=np.array([0,.01])/meters
            if layer:
                theta=13.1*np.pi/180;m=np.array([np.cos(theta),-np.sin(theta),np.sin(theta),np.cos(theta)])
                tc=rot(tc,m);dx=rot(dx,m);dy=rot(dy,m)
            local=rot(tc[:,None,:],rotation);gx=rot(dx,rotation);gy=rot(dy,rotation);slices=self.slices[ids,layer]
            samples=np.empty((n,4,4));delta=np.empty((n,4,2));seen=np.empty((n,4),dtype=int)
            for k in range(4):
                previous=np.full(n,k)
                if cache:
                    for j in range(k):
                        same=(slices[:,k]==slices[:,j])&np.all(rotation[:,k]==rotation[:,j],axis=-1)
                        previous=np.where(same,np.minimum(previous,j),previous)
                need=previous==k;rows=np.flatnonzero(need);reuse=np.flatnonzero(~need)
                calls+=len(rows)*3
                if len(rows):
                    v=self.tex(local[rows,k],slices[rows,k]);samples[rows,k]=v
                    delta[rows,k,0]=self.tex(local[rows,k]+gx[rows,k],slices[rows,k])[:,3]-v[:,3]
                    delta[rows,k,1]=self.tex(local[rows,k]+gy[rows,k],slices[rows,k])[:,3]-v[:,3]
                samples[reuse,k]=samples[reuse,previous[reuse]];delta[reuse,k]=delta[reuse,previous[reuse]];seen[:,k]=previous
            alpha=samples[:,:,3];hw=np.where(self.invert[ids,layer]>.5,1-alpha,alpha)*bw+bw*.0001
            hw/=np.maximum(hw.sum(axis=1,keepdims=True),1e-8);w=np.clip(hw**4-2e-10,0,1);w/=np.maximum(w.sum(axis=1,keepdims=True),1e-8)
            slope=-delta*self.strength[ids,layer,None]/.01
            normals=unit(np.concatenate((slope,np.ones((n,4,1))),axis=-1))
            reflection=np.zeros((n,4,4));rslices=self.rslices[ids,layer]
            for k in range(4):
                previous=np.full(n,k)
                if cache:
                    for j in range(k):
                        same=(rslices[:,k]==rslices[:,j])&np.all(rotation[:,k]==rotation[:,j],axis=-1)&(w[:,j]>0)
                        previous=np.where(same,np.minimum(previous,j),previous)
                active=w[:,k]>0;need=active&(previous==k);reuse=active&(previous<k)
                rows=np.flatnonzero(need);reuse_rows=np.flatnonzero(reuse);reflection_calls+=len(rows)
                if len(rows):reflection[rows,k]=self.reftex(local[rows,k],rslices[rows,k])
                reflection[reuse_rows,k]=reflection[reuse_rows,previous[reuse_rows]]
            arrayno=reflection[:,:,3]*self.idscale;ci=np.minimum(11,np.floor(arrayno).astype(int));part=arrayno-np.floor(arrayno)
            coloring=self.coloring[ci]*(1-part)+self.coloring[np.minimum(ci+1,11)]*part
            source_samples=samples.copy();source_samples[:,:,:3]*=(1-coloring[:,:,None])+macro[:,None,:]*coloring[:,:,None]
            ld.append(np.sum(source_samples*w[:,:,None],axis=1));ln.append(np.sum(normals*w[:,:,None],axis=1));lm.append(np.sum(self.mult[ids,layer]*bw,axis=1))
            smooth.append(np.sum(reflection[:,:,0]*w,axis=1))
            winner=np.argmax(w,axis=1);micro.append(reflection[np.arange(n),winner,3])
            lookups.append(seen);local_normals.append(normals)
        noise=noise2(xz/self.t['source_parameters']['randomVariationPeriod']);split=np.stack((noise,1-noise),axis=-1)
        ld=np.stack(ld,axis=1);ln=np.stack(ln,axis=1);lm=np.stack(lm,axis=1)
        lw=ld[:,:,3]*split*lm+split*.0001;lw/=np.maximum(lw.sum(axis=1,keepdims=True),1e-8);lw=np.clip(lw**4-2e-10,0,1);lw/=np.maximum(lw.sum(axis=1,keepdims=True),1e-8)
        # Macro is common to either exact limit. Its sRGB decode does not affect the reuse proof.
        color=np.sum(ld[:,:,:3]*lw[:,:,None],axis=1)
        normal=unit(np.sum(ln*lw[:,:,None],axis=1))
        roughness=1-np.sum(np.stack(smooth,axis=1)*lw,axis=1)
        micro_id=np.where(lw[:,0]>lw[:,1],micro[0],micro[1])
        return dict(color=color,normal=normal,roughness=roughness,micro_id=micro_id,calls=calls,reflection_calls=reflection_calls,local_normals=local_normals,lookups=lookups)


def linear_bilinear(a,uv):
    p=np.asarray(uv)*np.array([a.shape[1],a.shape[0]])-.5;b=np.floor(p).astype(int);f=p-b
    def at(x,y):
        z=a[(b[...,1]+y)%a.shape[0],(b[...,0]+x)%a.shape[1]].astype(float)/255
        z[...,:3]=linear(z[...,:3]);return z
    return at(0,0)*(1-f[...,0,None])*(1-f[...,1,None])+at(1,0)*f[...,0,None]*(1-f[...,1,None])+at(0,1)*(1-f[...,0,None])*f[...,1,None]+at(1,1)*f[...,0,None]*f[...,1,None]


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,required=True);ap.add_argument('--candidate',type=Path);ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    sys.path.insert(0,str(a.repo/'tools/unreal'))
    candidate=a.candidate or a.repo/'tools/unreal/wt_landdetail.py'
    spec=importlib.util.spec_from_file_location('lc_candidate',candidate);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    package=a.repo/'assets/maps/wt_vlaanderen';data=module.validate(package,json.loads((package/'scene-source.json').read_text(encoding='utf-8-sig')))
    ref=Reference(package);rng=np.random.default_rng(191123);checks=[]
    def check(name,passed,**detail):checks.append(dict(name=name,passed=bool(passed),**detail))
    hlsl=module.shader_code(data)
    check('continuous_gradients_and_shared_sampler',hlsl.count('Texture2DArraySampleGrad(Details')==3 and
          hlsl.count('Texture2DArraySampleGrad(Reflectance,DetailsSampler')==1 and 'ReflectanceSampler' not in hlsl)
    positions=rng.uniform(1,2047,(2048,2))
    for name,points,ids,rotation in [
        ('source_uniform',positions,None,None),
        ('different_strength_same_slice',np.array([[333.1317,476.3099]]),[0,2,0,2],np.tile([1,0,0,1],(4,1))),
        ('all_four_rotation_components',np.array([[333.1317,476.3099]]),[0,0,0,0],np.array([[1,0,0,1],[1,0,1,1],[1,1,0,1],[1,0,0,2]]))]:
        direct=ref.evaluate(points,forced_ids=ids,forced_rot=rotation);cached=ref.evaluate(points,True,forced_ids=ids,forced_rot=rotation)
        errors={k:float(np.max(np.abs(direct[k]-cached[k]))) for k in ('color','normal','roughness','micro_id')}
        check('cached_source_equivalence_'+name,max(errors.values())==0,errors=errors,
              direct_albedo_reads=direct['calls'],cached_albedo_reads=cached['calls'],
              direct_reflectance_reads=direct['reflection_calls'],cached_reflectance_reads=cached['reflection_calls'])
    saved=ref.rslices.copy();ref.rslices[2,0]=(ref.rslices[0,0]+1)%23
    points=np.array([[333.1317,476.3099]]);ids=[0,2,0,2];rotation=np.tile([1,0,0,1],(4,1))
    direct=ref.evaluate(points,forced_ids=ids,forced_rot=rotation);cached=ref.evaluate(points,True,forced_ids=ids,forced_rot=rotation)
    errors={k:float(np.max(np.abs(direct[k]-cached[k]))) for k in ('color','normal','roughness','micro_id')}
    check('independent_reflectance_identity_synthetic_guard',max(errors.values())==0,errors=errors,
          scope='synthetic alternate reflection mapping proves albedo cache identity cannot substitute for reflection identity')
    ref.rslices=saved
    for axis in (0,1):
        points=rng.uniform(1,2047,(256,2));points[:,axis]=(rng.integers(2,4092,256)+.5)*.5
        left=ref.evaluate(points,True,base_axis=axis);right=ref.evaluate(points,True)
        errors={k:float(np.max(np.abs(left[k]-right[k]))) for k in ('color','normal','roughness')}
        check('continuous_exact_classification_limit_'+str(axis),max(errors.values())<1e-12,errors=errors,
              points=256,categorical_micro_id_changes=int(np.count_nonzero(left['micro_id']!=right['micro_id'])))
    weights=np.array([[.25,.25,.25,.25],[.1,.4,.4,.1]]);ids=np.array([[.1,.3,.7,.9],[.1,.3,.7,.9]])
    winners=ids[np.arange(2),np.argmax(weights,axis=1)]
    check('source_neighbor_ties_first_and_layer_ties_second',winners.tolist()==[.1,.3] and
          float(np.where(.5>.5,.1,.9))==.9,neighbor_winners=winners.tolist(),layer_winner=.9,
          source='make_clipmap.dshl 665-667 and 828-831')
    report=dict(schema='wt_lc_reflectance_math_verification_v1',status='passed' if all(c['passed'] for c in checks) else 'failed',
        checks=checks,script_sha256=sha(__file__),candidate_sha256=sha(candidate),
        helper_sha256=sha(a.repo/'tools/unreal/wt_lc_reflectance.py'),
        reflectance_definition_sha256=sha(package/'terrain-reflectance/definition.json'),
        original_landdetail_sha256=sha(package/'terrain-details/definition.json'),native_run=False,
        scope='exact CPU-double mip0 fixed-footprint source sampling; no GPU/anisotropic/compiler/performance claim')
    (a.output/'verification.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(report))
    if report['status']!='passed':raise SystemExit(1)

if __name__=='__main__':main()
