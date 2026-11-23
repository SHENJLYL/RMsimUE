"""Prepare an explicit UE grassland enhancement without changing original foliage.

Uses original biome semantics, source road/rail/water triangles, original physical
building footprints and source small-grass textures. Distribution and crossed-card
geometry are declared UE adaptations. Native rendering is a separate acceptance.
"""
from __future__ import annotations
import argparse
import collections
import copy
import json
import math
from pathlib import Path
import re
import shutil
import time
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from prepare_wt_foliage import read, save, sha, checked_file, copy_bound, bilinear_pixels

SIZE = 4096
PIXEL_M = .5
HARD_LAND_MATERIALS = {2,3,5,6,7,8,9,10,18,22,23,25,26,33,34,35,36,37,38,41}
BUILDING = re.compile(r'building|house|barn|barrack|hangar|depot|shed|stable|tribune|tower|factory|bunker|church|kiosk|foundation|bridge|rail|curb|pavement')
GRASS_BIOMES = [0,1,2,9,10,11,12,13,18,19,24,26,27,29,30,31,32,33,36]


def bind(path, root):
    return dict(file=path.resolve().relative_to(root.resolve()).as_posix(), sha256=sha(path))


def source_texture(repo, spec):
    # Legacy paths inside the original source report are provenance, not executable
    # dependencies. Resolve original extraction by basename under canonical repo.
    candidates = list((repo/'artifacts/maps/wt_existing/avg_vlaanderen').glob('textures-*/*'+Path(spec['file']).name))
    matching = [p for p in candidates if sha(p) == spec['sha256']]
    if not matching:
        raise ValueError('canonical original texture missing: '+spec['file'])
    return matching[0]


def draw_faces(image, vertices, triangles):
    """Project original world X/Z triangle footprints; never replace by AABBs."""
    if not len(triangles):
        return 0
    pts = vertices[triangles][:,:,[0,2]].astype(np.float64)
    lo, hi = pts.min(axis=1), pts.max(axis=1)
    select = (hi[:,0]>=0)&(lo[:,0]<=2048)&(hi[:,1]>=0)&(lo[:,1]<=2048)
    # Vertical walls contribute no filled area; roofs/floors retain the footprint.
    area2 = np.abs((pts[:,1,0]-pts[:,0,0])*(pts[:,2,1]-pts[:,0,1])-
                   (pts[:,1,1]-pts[:,0,1])*(pts[:,2,0]-pts[:,0,0]))
    pts = pts[select & (area2>1e-8)]
    draw = ImageDraw.Draw(image)
    for tri in pts:
        draw.polygon([(float(x*2), float((2048-z)*2)) for x,z in tri], fill=255)
    return len(pts)


