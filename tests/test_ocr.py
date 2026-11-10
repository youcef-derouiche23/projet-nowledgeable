"""Tests de l'option OCR : image -> OCR -> extraction -> même flux que les CSV -> base."""
import os
import shutil
from decimal import Decimal

import pytest

from scripts import generate_images
from src import storage
from src.loader import parse_transactions
from src.ocr import OcrError, extract_fields, image_to_csv, normalize_fields
from src.pipeline import run_pipeline

# En local, les tests qui lisent vraiment une image sont ignorés si Tesseract est absent.
# En CI (variable CI=true), Tesseract est installé : ces tests doivent s'exécuter.
requires_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None and not os.environ.get("CI"),
    reason="Tesseract non installé (macOS : brew install tesseract)",
)

CLEAN_TEXT = """VIREMENT BANCAIRE
Date : 2026-11-05 09:10:00
IBAN origine : FR76 3000 6000 0112 3456 7890 189
Pays source : fr
Banque source : BNP Paribas
IBAN destinataire : DE89370400440532013000
Pays destinataire : DE
Montant : 6 200,00 EUR
"""


# ---------- Extraction et validation (sans Tesseract) ----------

def test_extract_and_normalize_fields():
    fields = normalize_fields(extract_fields(CLEAN_TEXT, "test.png"), "test.png")
    assert fields["iban_origine"] == "FR7630006000011234567890189"   # espaces retirés
    assert fields["pays_source"] == "FR"                              # mis en majuscules
    assert fields["montant"] == "6200.00"                             # virgule, espaces et EUR traités
    assert fields["datetime_transaction"] == "2026-11-05 09:10:00"


def test_missing_field_is_rejected():
    with pytest.raises(OcrError, match="champs introuvables"):
        extract_fields("VIREMENT BANCAIRE\nDate : 2026-11-05 09:10:00\n", "test.png")


@pytest.mark.parametrize("column, bad_value", [
    ("iban_origine", "FR76"),
    ("pays_source", "FRANCE"),
    ("datetime_transaction", "05/11/2026"),
    ("montant", "beaucoup"),
])
def test_invalid_values_are_rejected(column, bad_value):
    fields = extract_fields(CLEAN_TEXT, "test.png")
    fields[column] = bad_value
    with pytest.raises(OcrError):
        normalize_fields(fields, "test.png")


# ---------- Lecture réelle d'images avec Tesseract ----------

@requires_tesseract
def test_ocr_reads_generated_images(tmp_path):
    expected_amounts = {
        "avis_virement_001.png": Decimal("6200.00"),
        "avis_virement_002.png": Decimal("5000.00"),
        "avis_virement_003.png": Decimal("480.50"),
    }
    for path in generate_images.generate(tmp_path):
        rows = parse_transactions(image_to_csv(path.read_bytes(), path.name), path.name)
        assert len(rows) == 1
        assert rows[0]["montant"] == expected_amounts[path.name]
    first = parse_transactions(image_to_csv((tmp_path / "avis_virement_001.png").read_bytes(), "x"), "x")[0]
    assert first["iban_origine"] == "FR7630006000011234567890189"
    assert first["banque_source"] == "BNP Paribas"
    assert first["iban_destinataire"] == "DE89370400440532013000"


@requires_tesseract
def test_pipeline_processes_csv_and_images_together(db, uploaded_csv, tmp_path):
    for path in generate_images.generate(tmp_path):
        storage.upload_file(uploaded_csv.client, uploaded_csv.bucket, path)

    assert run_pipeline(uploaded_csv.client, uploaded_csv.bucket, retry_delay=0, s3_retry_delay=0) == 6

    assert db.execute("SELECT count(*) FROM processed_files").fetchone()[0] == 6
    assert db.execute("SELECT count(*) FROM transactions").fetchone()[0] == 19     # 16 CSV + 3 images
    flagged_from_images = {r[0] for r in db.execute(
        "SELECT t.montant FROM transactions t JOIN processed_files f ON f.id = t.file_id "
        "WHERE f.filename LIKE 'avis_virement%' AND t.flag")}
    assert flagged_from_images == {Decimal("6200.00")}      # 5000.00 pile au seuil : pas de flag
    # Relancer ne retraite rien (anti-doublon, y compris pour les images)
    assert run_pipeline(uploaded_csv.client, uploaded_csv.bucket, retry_delay=0, s3_retry_delay=0) == 0


@requires_tesseract
def test_unreadable_image_stops_pipeline_with_clear_error(db, s3):
    from PIL import Image
    import io
    blank = io.BytesIO()
    Image.new("RGB", (400, 200), "white").save(blank, format="PNG")
    s3.client.put_object(Bucket=s3.bucket, Key="vide.png", Body=blank.getvalue())

    with pytest.raises(OcrError, match="champs introuvables"):
        run_pipeline(s3.client, s3.bucket, retry_delay=0, s3_retry_delay=0)


@requires_tesseract
def test_processed_images_are_not_ocr_again(db, uploaded_csv, tmp_path, monkeypatch):
    for path in generate_images.generate(tmp_path):
        storage.upload_file(uploaded_csv.client, uploaded_csv.bucket, path)
    run_pipeline(uploaded_csv.client, uploaded_csv.bucket, retry_delay=0, s3_retry_delay=0)

    import src.ocr

    def forbidden(*args, **kwargs):
        raise AssertionError("l'OCR ne doit pas être relancé sur une image déjà traitée")

    monkeypatch.setattr(src.ocr, "image_to_csv", forbidden)
    assert run_pipeline(uploaded_csv.client, uploaded_csv.bucket, retry_delay=0, s3_retry_delay=0) == 0
