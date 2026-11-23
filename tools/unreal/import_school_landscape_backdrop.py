"""Import a pinned full-landscape backdrop; preserve sky lighting and all audited assets."""
from pathlib import Path
import importlib.util,hashlib,json,math,traceback
import unreal
ROOT=Path(__file__).resolve().parents[2]
def module(name,relative):
 spec=importlib.util.spec_from_file_location(name,ROOT/relative)
 value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
I=module('school_transport','tools/unreal/import_school_gym_assets.py')
F=module('school_fidelity_materials','tools/unreal/school_fidelity_materials.py')
P=F.PBR
SOURCE=ROOT/'assets/maps/rmuc2027/arena/sketchupbox-school-gym/environment-full-fidelity/SOURCE.json'
EXPECTED='d4cbdbc4ff7ea71e7266ae57cdca4e38f3222029250664ec5f374dee6630a079'
ROTATION_DEG=1.6185961034
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 output=I.argument('VenueImportReport')
 if output.exists():raise FileExistsError(output)
 output.parent.mkdir(parents=True,exist_ok=True)
 directory=Path(unreal.Paths.project_content_dir()).resolve()/'RMSim/Maps/rmuc2027/Arena/SchoolGymFidelity'
 before={p.relative_to(directory).as_posix():sha(p) for p in directory.rglob('*.uasset')}
 report={'status':'running','original_native_file_count':len(before),'source':str(SOURCE)}
 try:
  source=json.loads(SOURCE.read_text(encoding='utf-8-sig'))
  path=(SOURCE.parent/source['file']).resolve(strict=True)
  if not path.is_relative_to(SOURCE.parent) or source['id']!='kloofendal_48d_partly_cloudy':raise ValueError('Source identity changed')
  if source['resolution']!=[8192,4096] or sha(path)!=EXPECTED or source['sha256']!=EXPECTED:raise ValueError('Pinned landscape bytes changed')
  if path.stat().st_size!=101363445:raise ValueError('Unexpected source size')
  tex=next((x for x in P.import_asset(path,F.BASE+'/Textures','T_SchoolLandscape8K',factory=unreal.TextureFactory()) if isinstance(x,unreal.TextureCube)),None)
  if tex is None:raise RuntimeError('HDR did not import as TextureCube')
  tex.set_editor_property('srgb',False)
  tex.set_editor_property('compression_settings',unreal.TextureCompressionSettings.TC_HDR)
  tex.set_editor_property('max_texture_size',2048);tex.set_editor_property('lod_bias',0)
  mat=F._new_material('M_SchoolLandscape')
  mat.set_editor_property('shading_model',unreal.MaterialShadingModel.MSM_UNLIT)
  mat.set_editor_property('two_sided',True);mat.set_editor_property('is_sky',True)
  camera=P.expr(mat,unreal.MaterialExpressionCameraVectorWS,x=-1600)
  direction=P.multiply(mat,camera,-1.)
  channels=[]
  for i in range(3):
   mask=P.expr(mat,unreal.MaterialExpressionComponentMask,x=-1200,y=200*i,r=i==0,g=i==1,b=i==2,a=False)
   P.connect(direction,mask);channels.append(mask)
  c=math.cos(math.radians(ROTATION_DEG));s=math.sin(math.radians(ROTATION_DEG))
  def pair(a,b,y):
   node=P.expr(mat,unreal.MaterialExpressionAdd,x=-500,y=y)
   P.connect(a,node,'A');P.connect(b,node,'B');return node
  rx=pair(P.multiply(mat,channels[0],c),P.multiply(mat,channels[1],-s),0)
  ry=pair(P.multiply(mat,channels[0],s),P.multiply(mat,channels[1],c),200)
  xy=P.expr(mat,unreal.MaterialExpressionAppendVector,x=-250)
  P.connect(rx,xy,'A');P.connect(ry,xy,'B')
  xyz=P.expr(mat,unreal.MaterialExpressionAppendVector,x=-100)
  P.connect(xy,xyz,'A');P.connect(channels[2],xyz,'B')
  cube=P.expr(mat,unreal.MaterialExpressionTextureSampleParameterCube,x=100,texture=tex,parameter_name='SchoolLandscape8K',sampler_type=unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR)
  P.connect(xyz,cube)
  brightness=P.expr(mat,unreal.MaterialExpressionScalarParameter,x=100,y=300,parameter_name='EnvironmentBrightness',default_value=512.)
  radiance=P.expr(mat,unreal.MaterialExpressionMultiply,x=400)
  P.connect(cube,radiance,'A','RGB');P.connect(brightness,radiance,'B')
  P.property_input(radiance,unreal.MaterialProperty.MP_EMISSIVE_COLOR)
  F._save_material(mat)
  unreal.SystemLibrary.execute_console_command(None,'Editor.AsyncAssetCompilationFinishAll')
  for obj in [tex,mat]:
   if not unreal.EditorAssetLibrary.save_loaded_asset(obj):raise RuntimeError('Save failed '+obj.get_path_name())
  changed=[name for name,h in before.items() if sha(directory/name)!=h]
  if changed:raise RuntimeError('An existing audited asset changed: '+str(changed))
  report.update(status='imported_preserving_audited_assets_pending_visual_qa',source_sha256=EXPECTED,
   source_resolution=source['resolution'],texture=tex.get_path_name(),material=mat.get_path_name(),
   sample_yaw_rotation_deg=ROTATION_DEG,default_background_brightness=512,
   lighting_cubemap_unchanged=True,original_audited_native_files_unchanged=True,
   cubemap_face_limit=int(tex.get_editor_property('max_texture_size')),
   native_files={p.relative_to(directory).as_posix():{'bytes':p.stat().st_size,'sha256':sha(p)} for p in directory.rglob('*.uasset') if p.relative_to(directory).as_posix() not in before})
 except Exception as e:
  report.update(status='failed',error=str(e),traceback=traceback.format_exc());raise
 finally:
  report['script_sha256']=sha(Path(__file__))
  output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
 unreal.log('RMSIM_SCHOOL_LANDSCAPE_IMPORTED '+str(output))
if __name__=='__main__':main()
