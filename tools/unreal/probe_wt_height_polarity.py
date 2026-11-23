"""Controlled, source-specific asphalt HeightBlend.z probe. Run only in owned rendering UE.
Inputs: -SceneSource=<scene-source.json> -HeightPolarityProbeOutput=<fresh directory>
Mutates and saves only WT_e82be167172b929d5356. Original source contracts remain unchanged.
"""
from pathlib import Path
from datetime import datetime, timezone
import argparse, ast, hashlib, json, re, shutil, struct, sys, traceback
try:
    import unreal
except ModuleNotFoundError as exc:
    if exc.name != 'unreal':raise
    unreal=None

ROOT=(Path(unreal.Paths.project_dir()).resolve().parents[1] if unreal is not None else
      next(p for p in Path(__file__).resolve().parents if (p/'unreal/RMSim/RMSim.uproject').is_file()))
sys.path.insert(0,str(ROOT/'tools/unreal'))
import wt_ground_microdetails as micro
import wt_height_decals as height
if unreal is not None:
    from verify_wt_materials import asset_file, statistics
    from wt_ground_material_verify import height_instance, texture_state

MATERIAL_ID='WT_e82be167172b929d5356'
ASSET='/Game/RMSim/Maps/wt_vlaanderen/Materials/'+MATERIAL_ID
PARENT='/Game/RMSim/Maps/wt_vlaanderen/Materials/WT_Parent_HeightDecal'
EXPECTED_ASSET_SHA256='b7952f1016980db91ecdc9a66307f5ae6979c04e2e0977fca4484758ccacbe77'
EXPECTED_SOURCE_SHA256='427c9b06b62af0635fa08f2d6238211b9850caf7717a0773f186e1835be63b8f'
BEFORE_BLEND=[.25,1.,0.,0.]
PROBE_BLEND=[.25,1.,1.,0.]
PRIVATE_ADAPTATION_TAG='wt_private_asphalt_height_alpha_polarity_probe_v1'

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()
def require(ok,message):
    if not ok:raise RuntimeError(message)
def vector(value):
    return [float(value.get_editor_property(k)) for k in ('r','g','b','a')]
def save(path,report):
    temp=path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    temp.replace(path)
