"""Restore source SketchUp materials without changing source geometry or source files.

Creates a new candidate bundle. Existing GLB accessors, normals, transforms and
indices are retained. Legacy component paint inheritance is recovered per node;
four previously omitted BMP textures and source transparency are restored.
Colorized textures follow SketchUp's documented HLS delta procedure.
"""
from pathlib import Path
from copy import deepcopy
import argparse, colorsys, hashlib, inspect, io, json, re, shutil, struct
import numpy as np
from PIL import Image
from openskp import SkpFile
from openskp import legacy

def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def linear(x):
    x=float(x)/255.
    return x/12.92 if x<=.04045 else ((x+.055)/1.055)**2.4

def hls(rgb):
    q=[float(np.float32(v)/np.float32(255)) for v in rgb]
    h,l,s=colorsys.rgb_to_hls(*q)
    return (-1. if abs(max(q)-min(q))<.001 else h*360.,l,s)

def source_colorize(image, average, target, shift):
    # Equivalent HLS-delta procedure, with source stored average rather than
    # a guessed average sampled from a recompressed texture.
    ah,al,ass=hls(average); th,tl,ts=hls(target)
    mono_a=len(set(average))==1; mono_t=len(set(target))==1
    if mono_a and mono_t: dh,dl,ds=0.,tl-al,0.
    elif mono_t: dh,dl,ds=0.,tl-al,-1.
    elif mono_a: dh,dl,ds=th,tl-al,ts
    else: dh,dl,ds=(th-ah if shift else th),tl-al,ts-ass
    if abs(dh)+abs(dl)+abs(ds)<1e-12:
        return image.convert("RGBA"),[dh,dl,ds]
    rgba=np.asarray(image.convert("RGBA")).copy()
    flat=rgba.reshape(-1,4)
    for px in flat:
        h,l,s=hls(px[:3]);h=(h+dh if shift else dh)%360.
        l=min(1.,max(.01,l+dl));s=min(1.,max(0.,s+ds))
        c=(l,l,l) if abs(s)<.001 else colorsys.hls_to_rgb(h/360.,l,s)
        px[:3]=[min(255,max(0,int(v*255.))) for v in c]
    return Image.fromarray(rgba),[dh,dl,ds]

