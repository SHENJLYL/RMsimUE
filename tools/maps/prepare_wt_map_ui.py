"""Rebuild or verify original War Thunder minimap UI resources without modifying the game.

--verify reads an existing map package and original archives; it writes nothing.
--output creates a fresh staging overlay with overview.json, overview/ui-assets.json,
overview/ui-provenance.json, overview/ui/* and mission-markers.json. It never writes
the source map package. Review and apply the overlay separately before UE import.

Generation requires an already installed Inkscape (--inkscape or PATH). SVGs retain
the original paths/colors/alpha and render at 2x their intrinsic dimensions. Fonts
are byte-identical copies; the attention icon is a lossless PNG of the AVIF decode.
No software is downloaded, and no extracted game script is executed.
"""
import sys
sys.dont_write_bytecode = True

import argparse
import datetime
import hashlib
import io
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import traceback
import xml.etree.ElementTree as ET
from PIL import Image
from wt_vromfs import read_vromfs, bounded_zstd
from wt_blk import Reader, decode_blk, shared_context, MAX_DECODED_BYTES

FOLDER='/Game/RMSim/Maps/wt_vlaanderen/UI'
MISSION_ENTRY='gamedata/missions/cta/tanks/vlaanderen/mainareas/vlaanderen_dom_template.blk'
DOM_ENTRY='gamedata/missions/templates/tank_templates/dom_template.blk'
BATTLE_ENTRY='gamedata/missions/cta/tanks/vlaanderen/battleareas/template_vlaanderen_dom_battlearea.blk'
TEXTURES=[('player_arrow_asset','map_player','WT_MapPlayerArrow',[19,32]),
    ('hull_arrow_asset','map_player_tank_hull','WT_MapTankHull',[17,17]),
    ('capture_A','basezone_small_mark_a','WT_MapCaptureA',[21,21]),
    ('capture_B','basezone_small_mark_b','WT_MapCaptureB',[21,21]),
    ('capture_C','basezone_small_mark_c','WT_MapCaptureC',[21,21]),
    ('poi','poi','WT_MapPOI',[30,30]),('spawn','objective_tank','WT_MapSpawn',[36,36])]
FONTS=[('font_asset','pt_sans-web-regular-reducedheight.ttf','WT_MapFontFace'),
    ('zone_font_asset','firasanscondensed-light.ttf','WT_MapZoneFontFace')]
AVIF_TEXTURES=[('ping','chat_icon_attention','WT_MapPing',[28,28]),
    ('full_map_background_asset','option_select_odd','WT_MapPanelBackground',[128,16])]
ARCHIVES=['ui/atlases.vromfs.bin','ui/fonts.vromfs.bin','aces.vromfs.bin','gui.vromfs.bin','mis.vromfs.bin']


def require(value,reason):
    if not value:raise ValueError(reason)


