"""Audit actual viewport minimap pixels against the original calibrated map PNG.

Only a fresh output directory is written. This does not start UE or change any map.
Orientation is selected from eight image transforms independently of the declared
runtime flip flag. Photometric fitting tolerates camera-sector shading and gamma;
it establishes source-image orientation, not a claim of pixel-identical UI styling.
"""
import argparse
import datetime
import hashlib
import json
import math
from pathlib import Path
import sys
import traceback
import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(value, reason):
    if not value:
        raise ValueError(reason)


def corr(a, b):
    a=np.asarray(a,dtype=np.float64).reshape(-1);b=np.asarray(b,dtype=np.float64).reshape(-1)
    a=a-a.mean();b=b-b.mean();d=np.sqrt(np.dot(a,a)*np.dot(b,b))
    return float(np.dot(a,b)/d) if d>1e-10 else -1.0


def gray(rgb):
    return rgb[...,0]*.2126+rgb[...,1]*.7152+rgb[...,2]*.0722


def highpass(rgb):
    g=gray(rgb)
    smooth=np.asarray(Image.fromarray(np.clip(g,0,255).astype('uint8')).filter(ImageFilter.GaussianBlur(3.0)),dtype=np.float64)
    return g-smooth


def bilinear(rgb,u,v):
    h,w=rgb.shape[:2]
    x=np.clip(u*w-.5,0,w-1);y=np.clip(v*h-.5,0,h-1)
    x0=np.floor(x).astype(int);y0=np.floor(y).astype(int)
    x1=np.minimum(x0+1,w-1);y1=np.minimum(y0+1,h-1)
    tx=(x-x0)[...,None];ty=(y-y0)[...,None]
    return (rgb[y0,x0]*(1-tx)+rgb[y0,x1]*tx)*(1-ty)+(rgb[y1,x0]*(1-tx)+rgb[y1,x1]*tx)*ty


def transformed_uv(u,v,name):
    if name=='identity':return u,v
    if name=='flip_v':return u,1-v
    if name=='flip_u':return 1-u,v
    if name=='rotate_180':return 1-u,1-v
    if name=='transpose':return v,u
    if name=='transpose_flip_v':return v,1-u
    if name=='transpose_flip_u':return 1-v,u
    if name=='transpose_rotate_180':return 1-v,1-u
    raise ValueError(name)


def fit_color(expected,actual,mask):
    x=expected[mask];y=actual[mask]
    keep=np.ones(len(x),dtype=bool)
    slopes=np.ones(3);offsets=np.zeros(3)
    for _ in range(3):
        for c in range(3):
            a=x[keep,c];b=y[keep,c]
            var=np.mean((a-a.mean())**2)
            slopes[c]=np.clip(np.mean((a-a.mean())*(b-b.mean()))/max(var,1e-9),.2,3.0)
            offsets[c]=np.clip(b.mean()-slopes[c]*a.mean(),-100,100)
        residual=np.mean(np.abs(x*slopes+offsets-y),axis=1)
        keep=residual<=np.quantile(residual,.75)
    adjusted=np.clip(expected*slopes+offsets,0,255)
    return dict(raw_mae=float(np.mean(np.abs(x-y))),affine_mae=float(np.mean(residual[keep])),
        retained_samples=int(keep.sum()),trimmed_luminance_correlation=corr(gray(x[keep]),gray(y[keep])),
        color_slopes=slopes.tolist(),color_offsets=offsets.tolist()),adjusted


