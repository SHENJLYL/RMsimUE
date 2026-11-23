"""Assemble verified school hall + real CC0 campus mesh into one Unreal import batch."""
from pathlib import Path
import shutil,json,hashlib,requests
from PIL import Image
from io import BytesIO
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
r=Path('D:/simulator')
school=r/'assets/maps/rmuc2027/arena/sketchupbox-school-gym'
ext=r/'assets/maps/rmuc2027/arena/campus-exterior'
prep=json.loads((school/'prepared-02/prepared.json').read_text())
outside=json.loads((ext/'prepared-02/prepared.json').read_text())
target=school/'ue-import-02'
if target.exists():raise FileExistsError('Import batch already exists')
target.mkdir(parents=True)
(target/'textures').mkdir()
meshes=[];mats=dict(prep['materials'])
files={}
def cp(source,dest):
 dest.parent.mkdir(parents=True,exist_ok=True)
 shutil.copy2(source,dest)
 files[dest.relative_to(target).as_posix()]={'bytes':dest.stat().st_size,'sha256':sha(dest)}
for row in prep['meshes']:
 for suffix in ('.obj','.mtl'):
  p=school/'prepared-02'/(Path(row['file']).stem+suffix)
  if p.exists():cp(p,target/p.name)
 meshes.append(row)
for p in (school/'prepared-01/textures').iterdir():
 if p.is_file():cp(p,target/'textures'/p.name)
# Original SketchUp embeds four JPEG textures. Recover them directly from the
# validated converted GLB's BIN chunk with the source index preserved.
import struct
model_glb=(school/'converted/Indoor_Basketball_Gym_instanced_mm.glb').read_bytes()
assert model_glb[:4]==b'glTF'
json_length=struct.unpack_from('<I',model_glb,12)[0]
gltf=json.loads(model_glb[20:20+json_length].decode('utf-8'))
bin_chunk=20+json_length
assert model_glb[bin_chunk+4:bin_chunk+8]==b'BIN\x00'
for material_index,material in enumerate(gltf['materials']):
 tex=material.get('pbrMetallicRoughness',{}).get('baseColorTexture')
 if tex is None:continue
 img_id=gltf['textures'][tex['index']]['source']
 image_rec=gltf['images'][img_id]
 view=gltf['bufferViews'][image_rec['bufferView']]
 raw=model_glb[bin_chunk+8+view.get('byteOffset',0):bin_chunk+8+view.get('byteOffset',0)+view['byteLength']]
 extname='.jpg' if raw[:2]==b'\xff\xd8' else '.png'
 name='Material_'+str(material_index)
 if name not in mats:continue
 texture_name='Sketchup_'+name+extname
 img_path=target/'textures'/texture_name
 img_path.write_bytes(raw)
 files[img_path.relative_to(target).as_posix()]={'bytes':len(raw),'sha256':sha(img_path)}
 mats[name]['base_color_texture']='textures/'+texture_name
 print('RECOVERED_EMBEDDED_SKP_TEXTURE',name,img_id,len(raw),flush=True)
for name,entry in outside['meshes'].items():
 for suffix in ('.obj','.mtl'):
  p=ext/'prepared-02'/(name+suffix)
  if p.exists():cp(p,target/p.name)
 meshes.append(entry)
for name in ('Grass005_Color.jpg','Grass005_NormalDX.jpg','Grass005_Roughness.jpg'):
 cp(ext/'textures'/name,target/'textures'/name)
tree=ext/'sources/polyhaven/island_tree_02/textures'
for name in ('island_tree_02_diff_1k.jpg','island_tree_02_nor_gl_1k.jpg',
             'island_tree_02_branches_diff_1k.jpg','island_tree_02_branches_nor_gl_1k.jpg',
             'island_tree_02_leaves_diff_1k.jpg','island_tree_02_leaves_nor_gl_1k.jpg'):
 cp(tree/name,target/'textures'/name)
