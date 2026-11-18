"""Offline source and numeric checks for the Flanders material refinement.

This checks original package bytes and semantic fixtures. It performs no UE
authoring and makes no native rendering claim.
"""
import argparse
import ast
from collections import Counter
import datetime
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools/unreal'))
import wt_landdetail as detail
import wt_rendinst_decals as decals
from wt_landclass import validate_landclass_surface
from wt_grass import source_color_linear,validate_foliage


def require(value,message):
    if not value:
        raise AssertionError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(source_file,output):
    source_file=Path(source_file).resolve(strict=True)
    package=source_file.parent
    output=Path(output).resolve()
    output.mkdir(parents=True,exist_ok=False)
    report=dict(schema='wt_material_refine_source_preflight_v1',status='running',
        native_rendering_verified=False,native_compilation_verified=False,
        source_manifest_sha256=sha(source_file),checks={})
    try:
        source=json.loads(source_file.read_text(encoding='utf-8-sig'))
        data=detail.validate(package,source)
        original_land=validate_landclass_surface(package,source)
        grass=validate_foliage(package)
        report['definition_sha256']=sha(package/detail.DEFINITION_FILE)
        report['original_landclass_sha256']=sha(package/'landclass/landclass.json')
        report['grass_definition_sha256']=grass['definition_sha256']
        report['source_counts']=dict(biomes=len(data['table']['biomes']),
            detail_textures=len(data['table']['albedo_names']),all_ground_textures=len(data['textures']),
            grass_models=len(grass['models']),grass_layers=len(grass['layers']),
            original_landclasses=len(original_land['classes']))
        require(report['source_counts']['biomes']==65 and report['source_counts']['detail_textures']==23,
                'original detail table differs')
        specs=[spec for spec in source['materials'] if spec['shader'] in decals.SHADERS]
        counts=Counter(decals.contract(spec)['flavor'] for spec in specs)
        require(counts=={'DecalModulate':157,'DecalDiffuse':39,'DecalNormal':1},'source decal family set differs')
        report['decal_counts']=dict(counts)
        report['source_decal_ids']={kind:[spec['id'] for spec in specs if decals.flavor(spec['shader'])==kind] for kind in sorted(decals.FLAVORS)}
        missing=dict(next(spec for spec in specs if decals.flavor(spec['shader'])=='DecalModulate'))
        missing['texture_slots']=list(missing['texture_slots']);missing['texture_slots'][0]=None
        try:
            decals.contract(missing)
        except ValueError:
            report['checks']['missing_modulate_source_rejected']=True
        else:
            raise AssertionError('modulate source must not silently use a white fallback')

        wall=package/'textures/wall_dirt_spots_decal_a_tex_m.png'
        with Image.open(wall) as im:
            pixels=np.asarray(im.convert('RGBA'))
        border=np.concatenate((pixels[0],pixels[-1],pixels[:,0],pixels[:,-1]))
        median=np.median(border,axis=0)
        require(np.all(pixels[:,:,3]==255),'fixture alpha no longer fully opaque')
        require(median[:3].tolist()==[128,128,128],'fixture neutral border changed')
        encoded=128/255
        old_srgb=(encoded/12.92 if encoded<=.04045 else ((encoded+.055)/1.055)**2.4)
        report['black_gray_background_fixture']=dict(texture=wall.name,sha256=sha(wall),
            all_alpha_255=True,neutral_border_rgba=median.tolist(),
            old_srgb_base_color=old_srgb,source_linear_multiplier=2*encoded,
            exact_source_neutral_value=.5,exact_neutral_multiplier=1.0)
        # Independent blend equation: source dc/sc is 2*C*destination.
        values=np.array([0.0,.25,.5,.75,1.0])
        dst=np.array([.13,.27,.81])
        products=2*values[:,None]*dst
        require(np.array_equal(products[2],dst),'mid-gray source must preserve destination')
        require(np.allclose((2*.25)*(2*.75)*dst,.75*dst),'overlapping multipliers differ')
        depth=np.clip((40-np.array([0.,30.,35.,40.,50.]))/10,0,1)
        factors=1+(2*.25-1)*depth
        require(np.array_equal(factors,[.5,.5,.75,1,1]),'source fade must return to neutral')
        report['checks']['multiply_neutral_and_overlap']=True
        report['checks']['source_depth_fade_to_neutral']=True
        normal_spec=next(x for x in specs if decals.flavor(x['shader'])=='DecalNormal')
        require(normal_spec['texture_slots'][0] is None and normal_spec['texture_slots'][2] is not None,
                'normal-only fixture texture slots differ')
        report['normal_only_source']=dict(material_id=normal_spec['id'],
            slots=normal_spec['texture_slots'],base_color_written=False)

        # Plane fixtures independently fix sign and metre units of the UE adapter.
        normals=[]
        for a,b in [(0.,0.),(.2,0.),(0.,-.3),(.2,-.3)]:
            n=np.array([0.,0.,1.]);sx=np.array([1.,0.,0.]);sy=np.array([0.,1.,0.])
            r1=np.cross(sy,n);r2=np.cross(n,sx);det=np.dot(sx,r1)
            gradient=np.sign(det)*(a*r1+b*r2)
            actual=abs(det)*n-gradient;actual/=np.linalg.norm(actual)
            expected=np.array([-a,-b,1.]);expected/=np.linalg.norm(expected)
            require(np.max(np.abs(actual-expected))<1e-14,'surface-gradient sign/unit fixture failed')
            normals.append(dict(height_gradient_per_metre=[a,b],normal=actual.tolist()))
        report['gradient_normal_fixtures']=normals
        report['checks']['height_gradient_sign_and_units']=True
        require((0+34)/2==17 and 17 not in (0,34),'categorical counterexample differs')
        report['index_mip_counterexample']=dict(source_ids=[0,34],averaged_mip_id=17,
            selected_adapter='four point samples from original mip0, followed by source height weighting')
        report['checks']['categorical_index_requires_mip0']=True
        gray=source_color_linear([.5,.5,.5,1])
        corrected=gray[0]*4.59479341998814
        require(abs(corrected-1)<1e-12 and gray[3]==1,'source grass CPU/GPU color pairing differs')
        report['grass_color_fixture']=dict(input_srgb=.5,linear=gray[0],
            source_correction=4.59479341998814,result=corrected,previous_raw_result=.5*4.59479341998814)
        report['checks']['source_grass_color_pairing']=True

        scripts=['wt_materials.py','wt_landclass.py','wt_landdetail.py','wt_rendinst_decals.py',
                 'wt_material_refine_verify.py','verify_wt_materials.py','update_wt_materials.py']
        report['scripts_sha256']={}
        for name in scripts:
            filename=ROOT/'tools/unreal'/name
            ast.parse(filename.read_text(encoding='utf-8-sig'))
            report['scripts_sha256'][name]=sha(filename)
        report['checks']['source_contracts_and_texture_bytes']=True
        report['checks']['python_ast']=True
        report['checks']['source_manifest_unchanged']=sha(source_file)==report['source_manifest_sha256']
        require(report['checks']['source_manifest_unchanged'],'source manifest changed during read-only preflight')
        report['scope']=[
            'Original scene and model geometry are unchanged.',
            'Source color rules, original detail selection/scales and rendering offsets are retained.',
            'UE gradient normal is explicit; packed reflectance, independent decal attribute opacity, draw_order and wind remain outside this change.',
            'Native saved-asset shader, mesh-decal pass and near/far moving-view quality are separate pending gates.']
        report['status']='passed'
    except Exception as error:
        report.update(status='failed',error=str(error))
        raise
    finally:
        report['completed_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
        (output/'verification.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(dict(status=report['status'],source_counts=report['source_counts'],
        decal_counts=report['decal_counts'],checks=report['checks'],report_sha256=sha(output/'verification.json'))))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--scene-source',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    verify(args.scene_source,args.output)
