"""Chargement des fichiers CSV (entrées/sorties uniquement, aucun calcul métier)."""
import csv
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


def load_transactions(path: Path) -> list[dict]:
    """Lit un CSV et retourne une liste de transactions typées (montant en Decimal)."""
    path = Path(path)
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != EXPECTED_COLUMNS:
            raise InvalidCsvError(
                f"{path.name}: colonnes attendues {EXPECTED_COLUMNS}, reçues {reader.fieldnames}"
            )
        transactions = []
        for line_no, row in enumerate(reader, start=2):
            try:
                row["datetime_transaction"] = datetime.strptime(
                    row["datetime_transaction"], "%Y-%m-%d %H:%M:%S"
                )
                row["montant"] = Decimal(row["montant"])
            except (ValueError, InvalidOperation) as exc:
                raise InvalidCsvError(f"{path.name} ligne {line_no}: {exc}") from exc
            transactions.append(row)
    return transactions


def compute_file_hash(path: Path) -> str:
    """SHA-256 du contenu du fichier (sert à identifier un fichier déjà traité)."""
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