def source_preflight(source_path):
    """Validate the real package/inventory/proof bytes without loading or saving UE assets."""
    source_path=Path(source_path).resolve(strict=True)
    package=source_path.parent
    source=json.loads(source_path.read_text(encoding='utf-8-sig'))
    require(source['id']=='wt_vlaanderen' and source['source_sha256']==EXPECTED_SOURCE_SHA256,
            'Original map identity differs')
    specs=source['materials']
    require(isinstance(specs,list) and all(isinstance(s,dict) and isinstance(s.get('id'),str) for s in specs),
            'Expected the scene-source inline material-spec list')
    require(len({s['id'] for s in specs})==len(specs),'Duplicate source material IDs')
    selected=[s for s in specs if s['id']==MATERIAL_ID]
    require(len(selected)==1,'Expected one original material spec')
    spec=selected[0]
    require(spec['name']=='decal_asphalt_road_worn_b'
            and spec['shader']=='land_mesh_height_decal' and spec['parameters']=='',
            'Asphalt source identity/shader/parameters differ')
    require(spec['texture_slots']==['decal_asphalt_road_worn_b_tex_d*',None,
            'decal_asphalt_road_worn_b_tex_r*']+[None]*8,
            'Asphalt original d/null/r/null source slots differ')
    extraction=Path(source['extraction_root'])
    if not extraction.is_absolute():extraction=package/extraction
    extraction=extraction.resolve(strict=True)
    inventory_file=extraction/'textures-03/result.json'
    inventory_document=json.loads(inventory_file.read_text(encoding='utf-8-sig'))
    exports=inventory_document['exported']
    require(isinstance(exports,list) and all(isinstance(x,dict) and isinstance(x.get('name'),str) for x in exports),
            'Expected textures-03 inventory exported list')
    inventory={x['name']:x for x in exports}
    require(len(inventory)==len(exports),'Duplicate inventory texture names')
    content=ROOT/'unreal/RMSim/Content'
    filename=content/'RMSim/Maps/wt_vlaanderen/Materials'/f'{MATERIAL_ID}.uasset'
    parent_file=filename.parent/'WT_Parent_HeightDecal.uasset'
    world=filename.parent.parent/'World.umap'
    require(digest(filename)==EXPECTED_ASSET_SHA256,
            'Target original native uasset SHA differs; do not overwrite newer work')
    guarded=[source_path,inventory_file,world,parent_file]
    guarded+=list(filename.parent.glob('*.uasset'))
    target_textures={}
    for index,color in ((0,True),(2,False)):
        name=spec['texture_slots'][index].split('*')[0].split('$')[0]
        require(name in inventory,'Original source texture is absent from inventory: '+name)
        entry=inventory[name]
        require(isinstance(entry.get('size'),list) and entry['size']==[2048,1024]
                and entry.get('mode')=='RGBA' and re.fullmatch('[0-9a-f]{64}',entry.get('sha256','')),
                'Original target texture metadata differs: '+name)
        png=package/'textures'/(name+'.png')
        extracted_png=Path(entry['file'])
        if not extracted_png.is_absolute():extracted_png=inventory_file.parent/extracted_png
        extracted_png=extracted_png.resolve(strict=True)
        for source_png in (png,extracted_png):
            require(digest(source_png)==entry['sha256'],'Original source PNG differs: '+str(source_png))
            with source_png.open('rb') as stream:header=stream.read(29)
            require(header[:8]==b'\x89PNG\r\n\x1a\n' and
                    list(struct.unpack('>II',header[16:24]))==entry['size'] and
                    header[24:26]==bytes((8,6)),'Original source PNG format differs: '+str(source_png))
        asset_name=re.sub('[^A-Za-z0-9_]','_',name)+('_C' if color else '_D')
        texture_file=content/'RMSim/Maps/wt_vlaanderen/Textures'/(asset_name+'.uasset')
        require(texture_file.is_file(),'Expected original native texture file missing: '+str(texture_file))
        guarded += [png,extracted_png,texture_file]
        target_textures['T'+str(index)]=dict(source_name=name,source_size=entry['size'],
            source_png=str(png),source_png_sha256=entry['sha256'],extracted_png=str(extracted_png),
            native_asset='/Game/RMSim/Maps/wt_vlaanderen/Textures/'+asset_name+'.'+asset_name,
            native_file=str(texture_file),native_file_sha256=digest(texture_file),srgb=color)
    data=micro.validate(package)
    source_contract=height.contract(spec,data)
    definition=package/micro.DEFINITION_FILE
    guarded.append(definition)
    base=definition.parent
    for key in ('source_config_file','source_binding_file','proof_file'):
        guarded.append(micro._path(base,data[key]))
    guarded += [micro._path(base,s['texture_file']) for s in data['slots']]
    signatures={}
    expected={'verify_wt_materials.py':{'asset_file':['asset_path'],
                  'statistics':['material','texture_free']},
              'wt_ground_material_verify.py':{'height_instance':['material','spec','package','inventory','data'],
                  'texture_state':['texture']}}
    for module,wanted in expected.items():
        file=ROOT/'tools/unreal'/module
        functions={n.name:n for n in ast.parse(file.read_text(encoding='utf-8-sig')).body
                   if isinstance(n,ast.FunctionDef)}
        for name,args in wanted.items():
            require(name in functions and [a.arg for a in functions[name].args.args]==args,
                    'Native helper signature changed: '+module+':'+name)
            signatures[module+':'+name]=args
    guard_paths=list(dict.fromkeys(Path(p).resolve(strict=True) for p in guarded))
    files=[dict(path=str(p),sha256=digest(p)) for p in guard_paths]
    report=dict(schema='rmsim_asphalt_probe_source_preflight_v1',status='passed',
        source_file=str(source_path),source_sha256=digest(source_path),
        materials_storage='scene_source_inline_list',materials_count=len(specs),material_spec=spec,
        extraction_root=str(extraction),inventory_file=str(inventory_file),
        inventory_sha256=digest(inventory_file),inventory_exported_count=len(exports),
        inventory_status=inventory_document.get('status'),target_textures=target_textures,
        expected_target_file=str(filename),expected_target_sha256=EXPECTED_ASSET_SHA256,
        microdetail_definition_file=str(definition),microdetail_definition_sha256=digest(definition),
        microdetail_slot_count=data['slot_count'],microdetail_unique_textures=data['unique_texture_count'],
        source_height_contract=source_contract,native_helper_signatures=signatures,
        guarded_file_count=len(files),guarded_files=files,
        native_execution_performed=False,asset_mutation_performed=False)
    return source,spec,inventory,data,report

