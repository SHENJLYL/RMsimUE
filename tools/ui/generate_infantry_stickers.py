"""Fit the official 3/4 digits to the existing AIM small armor texture."""

import argparse
import hashlib
from pathlib import Path
import subprocess
import tempfile

from PIL import Image, ImageChops, ImageOps


REFERENCE_SHA256 = "f3b168cf3df10ca0f7ee92884586537d658da6b05e752d4c19bcdea0ed57eb9e"
REFERENCE_PAGE_PX = (827, 1170)
# Digit-only crop and enclosing plate size, measured on 100 dpi reference pages.
DIGITS = {
    3: (103, (300, 618, 529, 978), (440, 407)),
    4: (104, (284, 134, 548, 468), (421, 389)),
}


def generate(pdf: Path, sentry: Path, output: Path, renderer: str):
    if hashlib.sha256(pdf.read_bytes()).hexdigest() != REFERENCE_SHA256:
        raise ValueError("Expected RoboMaster 2026 Robot Specification V1.2.0 dated 2025-12-30")
    with Image.open(sentry) as source:
        face = source.convert("RGB")
        alpha = source.getchannel("A")
    background = face.getpixel((0, 0))
    brightest = tuple(channel[1] for channel in face.getextrema())
    old_print = ImageChops.difference(face, Image.new("RGB", face.size, background)).getbbox()
    if old_print:
        face.paste(background, old_print)

    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for number, (page, crop, plate) in DIGITS.items():
            raster = Path(tmp) / f"appendix-{number}"
            subprocess.run([renderer, "-f", str(page), "-l", str(page), "-r", "320",
                            "-png", "-gray", "-singlefile", str(pdf), str(raster)], check=True)
            with Image.open(raster.with_suffix(".png")) as reference:
                scale_x = reference.width / REFERENCE_PAGE_PX[0]
                scale_y = reference.height / REFERENCE_PAGE_PX[1]
                digit = reference.crop(tuple(
                    round(value * scale) for value, scale in zip(crop, (scale_x, scale_y) * 2)
                )).convert("L").point(lambda level: max(0, min(255, (level - 13) * 255 // 225)))
            digit_bounds = digit.point(lambda level: 255 if level >= 96 else 0).getbbox()
            if not digit_bounds:
                raise ValueError(f"Missing digit {number} on reference page {page}")
            digit = digit.crop(digit_bounds)
            extent = (
                round(digit.width * face.width / (plate[0] * scale_x)),
                round(digit.height * face.height / (plate[1] * scale_y)),
            )
            ink = ImageOps.contain(digit, extent, Image.Resampling.LANCZOS)
            center = ((face.width - ink.width) // 2, (face.height - ink.height) // 2)
            sticker = face.copy()
            sticker.paste(brightest, (*center, center[0] + ink.width, center[1] + ink.height), ink)
            sticker.putalpha(alpha)
            sticker.save(output / f"infantry_{number}_sticker.png", optimize=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    packaged = Path(__file__).resolve().parents[2] / "assets/ui/armor_stickers"
    parser.add_argument("--sentry", type=Path, default=packaged / "sentry_sticker.png")
    parser.add_argument("--output", type=Path, default=packaged)
    parser.add_argument("--pdftoppm", default="pdftoppm")
    args = parser.parse_args()
    generate(args.pdf, args.sentry, args.output, args.pdftoppm)
