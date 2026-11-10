"""Tests d'intégration : CSV dans S3 -> pipeline -> vérification en base PostgreSQL."""
from decimal import Decimal

import pytest

from src.pipeline import run_pipeline

A = "FR7630006000011234567890189"
B = "FR7612548029989876543210917"
C = "DE89370400440532013000"
D = "GB29NWBK60161331926819"
E = "ES9121000418450200051332"
F = "IT60X0542811101000000123456"


@pytest.fixture
def loaded(db, uploaded_csv):
    """Lance la vraie pipeline (S3 -> base) et retourne la connexion à la base."""
    run_pipeline(uploaded_csv.client, uploaded_csv.bucket)
    return db


def _totals(db, table, key_col, total_col):
    rows = db.execute(
        f"SELECT {key_col}, SUM({total_col}) FROM {table} GROUP BY {key_col}"
    ).fetchall()
    return dict(rows)


def test_data_is_inserted(loaded):
    assert loaded.execute("SELECT count(*) FROM processed_files").fetchone()[0] == 3
    assert loaded.execute("SELECT count(*) FROM transactions").fetchone()[0] == 16


def test_sum_sent_by_iban_values(loaded):
    assert _totals(loaded, "sent_by_iban", "iban_origine", "total_envoye") == {
        A: Decimal("10740.91"), B: Decimal("12575.25"), C: Decimal("16200.00"),
        D: Decimal("2750.75"), E: Decimal("5450.00"), F: Decimal("2480.00"),
    }


def test_sum_sent_by_bank_values(loaded):
    assert _totals(loaded, "sent_by_bank", "banque_source", "total_envoye") == {
        "BNP Paribas": Decimal("23316.16"), "Commerzbank": Decimal("16200.00"),
        "NatWest": Decimal("2750.75"), "CaixaBank": Decimal("5450.00"),
        "Intesa Sanpaolo": Decimal("2480.00"),
    }


def test_sum_received_by_iban_values(loaded):
    assert _totals(loaded, "received_by_iban", "iban_destinataire", "total_recu") == {
        A: Decimal("11475.25"), B: Decimal("10000.00"), C: Decimal("9580.00"),
        D: Decimal("12800.50"), E: Decimal("5450.76"), F: Decimal("890.40"),
    }


def test_flags_are_correct(loaded):
    flagged = {r[0] for r in loaded.execute("SELECT montant FROM transactions WHERE flag")}
    assert flagged == {
        Decimal("6200.00"), Decimal("7500.00"), Decimal("5000.01"),
        Decimal("10000.00"), Decimal("5200.00"),
    }
    # Le montant pile au seuil (5000.00) ne doit PAS être flaggé
    at_threshold = loaded.execute(
        "SELECT flag FROM transactions WHERE montant = 5000.00"
    ).fetchone()[0]
    assert at_threshold is False
