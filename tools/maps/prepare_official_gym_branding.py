#!/usr/bin/env python3
"""Prepare official DJI/RoboMaster artwork for UE, without redrawing geometry.

Requires Python 3, Pillow (metadata only), and Inkscape. Originals are fetched
from their official source URLs only when missing and verified before use.
"""
import argparse
import base64
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request
import xml.etree.ElementTree as ET

from PIL import Image

ROOT = None
INKSCAPE = "inkscape"
SVG = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG)
ET.register_namespace("xlink", XLINK)

SOURCES = [
    {
        "file": "dji_logo_white_official.svg",
        "url": "https://www-cdn.djiits.com/dps/a4cb06c299208ffb34ecdcf8b2a5d94a.svg",
        "page": "https://www.dji.com/",
        "evidence": "Official website footer image, alt=DJI Logo, class=logo white",
        "sha256": "a46fd9e7b6654c3835d73752bd5edf970014be002fb16288b351e50609251af7",
    },
    {
        "file": "dji_logo_gray_official.svg",
        "url": "https://www-cdn.djiits.com/dps/176201ad61d0753e17d860c4ca117932.svg",
        "page": "https://www.dji.com/",
        "evidence": "Official website footer image, alt=DJI Logo, class=logo gray",
        "sha256": "0b46c97bd3490af9ff2476c9a200e2cc964f0af48403d2efa1fa5fcb94454ab1",
    },
    {
        "file": "robomaster_wordmark_official.svg",
        "url": "https://rm-static.djicdn.com/robomasters/dps/f4ed821f19e0c9fdd826f35953c67cd7.svg",
        "page": "https://www.robomaster.com/zh-CN",
        "evidence": "Official site configuration v1.common.popo.icon.logo",
        "sha256": "5cdb8f89a26d2dea694652d8db7a95f21164e002df834f1a2b120612bc99e31b",
    },
    {
        "file": "robomaster_full_black_official.png",
        "url": "https://rm-static.djicdn.com/robomasters/public/document/RoboMaster-mecha-logo.png",
        "page": "https://www.robomaster.com/en-US/resource/pages/announcement/713",
        "evidence": "Official announcement attachment: RoboMaster Official Logo; dark gray original",
        "sha256": "9eb61acdc7e8bfa46eef2322ba40178d3bde9563fa8be941886ae41261fd1cec",
    },
    {
        "file": "robomaster_full_blue_official.png",
        "url": "https://rm-static.djicdn.com/robomasters/public/document/RoboMaster-mecha-logo-blue.png",
        "page": "https://www.robomaster.com/en-US/resource/pages/announcement/713",
        "evidence": "Official announcement attachment: RoboMaster Official Logo, blue",
        "sha256": "5ece57c380833c3c89232e37b5c77cee55e925506cc834557096f544e9cd928c",
    },
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def element(tag, **attrs):
    return ET.Element(f"{{{SVG}}}{tag}", {k.replace("_", "-"): str(v) for k, v in attrs.items()})


def svg(width, height, background=None):
    root = element("svg", width=width, height=height, viewBox=f"0 0 {width} {height}")
    if background:
        root.append(element("rect", width=width, height=height, fill=background))
    return root


def write_svg(name, root):
    path = ROOT / name
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def data_url(path):
    mime = "image/svg+xml" if path.suffix == ".svg" else "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def place_image(root, path, x, y, width, height):
    if path.suffix == ".svg":
        # Inline the source tree: SVG-as-image can be rasterized at the tiny
        # website intrinsic size by renderers, then blurred by enlargement.
        im = copy.deepcopy(ET.parse(path).getroot())
        for name, value in {"x": x, "y": y, "width": width, "height": height,
                            "preserveAspectRatio": "xMidYMid meet"}.items():
            im.set(name, str(value))
        root.append(im)
        return im
    im = element("image", x=x, y=y, width=width, height=height, preserveAspectRatio="xMidYMid meet")
    im.set(f"{{{XLINK}}}href", data_url(path))
    root.append(im)
    return im


def recolor_vector(source_name, target_name, color):
    original = ET.parse(ROOT / source_name).getroot()
    root = copy.deepcopy(original)
    before = [e.get("d") for e in original.iter() if e.tag == f"{{{SVG}}}path"]
    for e in root.iter():
        if e.get("fill") not in (None, "none"):
            e.set("fill", color)
    after = [e.get("d") for e in root.iter() if e.tag == f"{{{SVG}}}path"]
    assert before == after, "Logo path geometry must remain byte-for-byte identical"
    return write_svg(target_name, root)


def monochrome_full(target_name, white):
    # Embed the exact official bitmap; matrix changes RGB only, retaining alpha.
    root = svg(1800, 1487)
    defs = element("defs")
    filt = element("filter", id="mono", x="0%", y="0%", width="100%", height="100%",
                   color_interpolation_filters="sRGB")
    v = "1" if white else "0"
    filt.append(element("feColorMatrix", type="matrix", values=f"0 0 0 0 {v} 0 0 0 0 {v} 0 0 0 0 {v} 0 0 0 1 0"))
    defs.append(filt)
    root.append(defs)
    im = place_image(root, ROOT / "robomaster_full_black_official.png", 0, 0, 1800, 1487)
    im.set("filter", "url(#mono)")
    return write_svg(target_name, root)


def render(path, width, height):
    out = path.with_suffix(".png")
    subprocess.run([
        INKSCAPE, str(path), "--export-type=png", f"--export-filename={out}",
        f"--export-width={width}", f"--export-height={height}",
        "--export-png-color-mode=RGBA_8",
    ], check=True, capture_output=True)
    with Image.open(out) as im:
        assert im.size == (width, height)
        assert im.mode == "RGBA", im.mode
    return out


def metadata(path):
    result = {"file": path.name, "sha256": sha(path), "bytes": path.stat().st_size}
    if path.suffix == ".png":
        with Image.open(path) as im:
            alpha = im.convert("RGBA").getchannel("A")
            result.update(width=im.width, height=im.height, mode=im.mode,
                          alpha_range=list(alpha.getextrema()),
                          transparent=alpha.getextrema()[0] < 255,
                          alpha_bbox=list(alpha.getbbox() or (0, 0, 0, 0)))
    else:
        root = ET.parse(path).getroot()
        result.update(width=float(root.get("width")), height=float(root.get("height")),
                      viewBox=root.get("viewBox"), background="transparent")
    return result


def main():
    global ROOT, INKSCAPE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inkscape", default="inkscape")
    args = parser.parse_args()
    ROOT = args.output.resolve()
    ROOT.mkdir(parents=True, exist_ok=True)
    INKSCAPE = args.inkscape
    originals = []
    for source in SOURCES:
        path = ROOT / source["file"]
        if not path.exists():
            req = urllib.request.Request(source["url"], headers={"User-Agent": "Mozilla/5.0"})
            path.write_bytes(urllib.request.urlopen(req, timeout=60).read())
        assert sha(path) == source["sha256"], f"Source changed: {path.name}"
        originals.append({**source, **metadata(path)})

    derived = []
    for color_name, color in (("black", "#000000"), ("white", "#ffffff")):
        for brand, source, width, height in (
            ("dji", "dji_logo_white_official.svg", 1792, 1024),
            ("robomaster_wordmark", "robomaster_wordmark_official.svg", 4000, 280),
        ):
            vector = recolor_vector(source, f"{brand}_{color_name}.svg", color)
            bitmap = render(vector, width, height)
            derived.append({**metadata(bitmap), "svg": vector.name, "sources": [source],
                            "transformation": "Fill color only; every original path d preserved; uniform SVG rasterization",
                            "usage": "Transparent logo for decal or masked material"})
        full = monochrome_full(f"robomaster_full_{color_name}.svg", color_name == "white")
        bitmap = render(full, 1800, 1487)
        derived.append({**metadata(bitmap), "svg": full.name,
                        "sources": ["robomaster_full_black_official.png"],
                        "transformation": "Official bitmap embedded unchanged; SVG color matrix changes RGB only, preserving alpha",
                        "usage": "Transparent full emblem at original native dimensions"})

    for background, color in (("white", "black"), ("black", "white")):
        bg = "#ffffff" if background == "white" else "#080b10"
        divider = "#d9dce0" if background == "white" else "#39404a"
        horizontal = svg(4096, 1024, bg)
        place_image(horizontal, ROOT / f"robomaster_wordmark_{color}.svg", 180, 412, 2750, 192.5)
        horizontal.append(element("rect", x=3090, y=296, width=3, height=432, fill=divider))
        place_image(horizontal, ROOT / f"dji_{color}.svg", 3280, 337.7, 610, 348.6)
        path = write_svg(f"sign_horizontal_4x1_{background}.svg", horizontal)
        output = render(path, 4096, 1024)
        derived.append({**metadata(output), "svg": path.name,
                        "sources": ["robomaster_wordmark_official.svg", "dji_logo_white_official.svg"],
                        "transformation": "Unchanged logo path geometry, monochrome fills, uniform fit, plain background and divider",
                        "usage": "Opaque horizontal sign, aspect ratio 4:1"})

        vertical = svg(1024, 2048, bg)
        place_image(vertical, ROOT / f"robomaster_full_{color}.svg", 112, 248, 800, 800 * 1487 / 1800)
        vertical.append(element("rect", x=192, y=1110, width=640, height=2, fill=divider))
        place_image(vertical, ROOT / f"dji_{color}.svg", 212, 1320, 600, 600 * 24 / 42)
        path = write_svg(f"sign_vertical_1x2_{background}.svg", vertical)
        output = render(path, 1024, 2048)
        derived.append({**metadata(output), "svg": path.name,
                        "sources": ["robomaster_full_black_official.png", "dji_logo_white_official.svg"],
                        "transformation": "Official bitmap silhouette and DJI paths preserved, monochrome color, uniform fit, plain background and divider",
                        "usage": "Opaque vertical sign, aspect ratio 1:2"})

    preview = svg(1440, 1000, "#b8bec6")
    for x, y, w, h, name in (
        (30, 30, 1000, 250, "sign_horizontal_4x1_white.png"),
        (30, 310, 1000, 250, "sign_horizontal_4x1_black.png"),
        (1060, 30, 180, 360, "sign_vertical_1x2_white.png"),
        (1250, 30, 180, 360, "sign_vertical_1x2_black.png"),
        (40, 630, 400, 330.44, "robomaster_full_black.png"),
        (510, 700, 400, 228.57, "dji_black.png"),
        (1030, 630, 400, 330.44, "robomaster_full_white.png"),
    ):
        place_image(preview, ROOT / name, x, y, w, h)
    render(write_svg("branding_preview.svg", preview), 1440, 1000)

    manifest = {
        "schema_version": 1,
        "purpose": "Official DJI / RoboMaster logo artwork for RMsimUE5 venue signage",
        "originals": originals,
        "derived": derived,
        "preferred_textures": [
            "sign_horizontal_4x1_white.png", "sign_vertical_1x2_white.png",
            "dji_black.png", "robomaster_full_black.png", "robomaster_wordmark_black.png",
        ],
        "preview": "branding_preview.png",
        "generator": "prepare_branding.py; Python + Inkscape; no generative images or traced/redrawn logo geometry",
        "ue_import": {
            "sRGB": True,
            "compression": "Default or BC7 for sign BaseColor; verify alpha compression when using transparent logo masks",
            "addressing": "Clamp",
            "aspect_ratio": "Preserve marked aspect ratio through plane scale/UV or rerender from supplied SVG; do not stretch letters",
        },
        "rights_note": "Official brand artwork. DJI and RoboMaster trademark rights remain with their owners. Venue asset CC-BY licensing is separate and does not relicense these logos.",
    }
    (ROOT / "branding_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"originals": len(originals), "png_outputs": len(derived), "manifest": str(ROOT / "branding_manifest.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