def make_mask(actual,xx,yy,layout,viewport_height,columns,rows):
    pos=np.array(layout['map_position_px']);size=np.array(layout['map_size_px'])
    view=np.array(layout['view_min_uv']);span=np.array(layout['view_size_uv']);ui=viewport_height/1080.0
    mask=(xx>=pos[0]+3)&(xx<pos[0]+size[0]-3)&(yy>=pos[1]+3)&(yy<pos[1]+size[1]-3)
    # Exclude the border, coordinate labels, scale text/bar and known grid locations.
    mask&=(xx>=pos[0]+24*ui)&(yy>=pos[1]+28*ui)&(yy<pos[1]+size[1]-26*ui)
    for i in range(1,columns):
        gx=pos[0]+(i/columns-view[0])/span[0]*size[0];mask&=np.abs(xx-gx)>2.5
    for i in range(1,rows):
        gy=pos[1]+(i/rows-view[1])/span[1]*size[1];mask&=np.abs(yy-gy)>2.5
    # Saturated team glyphs and bright neutral/player glyphs cannot describe terrain.
    chroma=actual.max(axis=2)-actual.min(axis=2)
    foreground=((chroma>85)&(actual.max(axis=2)>150))|((actual.min(axis=2)>170)&(actual.max(axis=2)>205))
    foreground=np.asarray(Image.fromarray((foreground*255).astype('uint8')).filter(ImageFilter.MaxFilter(7)))>0
    mask&=~foreground
    return mask


def save_rgb(path,array):
    Image.fromarray(np.clip(array,0,255).astype('uint8')).save(path)


def arrow_audit(evidence,metric,name,output,capture=None):
    filename=evidence/'hud'/f'{name}.png'
    observed=capture if capture and isinstance(capture.get('presentation'),dict) else metric
    layout_source='capture_time_presentation' if observed is capture and capture is not None else 'phase_metrics_compatibility_fallback'
    if observed is None or not filename.is_file():return dict(status='unverified',reason='capture layout/phase metric or screenshot unavailable')
    image=Image.open(filename).convert('RGB');rgb=np.asarray(image,dtype=np.float64)
    layout=observed['presentation'];center=np.array(layout['vehicle_position_px'],dtype=float)
    forward=np.array(layout['vehicle_forward_px'],dtype=float);forward/=max(np.linalg.norm(forward),1e-9)
    ui=image.height/1080.;radius=max(22*ui,15.)
    bounds=(max(0,int(center[0]-radius)),max(0,int(center[1]-radius)),min(image.width,int(center[0]+radius+1)),min(image.height,int(center[1]+radius+1)))
    crop=rgb[bounds[1]:bounds[3],bounds[0]:bounds[2]]
    yy,xx=np.mgrid[bounds[1]:bounds[3],bounds[0]:bounds[2]];dx=xx+.5-center[0];dy=yy+.5-center[1]
    rr=np.sqrt(dx*dx+dy*dy)
    white=(crop.min(axis=2)>195)&((crop.max(axis=2)-crop.min(axis=2))<45)&(rr>6*ui)&(rr<18*ui)
    count=int(white.sum());result=dict(status='unverified',layout_source=layout_source,white_candidates=count,predicted_forward_px=forward.tolist(),predicted_tip_px=(center+forward*15*ui).tolist(),screenshot_sha256=sha(filename))
    detected=None
    if count>=5:
        radial=rr[white];outer=white&(rr>=max(np.quantile(radial,.85),10*ui))
        if int(outer.sum())>=2:
            delta=np.array([dx[outer].mean(),dy[outer].mean()]);length=float(np.linalg.norm(delta))
            if length>0:
                direction=delta/length;dot=float(np.clip(np.dot(direction,forward),-1,1));error=math.degrees(math.acos(dot));detected=center+delta
                result.update(detected_tip_px=detected.tolist(),detected_radius_px=length,direction_dot=dot,angle_error_deg=error,outer_white_samples=int(outer.sum()))
                if 10*ui<=length<=17*ui:
                    result['status']='passed' if error<=12 else 'review_required'
    annotated=image.crop(bounds).resize(((bounds[2]-bounds[0])*5,(bounds[3]-bounds[1])*5),Image.Resampling.NEAREST)
    draw=ImageDraw.Draw(annotated);origin=(center-np.array(bounds[:2]))*5;tip=(center+forward*15*ui-np.array(bounds[:2]))*5
    draw.line([tuple(origin),tuple(tip)],fill=(0,255,100),width=2)
    if detected is not None:
        point=(detected-np.array(bounds[:2]))*5;draw.ellipse((point[0]-3,point[1]-3,point[0]+3,point[1]+3),outline=(255,80,200),width=2)
    annotated.save(output/f'{name}-arrow-review.png')
    image.crop(bounds).save(output/f'{name}-arrow-original.png')
    result['annotation']='Green line is predicted forward/tip; magenta circle is a detected outer white cluster. Original crop is saved separately.'
    return result


