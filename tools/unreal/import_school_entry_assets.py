"""Import CC0 entry walls and a continuous distant-ground material in isolation.

Runs inside the full UE editor. Writes only SchoolEntry assets; the existing
school, source materials, competition boundary and layout are not modified.
"""
from pathlib import Path
import hashlib, importlib.util, json, math, traceback
import unreal

ROOT = Path(__file__).resolve().parents[2]
BASE = '/Game/RMSim/Maps/rmuc2027/Arena/SchoolEntry'
FIDELITY = '/Game/RMSim/Maps/rmuc2027/Arena/SchoolGymFidelity'
def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value
I = module('entry_mesh_transport', 'tools/unreal/import_school_gym_assets.py')
F = module('entry_fidelity_materials', 'tools/unreal/school_fidelity_materials.py')
V = module('entry_geometry_verifier', 'tools/unreal/verify_school_fidelity_geometry.py')
I.BASE = F.BASE = BASE
V.ASSET_PREFIX = BASE + '/'
P = F.PBR
def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def xyz(v):
    return [float(v.x), float(v.y), float(v.z)]
def norm(v):
    d = math.sqrt(sum(x*x for x in v))
    if d < 1.e-12: raise ValueError('Degenerate tangent')
    return [x/d for x in v]
def custom(material, code, inputs, output_type, y):
    descriptors = []
    for name in inputs:
        value = unreal.CustomInput()
        value.set_editor_property('input_name', name)
        descriptors.append(value)
    return P.expr(material, unreal.MaterialExpressionCustom, y=y, code=code,
                  inputs=descriptors, output_type=output_type,
                  description='Continue the original school grass UVs in world space')

