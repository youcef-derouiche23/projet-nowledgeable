"""Extraction de transactions depuis des images (OCR Tesseract).

Une image = un avis de virement. Le résultat est un CSV au MÊME format que les autres
sources : il suit ensuite exactement le même flux (validation, traitement, base de données).
"""
import csv
import io
import re
from decimal import Decimal, InvalidOperation

from src.loader import EXPECTED_COLUMNS

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg")

# Libellé écrit sur l'image -> colonne du CSV
LABELS = {
    "date": "datetime_transaction",
    "iban origine": "iban_origine",
    "pays source": "pays_source",
    "banque source": "banque_source",
    "iban destinataire": "iban_destinataire",
    "pays destinataire": "pays_destinataire",
    "montant": "montant",
}

IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$")
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


class OcrError(ValueError):
    """L'extraction ou la validation des informations de l'image a échoué."""


def ocr_text(image_bytes: bytes) -> str:
    """Lit le texte d'une image avec Tesseract."""
    import pytesseract
    from PIL import Image

    try:
        image = Image.open(io.BytesIO(image_bytes))
        return pytesseract.image_to_string(image, config="--psm 6")
    except pytesseract.TesseractNotFoundError as exc:
        raise OcrError("Tesseract n'est pas installé (macOS : brew install tesseract)") from exc


def extract_fields(text: str, name: str) -> dict[str, str]:
    """Repère les champs 'libellé : valeur' dans le texte lu par l'OCR."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        label, value = line.split(":", 1)
        column = LABELS.get(re.sub(r"\s+", " ", label.strip().lower()))
        if column and column not in fields:
            fields[column] = value.strip()
    missing = [c for c in EXPECTED_COLUMNS if c not in fields]
    if missing:
        raise OcrError(f"{name}: champs introuvables par l'OCR : {missing}")
    return fields


def normalize_fields(fields: dict[str, str], name: str) -> dict[str, str]:
    """Nettoie les erreurs classiques de l'OCR et valide chaque champ."""
    out = dict(fields)
    for col in ("iban_origine", "iban_destinataire"):
        out[col] = re.sub(r"\s+", "", out[col]).upper()          # espaces parasites
        if not IBAN_RE.match(out[col]):
            raise OcrError(f"{name}: {col} invalide après OCR : {out[col]!r}")
    for col in ("pays_source", "pays_destinataire"):
        out[col] = out[col].strip().upper()
        if not COUNTRY_RE.match(out[col]):
            raise OcrError(f"{name}: {col} invalide après OCR : {out[col]!r}")
    out["banque_source"] = out["banque_source"].strip()
    if not out["banque_source"]:
        raise OcrError(f"{name}: banque_source vide après OCR")
    out["datetime_transaction"] = re.sub(r"\s+", " ", out["datetime_transaction"]).strip()
    if not DATETIME_RE.match(out["datetime_transaction"]):
        raise OcrError(f"{name}: date invalide après OCR : {out['datetime_transaction']!r}")
    amount = re.sub(r"(?i)eur|€|\s", "", out["montant"]).replace(",", ".")
    try:
        out["montant"] = f"{Decimal(amount):.2f}"
    except InvalidOperation as exc:
        raise OcrError(f"{name}: montant invalide après OCR : {out['montant']!r}") from exc
    return out


def image_to_csv(image_bytes: bytes, name: str) -> str:
    """Image -> OCR -> extraction -> validation -> texte CSV au format standard."""
    fields = normalize_fields(extract_fields(ocr_text(image_bytes), name), name)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(EXPECTED_COLUMNS)
    writer.writerow([fields[c] for c in EXPECTED_COLUMNS])
    return buffer.getvalue()