def audit(args,report):
    evidence=args.evidence.resolve(strict=True);package=args.package.resolve(strict=True);out=args.output
    report_path=evidence/'control-result.json';overview_path=package/'overview.json'
    native=json.loads(report_path.read_text(encoding='utf-8-sig'));overview=json.loads(overview_path.read_text(encoding='utf-8-sig'))
    require(native.get('real_viewport_hud_captures') is True,'report does not declare actual viewport HUD captures')
    metrics={m['name']:m for m in native.get('phase_metrics',[])}
    captures={c['name']:c for c in native.get('hud_screenshots',[])}
    baseline_capture=captures.get('01-minimap-baseline')
    capture_layout_available=baseline_capture is not None and isinstance(baseline_capture.get('presentation'),dict)
    baseline=baseline_capture if capture_layout_available else metrics.get('original_map_ui_assets_and_supported_spawn')
    layout_source='capture_time_presentation' if capture_layout_available else 'phase_metrics_compatibility_fallback'
    require(baseline is not None,'baseline presentation is missing')
    layout=baseline['presentation'];position=np.array(layout['map_position_px'],dtype=float);size=np.array(layout['map_size_px'],dtype=float)
    require(np.isfinite(position).all() and np.isfinite(size).all() and (size>40).all(),'invalid map bounds')
    screenshot_path=evidence/'hud/01-minimap-baseline.png';image=Image.open(screenshot_path).convert('RGB')
    source_path=(package/overview['image_file']).resolve(strict=True)
    require(source_path.is_relative_to(package),'source image escapes map package')
    require(sha(source_path)==overview['image_sha256'],'source map PNG hash differs from the calibrated image')
    source_image=Image.open(source_path).convert('RGB')
    bounds=(math.floor(position[0]),math.floor(position[1]),math.ceil(position[0]+size[0]),math.ceil(position[1]+size[1]))
    require(bounds[0]>=0 and bounds[1]>=0 and bounds[2]<=image.width and bounds[3]<=image.height,'map rectangle lies outside the actual viewport')
    actual=np.asarray(image.crop(bounds),dtype=np.float64)
    yy,xx=np.mgrid[bounds[1]:bounds[3],bounds[0]:bounds[2]];xx=xx+.5;yy=yy+.5
    view=np.array(layout['view_min_uv'],dtype=float);span=np.array(layout['view_size_uv'],dtype=float)
    require(np.isfinite(view).all() and np.isfinite(span).all() and (span>0).all(),'invalid presented view rectangle')
    mask=make_mask(actual,xx,yy,layout,image.height,int(overview.get('grid_columns',7)),int(overview.get('grid_rows',7)))
    require(mask.sum()>=4000,'not enough terrain pixels after excluding HUD overlays')
    actual_hp=highpass(actual);names=['identity','flip_v','flip_u','rotate_180','transpose','transpose_flip_v','transpose_flip_u','transpose_rotate_180']
    candidates=[];rendered={};predicted={}
    for name in names:
        best=None
        for blur in (0.,1.5):
            src=np.asarray(source_image if blur==0 else source_image.filter(ImageFilter.GaussianBlur(blur)),dtype=np.float64)
            for shift_x in (-.5,0.,.5):
                for shift_y in (-.5,0.,.5):
                    u=view[0]+(xx-position[0]+shift_x)/size[0]*span[0];v=view[1]+(yy-position[1]+shift_y)/size[1]*span[1]
                    su,sv=transformed_uv(u,v,name);expected=bilinear(src,su,sv)
                    hp_corr=corr(highpass(expected)[mask],actual_hp[mask])
                    # Geometry correlation ranks all variants before any color/outlier fitting.
                    if best is None or hp_corr>best['highpass_correlation']:
                        best=dict(orientation=name,highpass_correlation=hp_corr,source_preblur_sigma_px=blur,screen_sample_offset_px=[shift_x,shift_y]);predicted[name]=expected
        photometric,adjusted=fit_color(predicted[name],actual,mask);best.update(photometric)
        best['score']=.75*best['highpass_correlation']+.25*best['trimmed_luminance_correlation']
        candidates.append(best);rendered[name]=adjusted
    candidates.sort(key=lambda r:r['score'],reverse=True);winner=candidates[0];runner=candidates[1]
    passed=winner['orientation']=='flip_v' and winner['highpass_correlation']>=.55 and winner['score']-runner['score']>=.15 and winner['affine_mae']<=18
    save_rgb(out/'actual-minimap.png',actual);save_rgb(out/'expected-flip-v.png',predicted['flip_v']);save_rgb(out/'expected-unflipped.png',predicted['identity'])
    save_rgb(out/'best-photometric-match.png',rendered[winner['orientation']]);Image.fromarray((mask*255).astype('uint8')).save(out/'terrain-comparison-mask.png')
    residual=np.mean(np.abs(actual-rendered[winner['orientation']]),axis=2);heat=np.zeros_like(actual);heat[...,0]=np.minimum(residual*8,255);heat[...,1]=np.minimum(residual*2,255);heat[~mask]=0
    save_rgb(out/'best-difference-heatmap.png',heat)
    w,h=actual.shape[1],actual.shape[0];panel=Image.new('RGB',(w*3,h+36),'#20252b');draw=ImageDraw.Draw(panel)
    for i,(label,arr) in enumerate([('Actual viewport crop',actual),('Expected V flip',predicted['flip_v']),('Expected without flip',predicted['identity'])]):
        panel.paste(Image.fromarray(np.clip(arr,0,255).astype('uint8')),(i*w,36));draw.text((i*w+7,10),label,fill='white')
    panel.save(out/'orientation-comparison.png')
    arrows={}
    for key,name in [('physical_left_turn_is_screen_counterclockwise','02-turn-left'),('physical_right_turn_is_screen_clockwise','03-turn-right')]:
        arrows[name]=arrow_audit(evidence,metrics.get(key),name,out,captures.get(name))
    report.update(status='passed' if passed else 'failed',scope='actual map texture orientation and source-image correspondence',native_report_status=native.get('status'),viewport_size=[image.width,image.height],crop_bounds_px=list(bounds),layout_source=layout_source,presentation=layout,terrain_pixels=int(mask.sum()),crop_pixels=int(mask.size),mask_coverage=float(mask.mean()),best_orientation=winner['orientation'],runner_up_orientation=runner['orientation'],orientation_score_gap=winner['score']-runner['score'],candidates=candidates,arrow_reviews=arrows,criteria=dict(expected_orientation='flip_v',minimum_highpass_correlation=.55,minimum_score_gap=.15,maximum_affine_mae_8bit=18,minimum_terrain_samples=4000),inputs=dict(control_result=dict(path=str(report_path),sha256=sha(report_path)),screenshot=dict(path=str(screenshot_path),sha256=sha(screenshot_path)),overview=dict(path=str(overview_path),sha256=sha(overview_path)),source_png=dict(path=str(source_path),sha256=sha(source_path))),limitations=['Grid, coordinate labels, scale bar and bright/saturated glyphs are excluded from the terrain fit.','Affine color fitting and limited source prefilter/subpixel search accommodate native texture sampling and camera-sector shading.','This automated test does not verify OS keyboard/mouse input or claim 1:1 original HUD appearance.','Arrow crops include separate predicted annotations; unverified/review_required detections require human inspection.'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--package',type=Path,default=Path(__file__).resolve().parents[2]/'assets/maps/wt_vlaanderen')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();require(not args.output.exists(),'use a fresh pixel-audit output directory');args.output.mkdir(parents=True)
    report=dict(schema='rmsim_map_ui_pixel_audit_v1',status='failed',started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),script_sha256=sha(__file__))
    try:audit(args,report)
    except Exception as exc:report.update(failure=str(exc),traceback=traceback.format_exc())
    report['finished_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    destination=args.output/'verification.json';destination.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status=report['status'],verification=str(destination.resolve()),best_orientation=report.get('best_orientation'),score_gap=report.get('orientation_score_gap'),failure=report.get('failure')),ensure_ascii=True))
    raise SystemExit(0 if report['status']=='passed' else 3)
