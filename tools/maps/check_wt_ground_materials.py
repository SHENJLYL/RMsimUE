"""Offline source and numeric preflight for Flanders ground refinement; no Unreal."""
import argparse
import ast
import hashlib
import json
import math
from pathlib import Path
import re
import sys

import numpy as np
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'unreal'))
import wt_ground_microdetails as micro
import wt_height_decals as height
import wt_landdetail as landdetail


def sha(path):
    return micro.digest(Path(path))


def unit(v):
    v=np.asarray(v,dtype=np.float64)
    return v/np.linalg.norm(v)


def rnm(a,b):
    t=np.asarray(a)+[0.,0.,1.]
    u=np.asarray(b)*[-1.,-1.,1.]
    return unit(t*np.dot(t,u)/t[2]-u)


def decode(payload,mask):
    clear=128/255
    coverage=min(1.,max(0.,(mask-clear)/(1-clear)))
    if coverage<=0:
        return 0.,0.,0.
    recovered=max((payload-clear*(1-coverage))/max(coverage,1.e-8),0.)
    valid=min(1.,max(0.,recovered*13))
    index=min(11.,max(0.,recovered*13-1))
    return index,coverage*valid,coverage


def check(source_file,output,engine_root):
    source_file=Path(source_file).resolve(strict=True)
    package=source_file.parent
    project=Path(__file__).resolve().parents[2]
    output=Path(output).resolve()
    output.mkdir(parents=True,exist_ok=False)
    source=json.loads(source_file.read_text(encoding='utf-8-sig'))
    data=micro.validate(package)
    checks=[]
    def require(name,passed,**facts):
        checks.append(dict(name=name,passed=bool(passed),**facts))
        if not passed:
            raise ValueError(name)
    report=dict(schema='wt_ground_refinement_preflight_v1',status='running',native_run=False,
        rendered_quality_verified=False,source_manifest_sha256=sha(source_file),
        source_definition_sha256=micro.DEFINITION_SHA256,source_proof_sha256=data['proof_sha256'],
        checks=checks,scripts_sha256={},source_files={},limits=[
            'Source-backed UE post-RVT application; the original shipped land consumer is absent from the public checkout.',
            'The R5 uniform quantization exercise is a numeric sensitivity model, not an actual BC3 codec or a rendered stability measurement.',
            'Native shader, serialized pin, texture policy, array and GrassOutput closure gates must run after authoring.'])
    try:
        scripts=('wt_materials.py','wt_height_decals.py','wt_ground_microdetails.py',
                 'wt_ground_material_verify.py','update_wt_materials.py','verify_wt_materials.py',
                 'wt_landdetail.py','wt_material_refine_verify.py','wt_lc_reflectance.py')
        for name in scripts:
            path=project/'tools/unreal'/name
            ast.parse(path.read_text(encoding='utf-8'),filename=str(path))
            report['scripts_sha256'][name]=sha(path)
        report['scripts_sha256'][Path(__file__).name]=sha(__file__)
        require('production_scripts_parse',True,files=len(scripts))
        local_data=landdetail.validate(package,source)
        local_code=landdetail.shader_code(local_data)
        require('lc1_fixed_source_local_derivatives_before_blending',
                local_code.index('worldDdx=ddx(')<local_code.index('int ids[4]')
                and 'ddx(height)' not in local_code and 'ddy(height)' not in local_code
                and local_code.count('Texture2DArraySampleGrad(Details,DetailsSampler,')==3
                and 'layerNormal[layer]+=localNormals[k]*weights[k]' in local_code
                and 'NormalOut=normalize(layerNormal[0]*layerWeights.x+layerNormal[1]*layerWeights.y)' in local_code,
                adapter=landdetail.NORMAL_ADAPTER,source_definition_sha256=sha(package/landdetail.DEFINITION_FILE))
        extraction=Path(source['extraction_root'])
        if not extraction.is_absolute():extraction=(package/extraction).resolve()
        inventory_file=sorted(extraction.glob('textures-*/result.json'))[-1]
        inventory={item['name']:item for item in json.loads(inventory_file.read_text(encoding='utf-8'))['exported']}
        specs=[s for s in source['materials'] if s['shader']==height.SHADER]
        contracts=[height.contract(s,data) for s in specs]
        require('original_height_material_layout',len(specs)==34,material_ids=[s['id'] for s in specs],
                source_slots=dict(diffuse=0,normal=None,reflection=2),microdetail_slots=12)
        require('explicit_ue_height_gradient_strength',height.SOURCE_BUMP_SCALE==.08
                and height.HEIGHT_NORMAL_STRENGTH==.2
                and all(c['ue_normal_adapter']==height.normal_adapter() for c in contracts),
                source_bump_scale=height.SOURCE_BUMP_SCALE,ue_adapter=height.normal_adapter(),
                source_metres=False)
        textures={}
        for contract in contracts:
            for key in ('source_diffuse','source_reflection'):
                name=contract[key]
                if name in textures:continue
                entry=inventory[name]
                path=package/'textures'/(name+'.png')
                require('unchanged_original_'+name,sha(path)==entry['sha256']==sha(entry['file']))
                textures[name]=dict(sha256=entry['sha256'],size=entry['size'])
        report['original_height_textures']=textures
        report['source_contract_example']=contracts[0]
        polarity=[height.height_polarity_contract(s) for s in specs]
        active=[item for item in polarity if item['adapter_active']]
        require('height_polarity_only_18_reviewed_source_ids',
                len(active)==18 and {item['material_id'] for item in active}==set(height.HEIGHT_POLARITY_ADAPTERS)
                and all(item['value']==1 for item in active)
                and all(item['value']==0 for item in polarity if not item['adapter_active']),
                source_adapters=polarity,retained_original_defaults=16)
        require('all_18_polarity_bindings_keep_original_png_bytes',
                all(textures[item['adapter']['diffuse']]['sha256']==item['adapter']['diffuse_sha256']
                    and textures[item['adapter']['reflection']]['sha256']==item['adapter']['reflection_sha256']
                    for item in active))
        approved=next(s for s in specs if s['id']=='WT_e82be167172b929d5356')
        overrides=[]
        for declared in (0,1):
            changed=dict(approved,parameters=approved['parameters']+'\nheight_above='+str(declared))
            got=height.height_polarity_contract(changed)
            overrides.append(got['value']==declared and got['source_explicit'] and not got['adapter_active'])
        wrong=dict(approved,texture_slots=list(approved['texture_slots']))
        wrong['texture_slots'][0]='wrong_source_tex_d*'
        rejected=False
        try:
            height.height_polarity_contract(wrong)
        except ValueError:
            rejected=True
        require('explicit_height_above_wins_and_false_source_binding_rejected',all(overrides) and rejected)
        require('same_shader_cdk_barg_cooked_layout',
                height.COOKED_REFLECTION_LAYOUT['build_swizzle_argb']=='BARG'
                and height.COOKED_REFLECTION_CHANNELS==dict(smoothness='R',reflectance='G',ambient_occlusion='B',microdetail_id='A')
                and all(c['reflection_channels']==height.COOKED_REFLECTION_CHANNELS
                        and c['cooked_reflection_layout']==height.COOKED_REFLECTION_LAYOUT for c in contracts),
                source_layout=height.COOKED_REFLECTION_LAYOUT,source_reflectance_and_ao_rendered=False)
        # Distinct source bytes demonstrate the exporter ARGB destination order independently
        # of the material's two consumer expressions.
        packing=[]
        for original in [(17,29,46,71),(0,255,23,128),(255,0,255,0)]:
            source_channels=dict(zip('RGBA',original))
            cooked=dict(zip('ARGB',[source_channels[x] for x in 'BARG']))
            packing.append(dict(source_rgba=list(original),cooked_rgba=[cooked[x] for x in 'RGBA'],
                smoothness=cooked['R'],reflectance=cooked['G'],ambient_occlusion=cooked['B'],microdetail_id=cooked['A']))
        require('barg_preserves_each_original_semantic_channel',
                all(item['smoothness']==item['source_rgba'][3] and item['reflectance']==item['source_rgba'][0]
                    and item['ambient_occlusion']==item['source_rgba'][1] and item['microdetail_id']==item['source_rgba'][2]
                    for item in packing),cases=packing)
        clear=128/255
        errors=[]
        for coverage in (1.,.5,.1,.01,.001):
            for index in range(12):
                payload=(index+1)/13
                result,strength,actual_coverage=decode(payload*coverage+clear*(1-coverage),coverage+clear*(1-coverage))
                errors.append(max(abs(result-index),abs(strength-coverage),abs(actual_coverage-coverage)))
        require('native_once_premultiplied_payload_roundtrip',max(errors)<2.e-11,
                cases=len(errors),max_absolute_error=max(errors))
        require('zero_coverage_ignores_clear_payload',decode(clear,clear)==(0.,0.,0.))
        invalid=[decode(clear*(1-a),a+clear*(1-a))[1] for a in (1.,.5,.1,.01)]
        require('unverified_zero_payload_has_zero_strength',max(invalid)<1.e-13,maximum_strength=max(invalid))
        overlap=[]
        for top,bottom,a,b in ((2.5,9.25,.3,.7),(11.,0.,.5,.5),(1.,10.,.1,1.)):
            coverage=a+b*(1-a)
            raw=(top+1)/13*a+(bottom+1)/13*b*(1-a)+clear*(1-coverage)
            index,strength,_=decode(raw,coverage+clear*(1-coverage))
            wanted=(top*a+bottom*b*(1-a))/coverage
            overlap.append(abs(index-wanted))
        require('overlapping_source_ids_blend_continuously',max(overlap)<1.e-12,max_error=max(overlap))
        slices=[np.asarray(Image.open(package/'ground-microdetails'/s['texture_file']),dtype=np.float64)/255 for s in data['slots']]
        epsilon=1.e-7
        worst=0.
        def sampled(index,pixel):
            low=int(math.floor(index));high=min(11,low+1);part=index-low
            return slices[low][pixel]*(1-part)+slices[high][pixel]*part
        for pixel in ((37,109),(211,397),(443,61)):
            for boundary in range(1,11):
                left=sampled(boundary-epsilon,pixel);right=sampled(boundary+epsilon,pixel)
                worst=max(worst,float(np.max(np.abs(left-right))))
        require('actual_source_slice_interpolation_has_no_integer_jump',worst<2*epsilon,
                asymmetric_pixel_points=3,boundaries=10,delta_index=2*epsilon,max_rgba_delta=worst)
        quant=[]
        for coverage in (1.,.5,.1,.01):
            largest=0.;max_strength=0.
            for index in np.linspace(0,11,133):
                raw=(index+1)/13*coverage+clear*(1-coverage)
                quantized=round(raw*31)/31
                result,strength,actual_coverage=decode(quantized,coverage+clear*(1-coverage))
                require_ok=0<=strength<=actual_coverage+1.e-12
                if not require_ok:raise ValueError('quantized payload exceeded its coverage')
                largest=max(largest,abs(result-index));max_strength=max(max_strength,strength)
            quant.append(dict(coverage=coverage,maximum_index_error=largest,maximum_strength=max_strength))
        require('quantization_effect_is_coverage_bounded',True,model='R5 uniform nearest; exact mask',
                cases=532,results=quant)
        gradients=[]
        slope=np.array([.31,-.27])
        for dx,dy in (([.01,0],[0,.02]),([-.01,0],[0,.02]),([.01,.007],[-.004,.02])):
            sx=np.array([*dx,0.]);sy=np.array([*dy,0.]);up=np.array([0.,0.,1.])
            r1=np.cross(sy,up);r2=np.cross(up,sx);det=np.dot(sx,r1)
            dhs=np.dot(slope,dx)*.08;dht=np.dot(slope,dy)*.08
            actual=unit(abs(det)*up-np.sign(det)*(dhs*r1+dht*r2))
            wanted=unit([-.08*slope[0],-.08*slope[1],1.])
            gradients.append(float(np.max(np.abs(actual-wanted))))
        require('height_gradient_matches_analytic_plane_under_uv_rotation_and_reflection',max(gradients)<1.e-14,
                cases=len(gradients),max_error=max(gradients),strength_domain='source UV height, not displaced geometry')
        scaled_errors=[]
        for degrees in (0.,15.,33.484358559182475,57.10201055031914,61.14712778013246,68.83304858121599,84.75169089002712):
            theta=math.radians(degrees)
            actual=unit([math.sin(theta)*height.HEIGHT_NORMAL_STRENGTH,0,math.cos(theta)])
            expected_angle=math.atan(height.HEIGHT_NORMAL_STRENGTH*math.tan(theta))
            scaled_errors.append(abs(math.acos(float(actual[2]))-expected_angle))
        require('ue_gradient_strength_preserves_direction_and_source_scale',max(scaled_errors)<1.e-13,
                ue_strength=height.HEIGHT_NORMAL_STRENGTH,source_bump_scale=height.SOURCE_BUMP_SCALE,
                max_radian_error=max(scaled_errors),extra_clamp=False)
        n=unit([.35,-.2,1])
        require('receiver_rnm_neutral_normal_preserves_slope',float(np.max(np.abs(rnm(n,[0,0,1])-n)))<1.e-14)
        require('flat_receiver_preserves_microdetail_normal',float(np.max(np.abs(rnm([0,0,1],n)-n)))<1.e-14)
        wrong={}
        for name in ('decal_road_rail_gravel_a_tex_r','river_sand_gravel_coast_border_tex_r'):
            rgba=np.asarray(Image.open(package/'textures'/(name+'.png')),dtype=np.float64)/255
            xy=rgba[:,:,[3,1]]*2-1
            z=np.sqrt(np.maximum(1-np.sum(xy*xy,axis=2),0))
            wrong[name]=dict(source_sha256=sha(package/'textures'/(name+'.png')),
                old_wrong_ag_normal_zero_z_fraction=float(np.mean(z==0)),
                old_wrong_ag_normal_z_below_point_two_fraction=float(np.mean(z<.2)),
                channels_rgba_mean=(rgba.mean(axis=(0,1))*255).tolist())
        report['old_reflection_as_normal_failure']=wrong
        require('height_shader_uses_alpha_gradient_and_cooked_reflection_r_a','reflection.ag' not in height.HEIGHT_DECAL_CODE
                and '1-reflection.r' in height.HEIGHT_DECAL_CODE and 'reflection.a*HeightSource.y' in height.HEIGHT_DECAL_CODE
                and 'up-gradient*HeightNormalStrength' in height.HEIGHT_DECAL_CODE)
        engine=Path(engine_root).resolve(strict=True)
        paths={
            'ue_rvt_writer':'Engine/Shaders/Private/VirtualTextureMaterial.usf',
            'ue_rvt_compression':'Engine/Shaders/Private/VirtualTextureCompress.usf',
            'ue_rvt_blend_clear':'Engine/Source/Runtime/Renderer/Private/VT/RuntimeVirtualTextureRender.cpp',
            'ue_clear_constant':'Engine/Source/Runtime/RHI/Private/RHI.cpp',
            'ue_specular_read':'Engine/Source/Runtime/Engine/Private/Materials/MaterialExpressions.cpp',
            'ue_array_sample_grad':'Engine/Shaders/Private/Common.ush'}
        for label,relative in paths.items():
            path=engine/relative
            report['source_files'][label]=dict(path=str(path),sha256=sha(path))
        native=(engine/paths['ue_rvt_writer']).read_text(encoding='utf-8')
        require('ue_raw_specular_is_multiplied_once',bool(re.search(
            r'float4\(\s*Specular,\s*Roughness,\s*PackedNormal\.z,\s*1(?:\.f|\.0f|\.0)?\s*\)\s*\*\s*Opacity',native)))
        code=micro.shader_code(data)
        post_contract=json.loads(micro.post_adapter_metadata(data,source_connected=True))
        require('microdetail_mip_gradients_outside_all_validity_branches',
                code.index('ddx(uv)')<code.index('if(coverage>0)')
                and code.index('ddx(uv)')<code.index('if(totalStrength<=0)')
                and code.count('Texture2DArraySampleGrad(')==1,
                logical_texture_reads=post_contract['texture_reads'])
        require('lc_and_rvt_ids_are_separately_sampled_before_effect_blending',
                'float surfaceIndices[2]={baseIndex,decalIndex};' in code
                and 'float surfaceStrengths[2]={(1-coverage)*baseValid,coverage*decalValid};' in code
                and post_contract['interpolation']=='independent_floor_next_within_each_surface'
                and post_contract['texture_reads']['maximum']==4
                and post_contract['source_connected'] is True
                and set(post_contract['inputs'])==set(micro.POST_INPUTS),post_adapter=post_contract)
        weight_cases=[]
        for coverage in (0.,.1,.5,1.):
            for base_valid in (0.,.25,1.):
                for decal_valid in (0.,.5,1.):
                    weights=[(1-coverage)*base_valid,coverage*decal_valid]
                    weights.append(1-sum(weights))
                    weight_cases.append(weights)
        require('independent_surface_and_neutral_weights_are_finite_partition',
                all(min(w)>=0 and abs(sum(w)-1)<1.e-12 for w in weight_cases),
                cases=len(weight_cases))
        backup=project/'artifacts/maps/wt-refine-20261118/ground-refine-01/backup.json'
        report['prior_asset_backup']=dict(path=str(backup),sha256=sha(backup))
        report['visual_baselines']={}
        for relative in ('gui-wall-03-reset/entry.png','gui-motion-before-01-frames/frames.json',
                         'contact-flanders-01/contact-rest/overview.png'):
            baseline=project/'artifacts/maps/wt-refine-20261118'/relative
            if baseline.is_file():report['visual_baselines'][relative]=sha(baseline)
        report['planned_native_scope']=dict(parent_count=4,instance_count=36,height_instances=34,
            retained_grass_layers=16,full_material_reload_expected=307,world_mutation=False)
        report.update(status='passed',check_count=len(checks))
    except Exception as error:
        report.update(status='failed',error=str(error),check_count=len(checks))
        raise
    finally:
        (output/'verification.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(dict(status=report['status'],checks=len(checks),report=str(output/'verification.json'))))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--scene-source',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--engine-root',required=True)
    args=parser.parse_args()
    check(args.scene_source,args.output,args.engine_root)
