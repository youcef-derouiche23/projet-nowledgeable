"""Génère des images d'avis de virement (1 transaction par image) pour tester l'OCR.

Usage : python -m scripts.generate_images [--output-dir data/incoming]
"""
import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from scripts.generate_csv import A, C, D, E, F, B

# (datetime, compte émetteur, compte destinataire, montant)
SCENARIO = {
    "avis_virement_001.png": ("2026-11-05 09:10:00", A, C, "6200.00"),   # flag
    "avis_virement_002.png": ("2026-11-05 11:25:00", D, E, "5000.00"),   # pile au seuil : pas de flag
    "avis_virement_003.png": ("2026-11-05 15:40:00", F, B, "480.50"),
}


def render(path: Path, datetime_str, src, dst, montant) -> None:
    lines = [
        "VIREMENT BANCAIRE", "",
        f"Date : {datetime_str}",
        f"IBAN origine : {src[0]}",
        f"Pays source : {src[1]}",
        f"Banque source : {src[2]}",
        f"IBAN destinataire : {dst[0]}",
        f"Pays destinataire : {dst[1]}",
        f"Montant : {montant} EUR",
    ]
    font = ImageFont.load_default(size=34)
    image = Image.new("RGB", (1250, 80 + 54 * len(lines)), "white")
    draw = ImageDraw.Draw(image)
    for i, line in enumerate(lines):
        draw.text((40, 40 + 54 * i), line, fill="black", font=font)
    image.save(path)


def generate(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, (dt, src, dst, montant) in SCENARIO.items():
        path = output_dir / name
        render(path, dt, src, dst, montant)
        written.append(path)
    return written


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="data/incoming")
    args = parser.parse_args()
    for p in generate(Path(args.output_dir)):
        print(f"OK {p}")