api=requests.get('https://api.polyhaven.com/files/island_tree_02',timeout=20)
api.raise_for_status();alpha_ref=api.json()['leaves_alpha']['1k']['png']
resp=requests.get(alpha_ref['url'],timeout=40);resp.raise_for_status()
with Image.open(BytesIO(resp.content)) as im:alpha=im.convert('L')
with Image.open(tree/'island_tree_02_leaves_diff_1k.jpg') as im:
 rgba=im.convert('RGBA')
 if alpha.size!=rgba.size:raise ValueError('Leaf alpha size mismatch')
 rgba.putalpha(alpha)
 rgba.save(target/'textures/IslandLeavesRGBA.png')
p=target/'textures/IslandLeavesRGBA.png'
files[p.relative_to(target).as_posix()]={'bytes':p.stat().st_size,'sha256':sha(p)}
rgb={
 'ExtGrass':([.5,.8,.5,1],.88),
 'ExtAsphalt':([.17,.17,.18,1],.90),
 'ExtPaving':([.75,.77,.75,1],.77),
 'ExtCurbs':([.84,.85,.81,1],.74),
 'ExtFacade':([.83,.85,.80,1],.72),
 'ExtBrick':([.6,.44,.39,1],.76),
 'ExtFrames':([.20,.27,.31,1],.4),
 'ExtRoof':([.32,.42,.48,1],.54),
 'ExtTrim':([.95,.97,.94,1],.68),
 'ExtBenchWood':([.69,.48,.28,1],.63),
 'ExtMetal':([.35,.4,.45,1],.41),
 'ExtLeaves':([.18,.36,.18,1],.75),
}
for name,(color,rough) in rgb.items():
 mats[name]={'base_color_factor':color,'metallic_factor':.68 if name in ('ExtFrames','ExtRoof','ExtMetal') else 0.,
            'roughness_factor':rough,'alpha_mode':'OPAQUE','semantic':'source'}
mats['ExtGrass']['base_color_texture']='textures/Grass005_Color.jpg'
mats['ExtGrass']['normal_texture']='textures/Grass005_NormalDX.jpg'
mats['ExtGlass']={'base_color_factor':[.18,.35,.49,.55],'semantic':'glass','alpha_mode':'BLEND'}
mats['island_tree_02']={'base_color_factor':[1,1,1,1],'roughness_factor':.8,'semantic':'source','alpha_mode':'OPAQUE',
    'base_color_texture':'textures/island_tree_02_diff_1k.jpg','normal_texture':'textures/island_tree_02_nor_gl_1k.jpg'}
mats['island_tree_02_branches']={'base_color_factor':[1,1,1,1],'roughness_factor':.8,'semantic':'source','alpha_mode':'OPAQUE',
    'base_color_texture':'textures/island_tree_02_branches_diff_1k.jpg','normal_texture':'textures/island_tree_02_branches_nor_gl_1k.jpg'}
mats['island_tree_02_leaves']={'base_color_factor':[1,1,1,1],'roughness_factor':.81,'semantic':'source','alpha_mode':'MASK',
   'alpha_cutoff':.45,'double_sided':True,'base_color_texture':'textures/IslandLeavesRGBA.png',
   'normal_texture':'textures/island_tree_02_leaves_nor_gl_1k.jpg'}
assert len(meshes)<=32
used=set(m for x in meshes for m in x['material_slots'])
missing=used-set(mats)
assert not missing,missing
for row in meshes:
 p=target/row['file']
 if sha(p)!=row['sha256']:raise RuntimeError('Copied geometry changed '+row['file'])
result={'schema':'rmsim_school_gym_native_import_v1','source_file':'../source/Indoor Basketball Gym.skp',
 'source_sha256':'a40bbf0c69bace3b5ee1aed3795217e69c88c4c58ce71b191aff3e033e3502bc',
 'source_layout':'../prepared-01/prepared.json','external_source':'../../campus-exterior/SOURCE.json',
 'materials':mats,'meshes':meshes,'files':files,
 'license_note':'SketchupBox modified project: credit site and do not redistribute isolated raw source. Poly Haven and ambientCG exterior CC0.',
 'removal_count':prep['removed'],'units':'metres'}
(target/'prepared.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print('SCHOOL_NATIVE_IMPORT_BATCH_READY',len(meshes),'assets',len(mats),'materials',len(files),'files',flush=True)