def build_footprints(repo, source, output):
    root = repo/'artifacts/maps/wt_existing/avg_vlaanderen'
    masks = {k:Image.new('L',(SIZE,SIZE)) for k in ('road_rail','structures','water')}
    records = []; counts=collections.Counter()
    land_path=root/'landmesh-01/landmesh.json'; land=read(land_path)
    records.append(bind(land_path, repo))
    for item in land['cells']:
        if item['kind'] not in ('decal','combined'):
            continue
        path=root/'landmesh-01'/item['file']
        with np.load(path) as data:
            face=data['triangles'][np.isin(data['material_ids'],list(HARD_LAND_MATERIALS))]
            n=draw_faces(masks['road_rail'], data['vertices'], face)
        if n: records.append(dict(bind(path,repo),category='road_rail',projected_triangles=n));counts['road_rail_triangles']+=n
    static_path=root/'static-02/scene.json'; static=read(static_path); records.append(bind(static_path,repo))
    water_ids=[i for i,m in enumerate(static['materials']) if m['shader']=='water_aces']
    if water_ids != [4]:
        raise ValueError('original water material inventory changed')
    for item in static['objects']:
        b=item['bounds_dagor_m']
        if b['hi'][0]<0 or b['lo'][0]>2048 or b['hi'][2]<0 or b['lo'][2]>2048: continue
        path=root/'static-02'/item['file']
        with np.load(path) as data:
            n=0
            for kind,select in [('water',data['material_ids']==4),('structures',data['material_ids']!=4)]:
                nf=draw_faces(masks[kind],data['vertices'],data['triangles'][select]);counts[kind+'_static_triangles']+=nf;n+=nf
        if n: records.append(dict(bind(path,repo),category='static_world',projected_triangles=n))
    cached={}
    for layout_path in sorted((root/'layout-06').glob('layer-*-instances.json')):
        layout=read(layout_path);records.append(bind(layout_path,repo))
        for name,entry in layout.items():
            if not BUILDING.search(name) or '_dp_' in name or entry['pos_inst']: continue
            path=root/'collision-all-04'/name/'collision.npz'
            if not path.exists(): continue
            if name not in cached:
                with np.load(path) as data: cached[name]=(data['vertices'].astype(np.float64),data['triangles'].copy())
            vertices,faces=cached[name]
            if not len(vertices) or not len(faces):
                counts['intentionally_empty_physical_resources']+=1
                continue
            local_radius=float(np.linalg.norm(vertices,axis=1).max())
            n=0;instances=0
            for row in entry['instances']:
                matrix=np.asarray(row,dtype=np.float64).reshape(3,4)
                position=matrix[:,3]
                radius=float(local_radius*np.linalg.norm(matrix[:,:3],ord=2))
                if position[0]+radius<0 or position[0]-radius>2048 or position[2]+radius<0 or position[2]-radius>2048:continue
                nf=draw_faces(masks['structures'],vertices@matrix[:,:3].T+position,faces)
                if nf: instances+=1;n+=nf
            if n:
                records.append(dict(bind(path,repo),category='instanced_PHYS_COLLIDABLE',model=name,
                    projected_triangles=n,core_instances=instances,transform_source=layout_path.relative_to(repo).as_posix()))
                counts['physical_instances']+=instances;counts['physical_triangles']+=n
    result={}
    for kind, image in masks.items():
        path=output/'masks'/(kind+'.png'); image.save(path)
        # 0.75m roads/rails, 0.5m physical structures/water. Ceil to pixel grid.
        radius=2 if kind=='road_rail' else 1
        result[kind]=np.asarray(image.filter(ImageFilter.MaxFilter(radius*2+1)))>0
        counts[kind+'_raw_pixels']=int(np.count_nonzero(np.asarray(image)))
        counts[kind+'_buffered_pixels']=int(result[kind].sum())
    return result, records,dict(counts)


