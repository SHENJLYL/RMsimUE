"""Generate local-only RMUC 2026 face textures from the official Appendices 5 and 6."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageOps

PDF_SHA256 = "77010458d06616b2bafad4400aad384f72e1b7007269c58061384de8fd0fb41b"
FACE_PX = (1080, 1000)  # 135 x 125 mm reference face, 8 px/mm
PATTERNS = {
    # Crop the central figure only. The mounting holes and plate perimeter are CAD geometry.
    "sentry": ((171, 112, 385, 305), (704, 630)),
    "outpost": ((204, 412, 351, 616), (472, 665)),
}


def generate(pdf: Path, output: Path, renderer: str):
    if hashlib.sha256(pdf.read_bytes()).hexdigest() != PDF_SHA256:
        raise ValueError("Expected the official RMUC 2026 Robot Specification V2.0.0, dated 2026-06-26")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        prefix = Path(tmp) / "appendix"
        subprocess.run([renderer, "-f", "102", "-l", "102", "-r", "320", "-png", "-gray",
                        "-singlefile", str(pdf), str(prefix)], check=True)
        with Image.open(prefix.with_suffix(".png")) as source:
            for name, (crop, extent) in PATTERNS.items():
                x0, y0, x1, y1 = crop
                pixels = (round(x0 * source.width / 555), round(y0 * source.height / 785),
                          round(x1 * source.width / 555), round(y1 * source.height / 785))
                signal = source.crop(pixels).convert("L").point(
                    lambda level: max(0, min(255, (level - 13) * 255 // 225)))
                box = signal.getbbox()
                if not box:
                    raise ValueError(f"Missing official symbol on the {name} face")
                ink = ImageOps.contain(signal.crop(box), extent, Image.Resampling.LANCZOS)
                face = Image.new("L", FACE_PX, color=12)
                corner = ((face.width - ink.width) // 2, (face.height - ink.height) // 2)
                face.paste(240, (*corner, corner[0] + ink.width, corner[1] + ink.height), ink)
                alpha = Image.new("L", FACE_PX, color=0)
                # 8 px/mm. The alpha channel marks 8 x 56 mm lamp optics at ±59 mm.
                for center in (face.width // 2 - 472, face.width // 2 + 472):
                    ImageDraw.Draw(alpha).rounded_rectangle(
                        (center - 32, 276, center + 31, 723), radius=8, fill=255)
                rgba = face.convert("RGBA")
                rgba.putalpha(alpha)
                rgba.save(output / f"{name}_sticker.png", optimize=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parents[2] / "assets/ui/armor_stickers")
    parser.add_argument("--pdftoppm", default="pdftoppm")
    args = parser.parse_args()
    generate(args.pdf, args.output, args.pdftoppm)