def digest(data):return hashlib.sha256(data).hexdigest()
def read_json(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def write_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def archive_context(files):
    """Preserve non-UTF8 *unused* shared names without weakening selected BLK decoding."""
    try:
        names,dictionary,meta=shared_context(files)
        return names,dictionary,dict(meta,opaque_non_utf8_names=0)
    except UnicodeDecodeError:
        raw=files.get('\xff?nm');require(raw is not None and len(raw)>=40,'shared namemap header missing')
        data=bounded_zstd(raw[40:],MAX_DECODED_BYTES);r=Reader(data)
        count=r.varuint();size=r.varuint()
        require(0<count<=size<=256*1024*1024,'invalid shared namemap size')
        encoded=r.take(size);require(encoded.endswith(b'\0') and r.pos==len(data),'invalid shared namemap coverage')
        fields=encoded[:-1].split(b'\0');require(len(fields)==count,'shared name count differs')
        names=[field.decode('utf-8',errors='surrogateescape') for field in fields]
        opaque=sum(any(0xDC80<=ord(ch)<=0xDCFF for ch in name) for name in names)
        key=raw[8:40].hex()+'.dict';dictionary=files.get(key)
        return names,dictionary,dict(shared_names=count,dictionary_entry=key if dictionary else None,opaque_non_utf8_names=opaque,policy='Opaque bytes preserved; any selected BLK reference to such a name is rejected.')


def reject_opaque(value):
    if isinstance(value,str):require(not any(0xDC80<=ord(ch)<=0xDCFF for ch in value),'selected BLK refers to a non-UTF8 shared name')
    elif isinstance(value,list):
        for v in value:reject_opaque(v)
    elif isinstance(value,dict):
        for k,v in value.items():reject_opaque(k);reject_opaque(v)


def decode_selected(files,entry):
    names,dictionary,context=archive_context(files)
    tree,decoding=decode_blk(files[entry],names,dictionary);reject_opaque(tree)
    return tree,dict(entry=entry,sha256=digest(files[entry]),context=context,decoding=decoding)


def get_sources(game):
    resources={};archives={}
    for name in ARCHIVES:
        files,info=read_vromfs(game/name);archives[name]=info
        if name=='ui/atlases.vromfs.bin':
            wanted=['gameuiskin/'+n+'.svg' for _,n,_,_ in TEXTURES]+['gameuiskin/'+name+'.avif' for _,name,_,_ in AVIF_TEXTURES]
            resources['icons']={entry:files[entry] for entry in wanted}
        elif name=='ui/fonts.vromfs.bin':
            resources['fonts']={'ttfs/'+name:files['ttfs/'+name] for _,name,_ in FONTS}
            resources['font_definition']=files['fonts.dynfont.blk']
        elif name=='aces.vromfs.bin':
            hud,proof=decode_selected(files,'config/hud.blk')
            resources['hud']=next(c for c in hud['children'] if c['name']=='tacticalMap');resources['hud_proof']=proof
        elif name=='gui.vromfs.bin':
            resources['styles']={entry:files[entry] for entry in ['gui/tacticalscr.css','globals/style/cssconsts.nut','gui/optionlist.css']}
        else:
            mission,proof=decode_selected(files,MISSION_ENTRY)
            resources['mission']=mission;resources['mission_proof']=proof
            resources['mission_dependency_hashes']={entry:digest(files[entry]) for entry in [DOM_ENTRY,BATTLE_ENTRY]}
    return resources,archives


def marker_data(source,overview,resources,archives,mode):
    mat=source['source_to_ros_matrix'];translation=source['source_translation_m']
    require(len(mat)==3 and all(len(row)==3 for row in mat) and len(translation)==3,'invalid source-to-ROS transform')
    def ros(v):return [sum(mat[i][j]*v[j] for j in range(3))+translation[i] for i in range(3)]
    def values(b):return {p['name']:p['value'] for p in b['params']}
    tree=resources['mission'];markers=[]
    for b in next(c for c in tree['children'] if c['name']=='areas')['children']:
        match=re.fullmatch('dom_capture_area_([0-9]+)_'+mode,b['name'])
        if not match:continue
        tm=values(b)['tm'];pos=tm[9:12];index=int(match.group(1));rp=ros(pos)
        require(1<=index<=3,'unexpected Dom capture index')
        markers.append(dict(id=b['name'],kind='capture',label=chr(64+index),team='neutral',position_ros_m=rp[:2],position_ros_xyz_m=rp,radius_m=math.sqrt(sum(v*v for v in tm[:3])),source=dict(entry=MISSION_ENTRY,block='areas/'+b['name'],dagor_position_m=pos)))
    for b in next(c for c in tree['children'] if c['name']=='units')['children']:
        vals=values(b);name=vals.get('name','');match=re.fullmatch('dom_t([12])_tank_spawn_([0-9]+)_'+mode,name)
        if not match:continue
        pos=vals['tm'][9:12];rp=ros(pos)
        markers.append(dict(id=name,kind='spawn',label=str(int(match.group(2))),team=int(match.group(1)),position_ros_m=rp[:2],position_ros_xyz_m=rp,source=dict(entry=MISSION_ENTRY,block='units/area_squad[name='+name+']',dagor_position_m=pos)))
    require(len(markers)==7 and len({m['id'] for m in markers})==7,'expected three Dom zones and four original tank spawn groups')
    require(all(all(math.isfinite(v) for v in m['position_ros_xyz_m']) for m in markers),'non-finite mission position')
    return dict(schema='rmsim_map_mission_markers_v1',map_id='wt_vlaanderen',source_sha256=overview['source_sha256'],coordinate_system='ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP',mission='vlaanderen_dom',mode=mode,reference_team=1,marker_state='static mission definitions; no live unit or capture ownership state',source_archive=archives['mis.vromfs.bin'],source_entry=MISSION_ENTRY,source_entry_sha256=resources['mission_proof']['sha256'],source_dependencies=resources['mission_dependency_hashes'],decoding_proof=resources['mission_proof'],source_to_ros_matrix=mat,source_translation_m=translation,markers=sorted(markers,key=lambda m:(m['kind'],m['id'])))


def svg_dimensions(data):
    root=ET.fromstring(data)
    for node in root.iter():
        require(node.tag.rsplit('}',1)[-1] not in ('script','foreignObject'),'active SVG content is unsupported')
        for key,value in node.attrib.items():
            if key.rsplit('}',1)[-1]=='href':require(value.startswith('#'),'external SVG reference is unsupported')
            for url in re.findall(r'url\(([^)]+)\)',value):require(url.strip().strip('\"\'').startswith('#'),'external SVG URL is unsupported')
    if 'width' in root.attrib and 'height' in root.attrib:
        return [float(re.sub(r'px$','',root.attrib[k])) for k in ['width','height']]
    return list(map(float,root.attrib['viewBox'].split()))[2:4]


def prepare(args,package,overview,source,resources,archives,report):
    out=args.output.resolve();require(not out.exists(),'output directory must be fresh')
    require(not out.is_relative_to(package) and not out.is_relative_to(args.game_root.resolve()),'output must be outside the source package and original game')
    executable=str(args.inkscape) if args.inkscape else shutil.which('inkscape')
    require(executable is not None and Path(executable).is_file(),'generation needs an existing Inkscape executable; use --inkscape')
    version=subprocess.run([executable,'--version'],capture_output=True,text=True,check=True).stdout.strip()
    out.mkdir(parents=True);ui=out/'overview/ui';ui.mkdir(parents=True);assets=[]
    for role,name,asset,size in TEXTURES:
        entry='gameuiskin/'+name+'.svg';data=resources['icons'][entry];svg=ui/(name+'.svg');png=ui/(name+'.png');svg.write_bytes(data)
        intrinsic=svg_dimensions(data);pixels=[int(round(v*2)) for v in intrinsic]
        require(all(1<=v<=1024 for v in pixels),'unexpected SVG dimensions')
        command=[executable,str(svg),'--export-type=png','--export-filename='+str(png),'--export-width='+str(pixels[0]),'--export-height='+str(pixels[1])]
        subprocess.run(command,check=True,capture_output=True)
        image=Image.open(png);require(image.mode=='RGBA' and list(image.size)==pixels and image.getextrema()[3][0]==0,'SVG rasterization lost dimensions or alpha')
        assets.append(dict(role=role,kind='texture',file='overview/ui/'+png.name,sha256=digest(png.read_bytes()),source_entry=entry,source_sha256=digest(data),source_archive='ui/atlases.vromfs.bin',asset_name=asset,asset=FOLDER+'/'+asset+'.'+asset,native_display_size_1080=size,decoded_size=pixels,rasterization='Inkscape SVG renderer; original paths, colors and alpha; 2x intrinsic size'))
    for role,name,asset,pixels in AVIF_TEXTURES:
        entry='gameuiskin/'+name+'.avif';avif=ui/(name+'.avif');avif.write_bytes(resources['icons'][entry]);png=avif.with_suffix('.png')
        image=Image.open(avif).convert('RGBA');require(list(image.size)==pixels,'original AVIF dimensions differ: '+role);image.save(png)
        row=dict(role=role,kind='texture',file='overview/ui/'+png.name,sha256=digest(png.read_bytes()),source_entry=entry,source_sha256=digest(resources['icons'][entry]),source_archive='ui/atlases.vromfs.bin',asset_name=asset,asset=FOLDER+'/'+asset+'.'+asset,decoded_size=list(image.size),rasterization='Pillow AVIF decode; original RGBA including alpha gradient; no resampling')
        if role=='full_map_background_asset':
            row.update(source_pixel_size=pixels,native_nine_slice_background_position=[1,5])
        else:row['native_display_size_1080']=pixels
        assets.append(row)
    for role,name,asset in FONTS:
        entry='ttfs/'+name;data=resources['fonts'][entry];(ui/name).write_bytes(data)
        assets.append(dict(role=role,kind='font',file='overview/ui/'+name,sha256=digest(data),source_entry=entry,source_sha256=digest(data),source_archive='ui/fonts.vromfs.bin',asset_name=asset,face_asset=FOLDER+'/'+asset+'.'+asset,asset=FOLDER+'/'+asset+'_Font.'+asset+'_Font'))
    provenance=dict(schema='rmsim_wt_map_ui_sources_v1',map_id='wt_vlaanderen',source_sha256=overview['source_sha256'],original_archives=[archives[n] for n in ARCHIVES],native_hud_entry='aces.vromfs.bin/config/hud.blk',native_hud_entry_sha256=resources['hud_proof']['sha256'],native_tactical_map_config=resources['hud'],source_style_hashes={name:digest(data) for name,data in resources['styles'].items()},font_definition_sha256=digest(resources['font_definition']),assets=assets,renderer=dict(executable=executable,version=version),generation_tool_sha256=digest(Path(__file__).read_bytes()),verification_boundary='Source provenance is verified; runtime screenshots independently establish rendering and interaction.')
    provenance['native_panel_style']=dict(source_archive='gui.vromfs.bin',source_entry='gui/optionlist.css',source_sha256=digest(resources['styles']['gui/optionlist.css']),re_type='9rect',background_color='@white',background_position=[1,5],background_repeat='expand')
    write_json(out/'overview/ui-provenance.json',provenance)
    write_json(out/'overview/ui-assets.json',dict(schema='rmsim_wt_map_ui_assets_v1',map_id='wt_vlaanderen',source_sha256=overview['source_sha256'],assets=assets))
    write_json(out/'mission-markers.json',marker_data(source,overview,resources,archives,args.mission_mode))
    updated=dict(overview)
    for a in assets:
        if a['role'].endswith('_asset'):updated[a['role']]=a['asset']
    updated['marker_assets']={a['role']:a['asset'] for a in assets if a['role'] in ['capture_A','capture_B','capture_C','spawn','ping','poi']}
    updated.update(mission_markers_file='mission-markers.json',ui_assets_file='overview/ui-assets.json',ui_provenance_file='overview/ui-provenance.json',ui_provenance_sha256=digest((out/'overview/ui-provenance.json').read_bytes()))
    write_json(out/'overview.json',updated)
    report.update(status='passed',mode='fresh_staging_overlay',output=str(out),source_package=str(package),canonical_package_written=False,assets=len(assets),markers=7,renderer_version=version,output_files=[dict(file=str(p.relative_to(out)),sha256=digest(p.read_bytes()),bytes=p.stat().st_size) for p in sorted(out.rglob('*')) if p.is_file()])
    report['finished_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    write_json(out/'preparation-report.json',report)


def verify(package,overview,source,resources,archives,report):
    manifest=read_json(package/overview['ui_assets_file']);provenance=read_json(package/overview['ui_provenance_file']);markers=read_json(package/overview['mission_markers_file'])
    require(manifest['schema']=='rmsim_wt_map_ui_assets_v1' and manifest['map_id']=='wt_vlaanderen' and manifest['source_sha256']==overview['source_sha256'],'UI manifest identity differs')
    require(digest((package/overview['ui_provenance_file']).read_bytes())==overview['ui_provenance_sha256'],'UI provenance hash differs')
    expected_roles={x[0] for x in TEXTURES+FONTS+AVIF_TEXTURES}
    require(len(manifest['assets'])==11 and {a['role'] for a in manifest['assets']}==expected_roles,'UI role coverage differs')
    for a in manifest['assets']:
        path=(package/a['file']).resolve(strict=True);require(path.is_relative_to(package/'overview/ui'),'UI file escapes its source folder')
        require(digest(path.read_bytes())==a['sha256'],'prepared UI hash differs: '+a['role'])
        original=(resources['fonts'] if a['kind']=='font' else resources['icons'])[a['source_entry']]
        require(digest(original)==a['source_sha256'],'original UI source hash differs: '+a['role'])
        if a['kind']=='font':require(path.read_bytes()==original,'font is not a byte-identical original')
        else:
            image=Image.open(path);require(list(image.size)==a['decoded_size'],'prepared icon dimensions differ')
            if a['source_entry'].endswith('.svg'):
                require(path.with_suffix('.svg').read_bytes()==original,'prepared SVG differs from original paths/colors')
            elif a['source_entry'].endswith('.avif'):
                reference_image=Image.open(io.BytesIO(original)).convert('RGBA')
                require(image.convert('RGBA').size==reference_image.size and image.convert('RGBA').tobytes()==reference_image.tobytes(),'prepared AVIF pixels/alpha differ from original: '+a['role'])

        reference=overview[a['role']] if a['role'].endswith('_asset') else overview['marker_assets'][a['role']]
        require(reference==a['asset'],'overview asset reference differs')
    require(provenance['native_hud_entry_sha256']==resources['hud_proof']['sha256'],'native map renderer config changed')
    require(provenance['native_tactical_map_config']==resources['hud'],'native map renderer values differ')
    fresh=marker_data(source,overview,resources,archives,markers['mode'])
    require(markers['source_entry_sha256']==fresh['source_entry_sha256'] and markers['markers']==fresh['markers'],'mission markers differ from the current original BLK')
    report.update(status='passed',mode='read_only_verify',assets_verified=11,original_mission_markers_verified=7,mission_decoding_proof=resources['mission_proof'],writes_performed=False,original_archives=archives)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--game-root',type=Path,default=Path(r'D:\steamlaptop\steamapps\common\War Thunder'))
    parser.add_argument('--package',type=Path,default=Path(__file__).resolve().parents[2]/'assets/maps/wt_vlaanderen')
    mode=parser.add_mutually_exclusive_group(required=True);mode.add_argument('--verify',action='store_true');mode.add_argument('--output',type=Path)
    parser.add_argument('--inkscape',type=Path);parser.add_argument('--mission-mode',choices=['arcade','hardcore'],default='arcade')
    args=parser.parse_args();report=dict(schema='rmsim_wt_map_ui_preparation_v1',status='failed',started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),script_sha256=digest(Path(__file__).read_bytes()))
    try:
        package=args.package.resolve(strict=True);overview=read_json(package/'overview.json');source=read_json(package/'scene-source.json')
        require(overview['map_id']==source['id']=='wt_vlaanderen' and overview['source_sha256']==source['source_sha256'],'map source identity differs')
        resources,archives=get_sources(args.game_root.resolve(strict=True))
        if args.verify:verify(package,overview,source,resources,archives,report)
        else:prepare(args,package,overview,source,resources,archives,report)
    except Exception as exc:report.update(failure=str(exc),traceback=traceback.format_exc())
    report['finished_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    print(json.dumps(report,ensure_ascii=True))
    raise SystemExit(0 if report['status']=='passed' else 3)