def parameter_snapshot(mi):
    edit=unreal.MaterialEditingLibrary
    textures={}
    for index in (0,2):
        tex=edit.get_material_instance_texture_parameter_value(mi,'T'+str(index))
        require(tex is not None,'missing original source texture slot')
        textures['T'+str(index)]=dict(path=tex.get_path_name(),state=texture_state(tex))
    return dict(height_blend=vector(edit.get_material_instance_vector_parameter_value(mi,'HeightBlend')),
        height_source=vector(edit.get_material_instance_vector_parameter_value(mi,'HeightSource')),
        height_normal_strength=float(edit.get_material_instance_scalar_parameter_value(mi,'HeightNormalStrength')),
        parent=mi.get_editor_property('parent').get_path_name(),textures=textures)

def run(source_path,output):
    source_path=Path(source_path).resolve(strict=True);package=source_path.parent
    evidence=Path(output).resolve()
    require(not evidence.exists(),'Probe evidence directory must be fresh')
    evidence.mkdir(parents=True)
    report_path=evidence/'verification.json'
    report=dict(schema='rmsim_asphalt_height_polarity_probe_v1',status='running',
        started_utc=datetime.now(timezone.utc).isoformat(),asset=ASSET,parent=PARENT,
        expected_original_asset_sha256=EXPECTED_ASSET_SHA256,private_resource_adaptation_tag=PRIVATE_ADAPTATION_TAG,
        source_semantics='Controlled adaptation for this hashed asphalt resource only. The public shader/source contract is retained as the baseline.',
        original_height_blend=BEFORE_BLEND,requested_probe_height_blend=PROBE_BLEND,
        only_intended_parameter_change='HeightBlend.b (z):0->1',
        visual_effect_verified=False,source_manifest_mutated=False,world_mutated=False,
        script_sha256=digest(__file__),helper_sha256={name:digest(ROOT/'tools/unreal'/name) for name in (
          'verify_wt_materials.py','wt_ground_material_verify.py','wt_ground_microdetails.py','wt_height_decals.py','wt_materials.py')})
    save(report_path,report)
    before_files={}
    try:
        command=unreal.SystemLibrary.get_command_line()
        require(not re.search(r'(?:^|\s)-(?:nullrhi|noshadercompile)\b',command,re.I),'Probe requires rendering and shader compilation')
        require(bool(re.search(r'(?:^|\s)-AllowCommandletRendering\b',command,re.I)),'Pass -AllowCommandletRendering')
        source,spec,inventory,data,preflight=source_preflight(source_path)
        report['source_preflight']=preflight
        filename=asset_file(ASSET)
        require(str(filename.resolve())==preflight['expected_target_file'],'Native asset path resolver differs')
        before_files={item['path']:item['sha256'] for item in preflight['guarded_files']}
        backup=evidence/'backup'/filename.name
        backup.parent.mkdir()
        shutil.copyfile(filename,backup)
        require(digest(backup)==EXPECTED_ASSET_SHA256,'Original asset backup differs')
        report.update(source_file=str(source_path),source_file_sha256=digest(source_path),
            material_spec=spec,backup_file=str(backup),backup_sha256=digest(backup),guarded_file_count=len(before_files))
        save(report_path,report)
        mi=unreal.load_asset(ASSET);parent=unreal.load_asset(PARENT)
        require(isinstance(mi,unreal.MaterialInstanceConstant),'Target is not a MaterialInstanceConstant')
        require(parent is not None and mi.get_editor_property('parent')==parent,'Original parent differs')
        require(parent.get_path_name()==PARENT+'.WT_Parent_HeightDecal','Unexpected parent object path')
        # Force source textures to be loaded before the established UE compilation barrier.
        for index in (0,2):
            require(unreal.MaterialEditingLibrary.get_material_instance_texture_parameter_value(mi,'T'+str(index)) is not None,
                    'Source texture slot failed to load')
        unreal.SystemLibrary.execute_console_command(None,'Editor.AsyncTextureCompilationFinishAll')
        report['original_native_contract']=height_instance(mi,spec,package,inventory,data)
        report['inventory_file']=preflight['inventory_file']
        report['inventory_sha256']=preflight['inventory_sha256']
        before=parameter_snapshot(mi)
        require(before['height_blend']==BEFORE_BLEND,'Original native HeightBlend differs')
        for item in before['textures'].values():
            tp=asset_file(item['path']);before_files[str(tp)]=digest(tp)
        report['parameters_before']=before
        errors=unreal.MaterialEditingLibrary.recompile_material(parent)
        require(errors is not None and not isinstance(errors,(str,bool)),'Expected native Array[str] compiler errors')
        report['parent_recompile_errors']=[str(x) for x in errors]
        require(not report['parent_recompile_errors'],'Original parent has compilation errors')
        report['parent_completed_statistics']=statistics(parent)
        report['instance_completed_statistics_before']=statistics(mi)
        save(report_path,report)
        setter_return=unreal.MaterialEditingLibrary.set_material_instance_vector_parameter_value(
            mi,'HeightBlend',unreal.LinearColor(*PROBE_BLEND))
        unreal.MaterialEditingLibrary.update_material_instance(mi)
        after=parameter_snapshot(mi)
        require(after['height_blend']==PROBE_BLEND,'Probe value did not read back')
        require({k:v for k,v in after.items() if k!='height_blend'}==
                {k:v for k,v in before.items() if k!='height_blend'},'An unrelated native instance parameter or source texture changed')
        report['setter_return_diagnostic_only']=setter_return
        report['parameters_after']=after
        report['instance_completed_statistics_after']=statistics(mi)
        report['compile_error_scope']='Native parent RecompileMaterial returned zero errors; target MI GetStatistics completed its real resource before and after the uniform-only vector change.'
        require(unreal.EditorAssetLibrary.save_loaded_asset(mi,only_if_is_dirty=False),'Target MI save failed')
        report['saved_asset']=ASSET
        require(parameter_snapshot(mi)['height_blend']==PROBE_BLEND,'Saved MI readback changed')
        checks=[dict(path=p,before_sha256=old,after_sha256=digest(p)) for p,old in before_files.items()]
        changed=[x['path'] for x in checks if x['before_sha256']!=x['after_sha256']]
        require(changed==[str(filename)] or set(changed)=={str(filename)},'Unexpected native/source file mutation: '+str(changed))
        require(digest(filename)!=EXPECTED_ASSET_SHA256,'Saved probe bytes did not change')
        check_path=evidence/'input-hashes.json'
        save(check_path,dict(schema='rmsim_single_material_probe_hashes_v1',checks=checks,changed_paths=changed))
        report.update(status='passed',finished_utc=datetime.now(timezone.utc).isoformat(),
            asset_sha256_after=digest(filename),input_hashes_file=str(check_path),input_hashes_sha256=digest(check_path),
            changed_files=changed,parent_world_source_unchanged=True,other_material_files_unchanged=True,
            saved_assets_modified=True,rollback_requires_closed_owned_editor=True)
        save(report_path,report)
        unreal.log('WT_ASPHALT_HEIGHT_POLARITY_PROBE_PASSED '+str(report_path))
    except Exception:
        report.update(status='failed',error=traceback.format_exc(),failed_utc=datetime.now(timezone.utc).isoformat())
        if before_files:
            report['observed_changed_files_on_failure']=[p for p,h in before_files.items() if Path(p).is_file() and digest(p)!=h]
        save(report_path,report)
        raise

