"""Read-only rigid contact clearance check from sampled RMUC terrain and 1x mesh vertices.

This is a geometry diagnostic, not a tire, suspension or traction simulation.
First run inspect_rmuc_waves.py in Blender to produce the source samples.
"""
import argparse
import json
from pathlib import Path
import numpy as np


def main():
    p=argparse.ArgumentParser();p.add_argument('--evidence',type=Path,required=True);a=p.parse_args();root=a.evidence
    d=np.load(root/'wave-profile.npz');body=np.load(root/'halfscale-lower-body-vertices.npz')
    x,height,tracks,rolling=d['x'],d['height'],d['y'],d['wheel_center_z'];axis=np.arange(5.8,9.201,.01)
    samples={}
    for name in body.files:
        v=body[name];bins=np.floor(v[:,:2]/.002).astype(int);order=np.argsort(v[:,2])
        _,first=np.unique(bins[order],axis=0,return_index=True);v=v[order[first]]
        if len(v):samples[name]=v
    wheels=np.array([[-.137267,-.137299,.0425],[-.137267,.137301,.0425],
                     [.137261,-.137299,.0425],[.137261,.137301,.0425]])
    minimum={k:(1e9,None,None) for k in samples};rows=[]
    for cx in axis:
        support=[]
        for wx,wy,_ in wheels:
            low=np.interp(cx+wx,x,rolling[0]);hi=np.interp(cx+wx,x,rolling[2])
            support.append(low+(hi-low)*(wy+.1373)/.2746)
        support=np.array(support);coef=np.linalg.lstsq(np.c_[wheels[:,:2],np.ones(4)],support,rcond=None)[0]
        normal=np.array([-coef[0],-coef[1],1]);normal/=np.linalg.norm(normal)
        forward=np.array([1,0,coef[0]]);forward/=np.linalg.norm(forward)
        rotation=np.c_[forward,np.cross(normal,forward),normal]
        rotated=wheels@rotation.T;rz=np.max(support-rotated[:,2]);spread=np.ptp(support-rotated[:,2])
        for name,vertices in samples.items():
            points=vertices@rotation.T+[cx,6.4,rz]
            low=np.interp(points[:,0],x,height[0]);hi=np.interp(points[:,0],x,height[2])
            ground=low+(hi-low)*(points[:,1]-tracks[0])/(tracks[2]-tracks[0])
            gap=points[:,2]-ground;index=int(np.argmin(gap))
            if gap[index]<minimum[name][0]:minimum[name]=(float(gap[index]),float(cx),vertices[index].tolist())
        rows.append([float(cx),float(rz),float(np.degrees(np.arctan(coef[0]))),float(spread)])
    report=dict(scope='source half-scale lower-mesh vertices against measured profiles; rigid fourwheel circle supports, no suspension or dynamics',
        sampled_step_m=.01,vertex_selection_bin_m=.002,route_y=6.4,
        minimum_clearance_by_part={k:dict(gap_m=g,route_x=x,local_vertex=v) for k,(g,x,v) in minimum.items()},
        source_body_bottom_only_2d_transverse_interpolation=True,max_fourwheel_contact_residual_m=max(v[3] for v in rows),pose_profiles=rows)
    (root/'rigid-contact-clearance.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='pose_profiles'},indent=2))


if __name__=='__main__':main()