def distant_ground(report):
    mesh = unreal.load_asset(FIDELITY + '/Meshes/RMExteriorGround.RMExteriorGround')
    if not isinstance(mesh, unreal.StaticMesh): raise RuntimeError('Original ground is missing')
    diagnostics=json.loads(unreal.MapImportLibrary.describe_map_mesh(mesh))
    section_ids = [i for i,s in enumerate(diagnostics['sections'])
                   if s['material_slot'] == 'ExtGrass']
    if len(section_ids) != 1: raise RuntimeError('Expected one original ExtGrass section')
    vertices, indices, normals, uvs, tangents = unreal.ProceduralMeshLibrary.get_section_from_static_mesh(mesh, 0, section_ids[0])
    if len(vertices) < 3 or len(indices) < 3: raise RuntimeError('Original grass has no triangles')
    points = [[v.x*.01,v.y*.01,v.z*.01] for v in vertices]
    uv = [[float(v.x),float(v.y)] for v in uvs]
    # ExtGrass is a solid source box with different UVs on its bottom and sides.
    # Continue only the visible upper grass surface, preserving its actual UVs.
    grass_top=max(points[int(k)][2] for k in indices)
    top_triangles=[]
    for i in range(0,len(indices),3):
        ids=[int(v) for v in indices[i:i+3]]
        if all(abs(points[k][2]-grass_top)<1.e-7 for k in ids):top_triangles.append(ids)
    if len(top_triangles)!=2:raise RuntimeError('Expected two upper grass-surface triangles')
    top_vertices=sorted({k for tri in top_triangles for k in tri})
    triangle = None
    for ids in top_triangles:
        a,b,c = [points[k] for k in ids]
        det=(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
        if abs(det)>1.e-8:
            triangle=(ids,a,b,c,det);break
    if triangle is None: raise RuntimeError('Original grass is not planar XY geometry')
    ids,a,b,c,det=triangle
    coeff=[]
    for channel in range(2):
        f0,f1,f2=[uv[k][channel] for k in ids]
        ax=((f1-f0)*(c[1]-a[1])-(f2-f0)*(b[1]-a[1]))/det
        ay=((b[0]-a[0])*(f2-f0)-(c[0]-a[0])*(f1-f0))/det
        coeff.append([ax,ay,f0-ax*a[0]-ay*a[1]])
    error=max(abs(sum(coeff[j][k]*points[i][k] for k in range(2))+coeff[j][2]-uv[i][j])
              for i in top_vertices for j in range(2))
    if error > 2.e-6: raise RuntimeError('Original upper grass UVs are not affine: '+str(error))
    zspread=max(points[i][2] for i in top_vertices)-min(points[i][2] for i in top_vertices)
    if zspread > 1.e-5: raise RuntimeError('Expected a planar original grass section')
    bounds=mesh.get_bounds()
    local_center=[bounds.origin.x*.01,bounds.origin.y*.01]
    layout_path=ROOT/'assets/maps/rmuc2027/arena/sketchupbox-school-gym/layout.json'
    layout=json.loads(layout_path.read_text(encoding='utf-8-sig'))
    group=next(g for g in layout['groups'] if g['mesh']=='RMExteriorGround')
    rows=group['instances']
    if len(rows)!=4 or any(r['rotation_deg']!=[0,0,0] for r in rows):
        raise ValueError('Expected four unrotated original ground tiles')
    centers=[[float(r['center'][0]),-float(r['center'][1])] for r in rows]
    origin=[sum(p[k] for p in centers)/4 for k in range(2)]
    hx=max(abs(p[0]-origin[0]) for p in centers)
    hy=max(abs(p[1]-origin[1]) for p in centers)
    if abs(hx-40)>1.e-5 or abs(hy-35)>1.e-5:
        raise ValueError('Unexpected original grass tile placement')
    dimensions=[float(bounds.box_extent.x)*.02,float(bounds.box_extent.y)*.02]
    if abs(dimensions[0]-80)>.001 or abs(dimensions[1]-70)>.001:
        raise ValueError('Original ground scale changed')
    mat=F._new_material('M_ExteriorDistantGround')
    mat.set_editor_property('tangent_space_normal',False)
    # Preserve the four original UV phases at the source-ground boundary.
    # Outside it, blend sampled colors/normals across quadrant seams rather
    # than blending UVs, which would stretch or fold the original grass image.
    half_extent=[hx*2,hy*2]
    weight_code=(
      f'float2 W=WorldPosition.xy*0.01;\n'
      f'float2 O=float2({origin[0]:.17g},{origin[1]:.17g});\n'
      f'float2 Q=W-O;\n'
      f'float d=max(max(abs(Q.x)-{half_extent[0]:.17g},abs(Q.y)-{half_extent[1]:.17g}),0.0);\n'
      f'float f=min(d,16.0);\n'
      f'if(f<0.00001) return float2(Q.x>=0.0?1.0:0.0,Q.y>=0.0?1.0:0.0);\n'
      f'return smoothstep(float2(-f,-f),float2(f,f),Q);')
    wp=P.expr(mat,unreal.MaterialExpressionWorldPosition,y=-600)
    weights=custom(mat,weight_code,['WorldPosition'],unreal.CustomMaterialOutputType.CMOT_FLOAT2,-600)
    P.connect(wp,weights,'WorldPosition')
    wx=P.expr(mat,unreal.MaterialExpressionComponentMask,r=True,g=False,b=False,a=False,y=-500)
    wy=P.expr(mat,unreal.MaterialExpressionComponentMask,r=False,g=True,b=False,a=False,y=-400)
    P.connect(weights,wx);P.connect(weights,wy)
    color_asset=FIDELITY+'/Textures/T_Grass005_Color_Color.T_Grass005_Color_Color'
    normal_asset=FIDELITY+'/Textures/T_Grass005_NormalDX_Normal.T_Grass005_NormalDX_Normal'
    textures=[unreal.load_asset(path) for path in (color_asset,normal_asset)]
    if not all(isinstance(t,unreal.Texture2D) for t in textures):
        raise RuntimeError('Original grass textures cannot be loaded')
    uv_codes=[];color_nodes=[];normal_nodes=[]
    for sy in (-1.,1.):
        for sx in (-1.,1.):
            center=[origin[0]+sx*hx,origin[1]+sy*hy]
            code=(f'float2 L=WorldPosition.xy*0.01-float2({center[0]:.17g},{center[1]:.17g})'
                  f'+float2({local_center[0]:.17g},{local_center[1]:.17g});\n'
                  f'return float2(dot(float3(L,1),float3({",".join(format(v,".17g") for v in coeff[0])})),'
                  f'dot(float3(L,1),float3({",".join(format(v,".17g") for v in coeff[1])})));')
            uv_codes.append(code)
            uvnode=custom(mat,code,['WorldPosition'],unreal.CustomMaterialOutputType.CMOT_FLOAT2,100*len(uv_codes))
            P.connect(wp,uvnode,'WorldPosition')
            color_sample=F._sample(mat,textures[0],'Color',100*len(uv_codes))
            normal_sample=F._sample(mat,textures[1],'Normal',400+100*len(uv_codes))
            P.connect(uvnode,color_sample,'UVs');P.connect(uvnode,normal_sample,'UVs')
            color_nodes.append(color_sample);normal_nodes.append(normal_sample)
    def mix(a,b,weight,y):
        value=P.expr(mat,unreal.MaterialExpressionLinearInterpolate,y=y)
        P.connect(a,value,'A');P.connect(b,value,'B');P.connect(weight,value,'Alpha')
        return value
    color=mix(mix(color_nodes[0],color_nodes[1],wx,900),
              mix(color_nodes[2],color_nodes[3],wx,1000),wy,1100)
    normal=mix(mix(normal_nodes[0],normal_nodes[1],wx,1200),
               mix(normal_nodes[2],normal_nodes[3],wx,1300),wy,1400)
    prepared=json.loads((ROOT/'assets/maps/rmuc2027/arena/sketchupbox-school-gym/ue-import-05-fidelity/prepared.json').read_text(encoding='utf-8-sig'))
    source=prepared['materials']['ExtGrass']
    factor=source['base_color_factor']
    P.property_input(P.color_tint(mat,color,factor[:3]),unreal.MaterialProperty.MP_BASE_COLOR)
    P.property_input(P.scalar(mat,float(source['roughness_factor']),150),unreal.MaterialProperty.MP_ROUGHNESS)
    P.property_input(P.scalar(mat,float(source['metallic_factor']),230),unreal.MaterialProperty.MP_METALLIC)
    # Use the actual native UV derivatives, independent of cube-face tangents.
    au,bu,_=coeff[0];av,bv,_=coeff[1];d=au*bv-bu*av
    if abs(d)<1.e-12:raise ValueError('Degenerate original grass UV mapping')
    t=norm([bv/d,-av/d,0.]);bvec=norm([-bu/d,au/d,0.])
    ncode=(f'return normalize(N.x*float3({",".join(format(v,".17g") for v in t)})'
           f'+N.y*float3({",".join(format(v,".17g") for v in bvec)})+N.z*float3(0,0,1));')
    nnode=custom(mat,ncode,['N'],unreal.CustomMaterialOutputType.CMOT_FLOAT3,450)
    P.connect(normal,nnode,'N');P.property_input(nnode,unreal.MaterialProperty.MP_NORMAL)
    F._save_material(mat)
    record={'asset':mat.get_path_name(),'original_ground_asset':mesh.get_path_name(),
            'original_ground_layout_sha256':sha(layout_path),'source_textures':[t.get_path_name() for t in textures],
            'original_linear_color_factor':factor,'uv_affine_coefficients_native_local_metres':coeff,
            'uv_affine_max_error':error,'grass_planar_z_spread_m':zspread,
            'grass_top_native_local_z_m':grass_top,'grass_top_source_triangles':len(top_triangles),
            'source_grass_sides_and_bottom_excluded_from_top_mapping':True,
            'native_local_bounds_center_xy_m':local_center,'world_tile_origin_ue_m':origin,
            'tile_center_half_separation_m':[hx,hy],'tangent_world':t,'bitangent_world':bvec,
            'original_textures_or_materials_modified':False,'quadrant_uv_codes':uv_codes,'phase_blend_weight_code':weight_code,
            'phase_crossfade':{'scope':'new distant ground only','maximum_half_width_m':16.,
             'source_edge_distance_zero_retains_original_phase':True,'linear_color_sample_blend':True,
             'decoded_normal_blend_then_world_normalize':True,'uv_coordinates_blended':False}}
    report['distant_ground_material']=record

def main():
    root=I.argument('EntryAssetRoot')
    output=I.argument('VenueImportReport')
    if output.exists():raise FileExistsError(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((root/'prepared.json').read_text(encoding='utf-8-sig'))
    if not manifest['meshes'] or any(not e['asset_name'].startswith('RMExteriorEntry') for e in manifest['meshes']):
        raise ValueError('Entry importer only accepts named entry assets')
    report={'status':'running','asset_root':str(root),'prepared_sha256':sha(root/'prepared.json'),
            'native_base':BASE,'materials':[],'textures':[],'meshes':[],
            'scope':'Independent CC0 entrance assets and distant-ground material only'}
    def save():output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    try:
        cache={};materials={}
        for name,data in manifest['materials'].items():
            materials[name]=F.make_material(name,data,root,manifest,cache,report)
            if data.get('source_occlusion_texture_present') is False:
                # A glTF metallic-roughness map does not assign its R channel to AO.
                P.property_input(P.scalar(materials[name],1.,650),unreal.MaterialProperty.MP_AMBIENT_OCCLUSION)
                F._save_material(materials[name])
                report['materials'][-1]['ambient_occlusion']='constant_1_as_source_has_no_occlusion_texture'
        for entry in manifest['meshes']:
            row=I.import_mesh(entry,root,manifest,materials,full_fallback=True)
            report['meshes'].append(row);save()
        distant_ground(report)
        unreal.SystemLibrary.execute_console_command(None,'Editor.AsyncAssetCompilationFinishAll')
        for entry,row in zip(manifest['meshes'],report['meshes']):
            mesh=unreal.load_asset(row['asset'])
            audit=V.verify_static_mesh_against_obj(mesh,root/entry['file'],
                expected_source_triangles=entry['triangles'],
                report_path=output.parent/(entry['asset_name']+'-geometry.json'))
            row['independent_geometry_status']=audit['status']
            row['native_render_triangles']=audit['native_render_triangles']
            if not unreal.EditorAssetLibrary.save_loaded_asset(mesh):raise RuntimeError('Final mesh save failed')
        report['status']='native_import_and_geometry_passed_pending_scene_visual_qa'
    except Exception as error:
        report.update(status='failed',error=str(error),traceback=traceback.format_exc())
        unreal.log_error('RMSIM_ENTRY_IMPORT_FAILED '+str(error));raise
    finally:
        native=Path(unreal.Paths.project_content_dir()).resolve()/'RMSim/Maps/rmuc2027/Arena/SchoolEntry'
        report['script_sha256']=sha(__file__)
        report['native_files']={p.relative_to(native).as_posix():{'bytes':p.stat().st_size,'sha256':sha(p)}
                                for p in sorted(native.rglob('*.uasset'))}
        save()
    unreal.log('RMSIM_ENTRY_IMPORT_COMPLETE '+str(output))
if __name__=='__main__':main()