def parse_source(path):
    # The installed legacy reader omits image-average RGB from its public
    # material dictionary. Capture that already-parsed field in memory.
    averages={}
    source=inspect.getsource(legacy._texture_block)
    needle="    colorized = blob[0] == 2"
    assert source.count(needle)==1
    modified=source.replace(needle,
        "    _audit_texture_averages[fname] = tuple(avg[:4])\n"+needle)
    namespace=dict(legacy.__dict__);namespace["_audit_texture_averages"]=averages
    exec(compile(modified,"<source-average-audit>","exec"),namespace)
    previous=legacy._texture_block
    try:
        legacy._texture_block=namespace["_texture_block"]
        skp=SkpFile.open(str(path));skp.parse()
    finally:
        legacy._texture_block=previous
    return skp,averages,hashlib.sha256(source.encode()).hexdigest()

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--source",type=Path,required=True)
    ap.add_argument("--original-glb",type=Path,required=True)
    ap.add_argument("--original-bundle",type=Path,required=True)
    ap.add_argument("--output",type=Path,required=True)
    a=ap.parse_args()
    if a.output.exists():raise FileExistsError(a.output)
    a.output.mkdir(parents=True);(a.output/"textures").mkdir()
    (a.output/"source-textures").mkdir()
    skp,averages,reader_sha=parse_source(a.source)
    p=skp._parsed; source_mats=p["materials"]
    original=a.original_glb.read_bytes()
    nj=struct.unpack_from("<I",original,12)[0]
    g=json.loads(original[20:20+nj])
    bin_at=20+nj;nb=struct.unpack_from("<I",original,bin_at)[0]
    binary=bytearray(original[bin_at+8:bin_at+8+nb])
    # Exact parsed-node traversal order used by OpenSKP instanced_glb.export.
    inherited={0:None}; definition={0:"ROOT"}; counter=[0]
    def walk(did,parent_material,chain):
        for inst in p["defs_dict"][did]["builder"].instances:
            counter[0]+=1;index=counter[0]
            mat=inst.get("material_id") or parent_material
            inherited[index]=mat;definition[index]=inst["ref_idx"]
            if inst["ref_idx"] in chain:raise RuntimeError("Recursive definition")
            walk(inst["ref_idx"],mat,chain+(inst["ref_idx"],))
    walk("ROOT",None,("ROOT",))
    old_node_ids={int(n["name"][1:]) for n in g["nodes"]
                  if re.fullmatch(r"n\d+",n.get("name",""))}
    assert old_node_ids==set(inherited),(len(old_node_ids),len(inherited))
    report={"source_sha256":sha(a.source),"original_glb_sha256":sha(a.original_glb),
      "original_bundle":str(a.original_bundle),"legacy_reader_sha256":reader_sha,
      "source_layer_color":p["layer_colors"],
      "default_face_policy":"Unpainted default face uses neutral white; legacy SKP style colors are not exposed by OpenSKP. Layer0 display color is not an authored surface material.",
      "colorize_reference":"https://github.com/SketchUp/sketchup-colorize-algorithm",
      "textures":[],"material_variants":{},"inherited_assignments":[]}
    # Original GLB slots with explicit authored materials; colors aliased by
    # OpenSKP share identical appearance. Default Layer0 slots are None.
    old_to_source=[None,22,None,25,50,25,24,29,32,62,37,44,43,41,44,46,47,41,42,26,38,34,65,34,33,51,48,30,57,58,55,28,63,22,54,39,39,35,27,68,67,69,52,45]
    assert len(g["materials"])==len(old_to_source)
    report["ambiguous_equal_color_aliases"]={"3,5":["school_metal_cabinet","chrome"],"10,20":["school_window_black","[Color_007]","*33"],"7":["*8","*29","*32"]}
    original_materials=deepcopy(g["materials"]); original_meshes=deepcopy(g["meshes"])
    original_texcoords={i:deepcopy(ac) for i,ac in enumerate(g["accessors"])}
    g["materials"]=[];g["images"]=[];g["textures"]=[];g["samplers"]=[{"wrapS":10497,"wrapT":10497,"magFilter":9729,"minFilter":9987}]
    material_data={}; texture_ids={}
    def append_buffer(data,target=None):
        while len(binary)%4:binary.append(0)
        view={"buffer":0,"byteOffset":len(binary),"byteLength":len(data)}
        if target:view["target"]=target
        index=len(g["bufferViews"]);g["bufferViews"].append(view);binary.extend(data)
        return index
    for name,mat in source_mats.items():
        tex=mat.get("texture")
        if not tex:continue
        mid=next(k for k,v in p["material_id_to_name"].items() if v==name)
        raw=tex["data"];raw_name="SU_"+str(mid)+"_"+Path(tex["filename"]).name
        (a.output/"source-textures"/raw_name).write_bytes(raw)
        im=Image.open(io.BytesIO(raw));im.load()
        before=np.asarray(im.convert("RGBA")).copy()
        deltas=None
        if mat.get("colorized"):
            avg=averages[tex["filename"]][:3]
            target=[mat["color"][c] for c in ("r","g","b")]
            im,deltas=source_colorize(im,avg,target,mat.get("colorize_type",0)==0)
        out=a.output/"textures"/("SU_"+str(mid)+".png")
        im.save(out,format="PNG")
        rgba=np.asarray(im.convert("RGBA"))
        alpha=rgba[:,:,3]
        has_alpha=bool((alpha<255).any())
        pixels_preserved=bool(np.array_equal(before,rgba)) if not mat.get("colorized") else None
        entry={"source_name":name,"source_id":mid,"source_filename":tex["filename"],
          "source_bytes_sha256":hashlib.sha256(raw).hexdigest(),"output":out.relative_to(a.output).as_posix(),
          "output_sha256":sha(out),"dimensions":list(im.size),
          "colorized":mat.get("colorized",False),"colorize_type":mat.get("colorize_type",0),
          "stored_average":list(averages[tex["filename"]][:3]),
          "colorize_hls_delta":deltas,"uncolorized_pixels_identical":pixels_preserved,
          "has_alpha":has_alpha,"tile_size_inches":[tex["x_scale"],tex["y_scale"]]}
        report["textures"].append(entry)
        idx=len(g["images"])
        g["images"].append({"name":"SU_"+str(mid),"mimeType":"image/png","bufferView":append_buffer(out.read_bytes())})
        g["textures"].append({"source":idx,"sampler":0})
        texture_ids[mid]=(idx,entry)
    material_index={}
    def get_material(mid,double_sided):
        key=(mid,double_sided)
        if key in material_index:return material_index[key]
        suffix="D" if double_sided else "S"
        slot=("SU_Default" if mid is None else "SU_M"+str(mid))+"_"+suffix
        if mid is None:
            name="Unpainted default";rgb=[255,255,255];alpha=1.;tex=None
        else:
            name=p["material_id_to_name"][mid];mat=source_mats[name]
            rgb=[mat["color"][c] for c in ("r","g","b")]
            alpha=mat.get("transparency",1.);tex=texture_ids.get(mid)
        factor=[1.,1.,1.,alpha] if tex else [*(linear(v) for v in rgb),alpha]
        mode="BLEND" if alpha<.999 else "MASK" if tex and tex[1]["has_alpha"] else "OPAQUE"
        pr={"baseColorFactor":factor,"roughnessFactor":.74,"metallicFactor":0.}
        d={"name":slot,"pbrMetallicRoughness":pr,"doubleSided":double_sided,"alphaMode":mode,
           "extras":{"sketchup_material_id":mid,"sketchup_material_name":name,"source_rgb_srgb":rgb}}
        data={"base_color_factor":factor,"roughness_factor":.74,"metallic_factor":0.,
          "double_sided":double_sided,"alpha_mode":mode,"semantic":"glass" if mode=="BLEND" else "source",
          "source_material_id":mid,"source_material_name":name,"source_color_srgb":rgb,
          "color_space":"linear_constant_srgb_texture","source_opacity":alpha}
        if tex:
            pr["baseColorTexture"]={"index":tex[0]}
            data["base_color_texture"]=tex[1]["output"]
        if mode=="MASK":d["alphaCutoff"]=.5;data["alpha_cutoff"]=.5
        material_data[slot]=data
        material_index[key]=len(g["materials"]);g["materials"].append(d)
        report["material_variants"][slot]=data
        return material_index[key]
    def adjusted_uv(accessor_id,mid):
        ac=original_texcoords[accessor_id];view=g["bufferViews"][ac["bufferView"]]
        assert ac["componentType"]==5126 and ac["type"]=="VEC2" and "byteStride" not in view
        offset=view.get("byteOffset",0)+ac.get("byteOffset",0)
        uv=np.frombuffer(binary,dtype="<f4",count=ac["count"]*2,offset=offset).copy().reshape(-1,2)
        tex=source_mats[p["material_id_to_name"][mid]]["texture"]
        # The old unpainted primitive had a 1 inch fallback projection.
        # _add_face_side retains V as 1-v after export to GLB.
        uv[:,0]/=tex["x_scale"]
        uv[:,1]=(uv[:,1]-1.)/tex["y_scale"]+1.
        vac={"bufferView":append_buffer(uv.astype("<f4").tobytes(),34962),
          "componentType":5126,"count":len(uv),"type":"VEC2"}
        index=len(g["accessors"]);g["accessors"].append(vac);return index
    # Mesh resources are duplicated only for distinct inherited paint contexts.
    g["meshes"]=[]; variants={};changed_nodes=0
    for nd in g["nodes"]:
        if "mesh" not in nd:continue
        match=re.fullmatch(r"n(\d+)_g\d+",nd.get("name",""))
        if not match:raise ValueError("Unexpected primitive node: "+repr(nd.get("name")))
        node_id=int(match[1]);old_idx=nd["mesh"];old=original_meshes[old_idx]
        assert len(old["primitives"])==1
        old_mid=old["primitives"][0]["material"];source_mid=old_to_source[old_mid]
        is_default=old_mid in(0,2)
        if is_default:source_mid=inherited[node_id]
        key=(old_idx,source_mid if is_default else "explicit")
        if key not in variants:
            mesh=deepcopy(old);mesh["name"]=old.get("name","Mesh")+"_"+str(source_mid)
            pr=mesh["primitives"][0]
            ds=bool(original_materials[old_mid].get("doubleSided",False))
            pr["material"]=get_material(source_mid,ds)
            if is_default and source_mid in texture_ids and "TEXCOORD_0" in pr["attributes"]:
                pr["attributes"]["TEXCOORD_0"]=adjusted_uv(pr["attributes"]["TEXCOORD_0"],source_mid)
            variants[key]=len(g["meshes"]);g["meshes"].append(mesh)
        nd["mesh"]=variants[key]
        if is_default and source_mid is not None:
            changed_nodes+=1
            report["inherited_assignments"].append({"node":nd["name"],"definition":definition[node_id],"source_material_id":source_mid})
    g["buffers"]=[{"byteLength":len(binary)}]
    packed=json.dumps(g,separators=(",",":")).encode("utf8")
    packed+=b" "*((-len(packed))%4);binary+=b"\0"*((-len(binary))%4)
    target=a.output/"Indoor_Basketball_Gym_source_fidelity_mm.glb"
    total=12+8+len(packed)+8+len(binary)
    target.write_bytes(struct.pack("<III",0x46546C67,2,total)+struct.pack("<II",len(packed),0x4E4F534A)+packed+struct.pack("<II",len(binary),0x004E4942)+binary)
    report.update(corrected_glb_sha256=sha(target),corrected_glb=str(target),
       original_geometry_accessors_unchanged=True,legacy_instance_contexts=len(inherited),
       inherited_primitive_assignments_restored=changed_nodes,
       source_texture_count=len(texture_ids),material_variant_count=len(g["materials"]),
       geometry_note="POSITION/NORMAL/index accessors and all node transforms retained byte-identical; only inherited textured default UV accessors have the recorded tile-size correction.")
    (a.output/"source-materials.json").write_text(json.dumps(material_data,indent=2)+"\n",encoding="utf8")
    (a.output/"source-material-audit.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf8")
    print(json.dumps({k:v for k,v in report.items() if k not in("textures","material_variants","inherited_assignments")},indent=2))
if __name__=="__main__":main()
