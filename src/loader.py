"""Chargement des fichiers CSV (entrées/sorties uniquement, aucun calcul métier)."""
import csv
import hashlib
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

EXPECTED_COLUMNS = [
    "datetime_transaction", "iban_origine", "pays_source", "banque_source",
    "iban_destinataire", "pays_destinataire", "montant",
]


class InvalidCsvError(ValueError):
    """Le fichier CSV ne respecte pas le format attendu."""


def list_csv_files(directory: Path) -> list[Path]:
    return sorted(Path(directory).glob("*.csv"))


def compute_content_hash(data: bytes) -> str:
    """SHA-256 du contenu (sert à identifier un fichier déjà traité)."""
    return hashlib.sha256(data).hexdigest()


def compute_file_hash(path: Path) -> str:
    return compute_content_hash(Path(path).read_bytes())


def parse_transactions(content: str, source_name: str) -> list[dict]:
    """Transforme le texte d'un CSV en transactions typées (montant en Decimal)."""
    reader = csv.DictReader(io.StringIO(content, newline=""))
    if reader.fieldnames != EXPECTED_COLUMNS:
        raise InvalidCsvError(
            f"{source_name}: colonnes attendues {EXPECTED_COLUMNS}, reçues {reader.fieldnames}"
        )
    transactions = []
    for line_no, row in enumerate(reader, start=2):
        try:
            row["datetime_transaction"] = datetime.strptime(
                row["datetime_transaction"], "%Y-%m-%d %H:%M:%S"
            )
            row["montant"] = Decimal(row["montant"])
        except (ValueError, InvalidOperation) as exc:
            raise InvalidCsvError(f"{source_name} ligne {line_no}: {exc}") from exc
        transactions.append(row)
    return transactions


def load_transactions(path: Path) -> list[dict]:
    """Lit un CSV local (utilisé par les tests ; la pipeline lit depuis S3)."""
    path = Path(path)
    return parse_transactions(path.read_text(encoding="utf-8"), path.name)