if __name__=='__main__':
    if unreal is None:
        parser=argparse.ArgumentParser(description='Read-only real-data preflight; does not load UE')
        parser.add_argument('--OfflinePreflight',action='store_true',required=True)
        parser.add_argument('--SceneSource',type=Path,required=True)
        parser.add_argument('--Output',type=Path,required=True)
        args=parser.parse_args()
        require(not args.Output.exists(),'Offline report must use a fresh path')
        *_,report=source_preflight(args.SceneSource)
        report['script_sha256']=digest(__file__)
        report['checked_utc']=datetime.now(timezone.utc).isoformat()
        args.Output.parent.mkdir(parents=True,exist_ok=True)
        save(args.Output,report)
        print(json.dumps(dict(status=report['status'],output=str(args.Output.resolve()),
            guarded_files=report['guarded_file_count'],materials=report['materials_count'],
            inventory_exported=report['inventory_exported_count'],
            report_sha256=digest(args.Output)),ensure_ascii=True))
    else:
        command=unreal.SystemLibrary.get_command_line()
        source=re.search(r'-SceneSource=(?:"([^"]+)"|(\S+))',command)
        output=re.search(r'-HeightPolarityProbeOutput=(?:"([^"]+)"|(\S+))',command)
        if not source or not output:raise ValueError('Pass -SceneSource and -HeightPolarityProbeOutput=<fresh directory>')
        run(source.group(1) or source.group(2),output.group(1) or output.group(2))