def land_coverage(package, land):
    atlas=np.asarray(Image.open(checked_file(package,land['textures']['WT_LandWeights'])),dtype=np.float32)/255
    cells={tuple(c['xy']):c for c in land['cells']}
    result=np.zeros((SIZE,SIZE),np.float32);xx=(np.arange(SIZE,dtype=np.float32)+.5)*.5
    for row in range(0,SIZE,128):
        zz=2048-(np.arange(row,row+128,dtype=np.float32)+.5)*.5
        for cx in (16,17):
            cols=np.where((xx/1024+16).astype(np.int32)==cx)[0]
            for cy in (16,17):
                rows=np.where((zz/1024+16).astype(np.int32)==cy)[0]
                if not len(cols) or not len(rows):continue
                cell=cells[(cx,cy)];count=cell['blend_count'];classes=cell['class_ids'][:count]
                if 1 not in classes:continue
                fx=(xx[cols]/1024+16-cx)[None,:];fy=(zz[rows]/1024+16-cy)[:,None]
                previous=[bilinear_pixels(atlas,(pg%112)*68+2+fx*64-.5,(pg//112)*68+2+fy*64-.5)
                          for pg in cell['pages'][:math.ceil(max(0,count-1)/3)]]
                arr=np.concatenate(previous,axis=2)[...,:count-1] if previous else np.empty((len(rows),len(cols),0))
                slot=classes.index(1)
                weight=np.maximum(0,1-arr.sum(axis=2)) if slot==count-1 else arr[...,slot]
                result[np.ix_(row+rows,cols)]=weight
    return result


def noise(cell_m, seed):
    n=int(math.ceil(2048/cell_m))+1
    values=np.random.default_rng(seed).random((n,n),dtype=np.float32)
    result=np.empty((SIZE,SIZE),np.float32)
    x=(np.arange(SIZE,dtype=np.float32)+.5)*.5/cell_m
    for row in range(0,SIZE,128):
        y=(np.arange(row,row+128,dtype=np.float32)+.5)*.5/cell_m
        result[row:row+128]=bilinear_pixels(values,x[None,:],y[:,None])
    return result


def color_masks(spec):
    colors={}
    for channel in 'rgb':
        colors[channel]={}
        for end,key in [('min','from'),('max','to')]:
            colors[channel][end]=[v/255 for v in spec['source_colors']['color_mask_'+channel+'_'+key]['rgba']]
    return colors


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,required=True)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--small-source',type=Path,required=True)
    p.add_argument('--small-meshes',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--evidence',type=Path,required=True)
    p.add_argument('--staging',action='store_true',help='prepare a fresh review package before publishing canonical foliage-enhanced')
    p.add_argument('--baseline-definition',type=Path,help='immutable prior definition for before/after proof and fixed spatial witnesses')
    a=p.parse_args();repo=a.repo.resolve();package=repo/'assets/maps/wt_vlaanderen';out=a.output.resolve()
    if out.exists() or a.evidence.exists():raise ValueError('use fresh output and evidence directories')
    if not a.staging and out!=package/'foliage-enhanced':raise ValueError('enhancement must be an independent canonical package or explicit staging output')
    original=package/'foliage/foliage.json';base=read(original);source=read(a.source)
    small=read(a.small_source);mesh=read(a.small_meshes);land_path=package/'landclass/landclass.json';land=read(land_path)
    if small['status']!='passed' or mesh['status']!='passed' or mesh['source_sha256']!=sha(a.small_source) or min(small.get('version',1),mesh.get('version',1))<2:
        raise ValueError('small grass source and card mesh proof required')
    baseline=read(a.baseline_definition) if a.baseline_definition else None
    baseline_proof=read(checked_file(a.baseline_definition.parent,baseline['enhancement_proof'])) if baseline else None
    if len(base['layers'])!=13 or len(base['models'])!=7 or base['source_level_sha256']!=source['source_level_sha256']:
        raise ValueError('original foliage identity differs')
    started=time.monotonic();out.mkdir(parents=True);a.evidence.mkdir(parents=True)
    for folder in ('textures','meshes','weights','masks','source'): (out/folder).mkdir()
    result=copy.deepcopy(base)
    result.update(schema='wt_enhanced_foliage_v1',version=2,status='prepared_pending_native_spatial_acceptance')
    result['source_inputs']={}
    for path in (original,a.source,a.small_source,a.small_meshes,land_path,package/'heightfield.json'):
        result['source_inputs'][path.resolve().relative_to(repo).as_posix()]=sha(path)
    result['source_config']=dict(file='source/foliage-source.json',sha256=sha(a.source))
    shutil.copyfile(a.source,out/'source/foliage-source.json')
    shutil.copyfile(a.small_source,out/'source/small-grass-source.json')
    for model in result['models']:
        for file_key,hash_key,container in [('file','sha256',model),('texture','texture_sha256',model['material']),
                                          ('alpha_texture','alpha_texture_sha256',model['material'])]:
            src=checked_file(original.parent,dict(file=container[file_key],sha256=container[hash_key]))
            copy_bound(src,out,container[file_key])
    copy_bound(checked_file(original.parent,dict(file=base['land_color']['texture'],sha256=base['land_color']['texture_sha256'])),out,base['land_color']['texture'])
    textures={t['name']:t for t in small['textures']};cards={m['id']:m for m in mesh['models']}
    for spec in small['models']:
        card=cards['ue_'+spec['id']];m=copy.deepcopy(card)
        copy_bound(checked_file(a.small_meshes.parent,card),out,m['file'])
        mask=textures[spec['texture']];alpha=textures[spec['alpha_texture']];normal=textures[spec['normal_texture']]
        for t in (mask,alpha,normal): copy_bound(checked_file(a.small_source.parent,t),out,t['file'])
        m['material']=dict(shader='ue_small_grass_card',texture=mask['file'],texture_sha256=mask['sha256'],
            alpha_texture=alpha['file'],alpha_texture_sha256=alpha['sha256'],
            normal_texture=normal['file'],normal_texture_sha256=normal['sha256'],normal_size=normal['size'],
            normal_semantics=spec['normal_semantics'],texture_address='clamp',
            source_gamma_equal_one=all(t['source_gamma_equal_one'] for t in (mask,alpha,normal)),
            texture_semantics='original linear small-grass RGB mask atlas',alpha_semantics='original paired T0_a.r')
        m['fbx_import_scale']=1;result['models'].append(m)
    index_path=source_texture(repo,source['core_region']['source_index_texture'])
    indices=np.asarray(Image.open(index_path));result['source_inputs'][index_path.relative_to(repo).as_posix()]=sha(index_path)
    if indices.shape!=(SIZE,SIZE):raise ValueError('original biome grid differs')
    footprints,records,counts=build_footprints(repo,source,out)
    biome_hard=np.isin(indices,[8,35])
    hard=biome_hard.copy()
    for mask in footprints.values():hard|=mask
    Image.fromarray(hard.astype(np.uint8)*255).save(out/'masks/hard-exclusion.png')
    # Reserve an additional metre at hard edges for medium/flower meshes. The
    # short layer can cover soil outside the hard buffer without clipping tracks.
    flower_clear=np.asarray(Image.fromarray(hard.astype(np.uint8)*255).filter(ImageFilter.MaxFilter(5)))==0
    lc=land_coverage(package,land)
    eligible=np.isin(indices,GRASS_BIOMES).astype(np.float32)
    eligible[np.isin(indices,[4,16])]=.22
    height_spec=read(package/'heightfield.json');height_file=package/height_spec['height_file']
    height=np.fromfile(height_file,dtype='<u2').reshape(SIZE,SIZE).astype(np.float32)*(64/65535)-20
    height=height[::-1].copy();dz,dx=np.gradient(height,.5,.5)
    slope=np.sqrt(dx*dx+dz*dz);slope_factor=np.clip((.85-slope)/.60,0,1)
    large=noise(32,61118);medium=noise(9,11018);fine=noise(2.5,31018)
    variation=.40+.30*large+.20*medium+.10*fine
    soil=eligible*lc*slope_factor*(~hard)
    base_weight=soil*variation
    Image.fromarray(np.rint(base_weight*255).astype(np.uint8)).save(out/'masks/short-grass-coverage.png')
    cluster=np.clip((large*.65+medium*.35-.48)/.30,0,1)
    fields={};layer_stats=[]
    for i,layer in enumerate(result['layers']):
        ids=layer['source']['biome_ids'];model=layer['model_id']
        flower=model not in ('grass_fill_a','grass_simple_a')
        own=np.isin(indices,ids)*lc*slope_factor*flower_clear
        if flower:
            weight=own*cluster*(.08+.28*fine)
            layer['density_per_100m2']=8.0 if 'tulip' in model else 6.0
            layer['scale_x']=layer['scale_y']=[.40,.75]
            layer['scale_z']=[.35,.65]
        else:
            weight=own*(.12+.22*medium)
            layer['density_per_100m2']=12.0 if model=='grass_fill_a' else 16.0
            layer['scale_x']=layer['scale_y']=[.38,.70]
            layer['scale_z']=[.28,.52]
        layer['start_cull_distance_cm']=9000;layer['end_cull_distance_cm']=14000
        layer['enhancement']=dict(role='local_flower_cluster' if flower else 'secondary_grass_variety',
            original_layer_preserved=layer['id'],source_biome_ids=ids,hard_footprint_exclusion=True)
        fields[i]=np.rint(np.clip(weight,0,1)*255).astype(np.uint8)
    for j,spec in enumerate(small['models']):
        i=13+j
        weight=base_weight if j==0 else soil*(.20+.55*(medium if j==1 else 1-medium))*(.45+.55*large)*flower_clear
        fields[i]=np.rint(np.clip(weight,0,1)*255).astype(np.uint8)
        density=[200.0,150.0,120.0][j]
        layer=dict(id='ue_short_grass_'+str(j),model_id='ue_'+spec['id'],weight_texture='WT_GrassWeights3',
            weight_channel=j+1,density_per_100m2=density,scale_x=[.65,1.40],scale_y=[.65,1.40],
            scale_z=[[.70,1.40],[.55,.95],[.55,.95]][j],scaling='lock_xy',
            start_cull_distance_cm=7000,end_cull_distance_cm=11000,collision=False,
            color_masks=color_masks(spec),source=dict(small_grass_type=spec['id'],parameters=spec['parameters']),
            enhancement=dict(role='continuous_short_grass' if j==0 else 'short_grass_variation',
                hard_footprint_exclusion=True,original_source_colors=True))
        result['layers'].append(layer)
    result['weight_textures']=[]
    for t in range(4):
        packed=np.stack([fields[4*t+c] for c in range(4)],axis=2)
        path=out/'weights'/('WT_GrassWeights'+str(t)+'.png');Image.fromarray(packed).save(path)
        if not np.array_equal(np.asarray(Image.open(path)),packed):raise ValueError('weight PNG bytes differ')
        result['weight_textures'].append(dict(id='WT_GrassWeights'+str(t),file=path.relative_to(out).as_posix(),
            sha256=sha(path),width=SIZE,height=SIZE,channels=4,srgb=False,address='clamp'))
    for i,layer in enumerate(result['layers']):
        values=fields[i];overlap=int(np.count_nonzero(values[hard]))
        if overlap:raise ValueError('grass remains on excluded hard footprint')
        stats=dict(id=layer['id'],nonzero_pixels=int(np.count_nonzero(values)),
            weighted_area_m2=float(values.sum(dtype=np.float64)/255*.25),hard_overlap_pixels=overlap,
            expected_instances=float(values.sum(dtype=np.float64)/255*.25*layer['density_per_100m2']/100))
        layer['weight_statistics']=stats;layer_stats.append(stats)
    samples=[]
    def sample(label,x,z,predicate):
        col=min(SIZE-1,max(0,int(x*2)));row=min(SIZE-1,max(0,int((2048-z)*2)))
        samples.append(dict(id=label,ros_xy_m=[x,-z],png_xy=[col,row],biome_id=int(indices[row,col]),
            hard=bool(hard[row,col]),hard_categories=[k for k,m in footprints.items() if m[row,col]],
            height_m=float(height[row,col]),slope=float(slope[row,col]),expected_surface=predicate,
            weights={l['id']:int(fields[i][row,col]) for i,l in enumerate(result['layers'])}))
    sample('previous_gui_station_grass',1075.232351,1012.656026,'station rail/road exclusion or outside edge')
    sample('spawn',1028,1020,'source-resolved surface')
    # Select real eligible terrain positions, not invented rendered landmarks.
    yy,xx=np.indices((SIZE,SIZE),dtype=np.float32);xx=(xx+.5)*.5;zz=2048-(yy+.5)*.5
    near=(xx-1075.23)**2+(zz-1012.66)**2
    choices=[('near_station_grass', (near>12**2)&(near<90**2)&(base_weight>.55)&flower_clear),
             ('green_slope', (near>100**2)&(near<650**2)&(base_weight>.45)&(slope>.08)&flower_clear),
             ('hard_road_edge', (near<200**2)&footprints['road_rail']),
             ('open_meadow', (near>200**2)&(base_weight>.65)&flower_clear)]
    for label,mask in choices:
        previous=next((x for x in baseline_proof['samples'] if x['id']==label),None) if baseline_proof else None
        if previous:
            sample(label,previous['ros_xy_m'][0],-previous['ros_xy_m'][1],previous['expected_surface'])
        else:
            candidates=np.flatnonzero(mask)
            if not len(candidates):raise ValueError('no actual source witness for '+label)
            chosen=candidates[np.argmin(near.ravel()[candidates])];row,col=divmod(int(chosen),SIZE)
            sample(label,float(xx[row,col]),float(zz[row,col]),'hard_clear' if label=='hard_road_edge' else 'grassland')
    result['color_input_encoding']='source_srgb_bytes_normalized; shader parameters use pow(rgb,2.2), alpha unchanged'
    result['placement']['source_rule_reconstruction']=False
    result['placement']['enhancement']='UE grassland coverage and deterministic multi-scale variation with original-source hard footprint exclusion'
    result['adaptation_scope']=dict(geometry='7 original meshes + 3 explicit UE short crossed-card models',
        coverage='source semantic biome/landclass/slope and real source footprint exclusions; continuous UE short-grass layer',
        color='published gpuGrass.cpp CPU pow2.2 + grass_generate.dshl RGB mask composition; original six endpoints',
        original_random_positions='not stored; UE Halton placement',wind_animation=False)
    result['visual_adapter']=dict(schema='wt_grass_visual_adapter_v2',
        source_normal_channels=dict(x='A',y='G',translucency='B',smoothness='R'),
        source_normal_reference='grass_generate.dshl:821-826',
        root_normals='source ground-up, with tilted-tip normals from original vertical_angle parameters',
        source_tip_reference='grass_generate_inc.dshl:254-257,525-529',
        ue_surface_adaptation='world-space grass normals preserve the upper hemisphere for two-sided cards; source AG detail, B transmission and R gloss',
        minimum_normal_up_component=.20,ue_single_atlas_per_card=True,
        original_color_endpoints_unchanged=True,source_gamma_conversion_count=1,
        density_per_100m2=[200,150,120],hard_footprint_exclusions_unchanged=True,
        flower_scale_z=[.35,.65],baseline_definition_sha256=sha(a.baseline_definition) if baseline else None)
    result['limitations']=['UE distribution/short-card enhancement is not original Dagor grass placement',
        'wind animation not implemented','native render acceptance at three spatial views is separate from preparation']
    proof=dict(schema='wt_foliage_enhancement_proof_v1',status='passed',scope='offline_source_bound_preparation',
        original_definition=bind(original,repo),source_level_sha256=base['source_level_sha256'],
        source_inputs=result['source_inputs'],source_geometry=records,geometry_counts=counts,
        hard_biomes=[8,35],hard_material_indices=sorted(HARD_LAND_MATERIALS),hard_model_regex=BUILDING.pattern,
        raster=dict(resolution=[SIZE,SIZE],pixel_m=.5,png_top='source Z=2048 / ROS Y=-2048',
            road_buffer_m=1.0,structure_water_buffer_m=.5,flower_extra_buffer_m=1.0),
        source_meshes_unchanged=True,original_foliage_sha256=sha(original),
        short_model_count=3,total_models=10,total_layers=16,weight_texture_count=4,
        short_height_ranges_m=[[next(m for m in result['models'] if m['id']==l['model_id'])['bounds_m']['hi'][2]*s
                               for s in l['scale_z']] for l in result['layers'][13:]],
        hard_pixels=int(hard.sum()),short_nonzero_pixels=int(np.count_nonzero(fields[13])),
        short_grass_area_m2=float(np.count_nonzero(fields[13])*.25),layers=layer_stats,samples=samples,
        masks={p.name:dict(file=p.relative_to(out).as_posix(),sha256=sha(p)) for p in (out/'masks').glob('*.png')},
        weight_textures=result['weight_textures'],preparer_sha256=sha(Path(__file__)),elapsed_seconds=time.monotonic()-started,
        native_render_status='pending; require station/green slope/hard edge views')
    proof['visual_adapter']=result['visual_adapter']
    if baseline:
        old_hard=baseline_proof['masks']['hard-exclusion.png']['sha256']
        if proof['masks']['hard-exclusion.png']['sha256']!=old_hard:
            raise ValueError('visual refinement changed the accepted hard footprint exclusion')
        proof['baseline']=dict(definition=bind(a.baseline_definition,repo),
            original_hard_exclusion_sha256=old_hard,hard_exclusion_bytes_unchanged=True,
            prior_layer_statistics=baseline_proof['layers'],fixed_spatial_witnesses=True)
    if not all(sha(repo/path)==digest for path,digest in result['source_inputs'].items()):raise ValueError('source inputs changed')
    save(out/'enhancement-proof.json',proof)
    result['enhancement_proof']=dict(file='enhancement-proof.json',sha256=sha(out/'enhancement-proof.json'))
    save(out/'foliage.json',result)
    save(a.evidence/'verification.json',dict(schema='wt_foliage_enhancement_preparation_v1',status='passed',
        definition=bind(out/'foliage.json',repo),proof=bind(out/'enhancement-proof.json',repo),
        original_definition=bind(original,repo),source_level_sha256=base['source_level_sha256'],
        models=10,layers=16,weight_textures=4,hard_overlap_pixels=0,
        short_grass_area_m2=proof['short_grass_area_m2'],samples=samples,
        scope=proof['scope'],native_render_status=proof['native_render_status']))
    # Spatial comparison is measured source data, not a synthetic scene preview.
    preview=Image.new('RGB',(1536,512));old=np.zeros((SIZE,SIZE),np.uint8)
    for item in base['weight_textures']: old=np.maximum(old,np.asarray(Image.open(checked_file(original.parent,item))).max(axis=2))
    for k,arr in enumerate((old,fields[13],hard.astype(np.uint8)*255)):
        preview.paste(Image.fromarray(arr).convert('RGB').resize((512,512)),(512*k,0))
    preview.save(a.evidence/'coverage-before-after-hard.png')
    print(json.dumps(dict(status='passed',definition_sha256=sha(out/'foliage.json'),proof_sha256=sha(out/'enhancement-proof.json'),
        short_grass_area_m2=proof['short_grass_area_m2'],samples=samples),indent=2))


if __name__=='__main__':
    main()
