"""Native regression for original leaf geometry and duplicate material identities."""
import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
import traceback
import unreal

sys.path.insert(0,str(Path(__file__).resolve().parent))
from import_wt_scene import import_mesh, read, save_json
from wt_materials import Textures, parent_material, make_instance

root=Path(__file__).resolve().parents[2]
package=root/'assets/maps/wt_vlaanderen'
source=read(package/'scene-source.json')
folder='/Game/RMSim/Maps/wt_vlaanderen'
match=re.search(r'-MeshEvidence=(?:"([^"]+)"|(\S+))',unreal.SystemLibrary.get_command_line())
output=Path(match.group(1) or match.group(2)).resolve() if match else root/'artifacts/maps/wt_existing'/('mesh-preservation-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
output.mkdir(parents=True,exist_ok=False)
report=dict(status='running',complete=False,source_manifest_sha256=hashlib.sha256((package/'scene-source.json').read_bytes()).hexdigest(),results=[],scope='native_original_mesh_reimport_material_identity_triangles_uvs_and_bounds')
cache=dict(schema='wt_scene_mesh_cache_v1',assets={})
try:
    textures=Textures(package,Path(source['extraction_root']),folder)
    parent=parent_material(folder,'Leaves',textures)
    mats={}
    for spec in source['materials']:
        if spec['shader']=='rendinst_facing_leaves':
            identity,instance=make_instance(folder,spec,parent,textures)
            mats[identity]=instance
    names=('prop_kh_gol_euraspen_fall_03_rendinst','prop_kh_gol_liveoak_03_rendinst','prop_kh_gol_tree_simple_a_rendinst',
           'prop_small_airfield_obj_barracks','prop_small_airfield_obj_officer_house2')
    # Discover the three exact authored leaf IDs from their versioned adapter contract.
    leaves=[spec['id'] for spec in source['meshes'] if spec.get('leaf_adapter')]
    if len(leaves)!=3:raise ValueError('expected precisely three source leaf adaptations')
    names=tuple(leaves)+names[-2:]
    for name in names:
        spec=next(item for item in source['meshes'] if item['id']==name)
        for identity in spec['material_slots']:
            if identity not in mats:mats[identity]=unreal.load_asset(folder+'/Materials/'+identity)
        if any(value is None for value in mats.values()):raise ValueError('missing original material')
        result=import_mesh(package,folder+'/Meshes/'+name+'.'+name,spec,False,mats,cache,output/'cache.json',folder)
        report['results'].append(result)
        save_json(output/'verification.json',report)
    expected={
        'prop_small_airfield_obj_barracks':{'WT_84c786033ddc6817292e':172,'WT_9a3c9e0714f842698e5a':204},
        'prop_small_airfield_obj_officer_house2':{'WT_6d6bdb4288c73815d333':22,'WT_5565084317f5134e0501':70,'WT_9a3c9e0714f842698e5a':496}}
    for result in report['results']:
        if result['id'] not in expected:continue
        actual={}
        for section in result['native_geometry']['sections']:
            identity=section['material_slot'];actual[identity]=actual.get(identity,0)+section['triangles']
        if actual!=expected[result['id']]:raise ValueError('native per-material triangle assignment differs from original NPZ')
        result['source_material_triangle_counts_verified']=actual
    report.update(status='passed',complete=True,checks=5,finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    unreal.log('WT_MESH_PRESERVATION_COMPLETE '+str(output/'verification.json'))
except Exception as error:
    report.update(status='failed',complete=True,error=str(error),traceback=traceback.format_exc())
    raise
finally:
    save_json(output/'verification.json',report)
