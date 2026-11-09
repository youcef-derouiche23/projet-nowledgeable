"""Tests d'intégration : génération CSV -> pipeline -> vérification en base PostgreSQL.

Ces tests utilisent une vraie base de données et la vraie pipeline.
En CI (variable CI=true), une base injoignable fait ÉCHOUER les tests au lieu de les ignorer.
"""
import os
from decimal import Decimal

import psycopg
import pytest

from scripts.generate_csv import generate
from src.database import DEFAULT_DATABASE_URL
from src.pipeline import run_pipeline

A = "FR7630006000011234567890189"
B = "FR7612548029989876543210917"
C = "DE89370400440532013000"
D = "GB29NWBK60161331926819"
E = "ES9121000418450200051332"
F = "IT60X0542811101000000123456"


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    """Repart d'une base vide, génère les CSV, lance la pipeline, puis donne accès à la base."""
    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    try:
        conn = psycopg.connect(url, autocommit=True)
    except psycopg.OperationalError as exc:
        if os.environ.get("CI"):
            raise
        pytest.skip(f"Base de données indisponible : {exc}")

    conn.execute(
        "DROP TABLE IF EXISTS received_by_iban, sent_by_bank, sent_by_iban, "
        "transactions, processed_files CASCADE"
    )
    input_dir = tmp_path_factory.mktemp("incoming")
    generate(input_dir)
    run_pipeline(input_dir)

    yield conn
    conn.close()


def _totals(db, table, key_col, total_col):
    rows = db.execute(
        f"SELECT {key_col}, SUM({total_col}) FROM {table} GROUP BY {key_col}"
    ).fetchall()
    return dict(rows)


def test_data_is_inserted(db):
    assert db.execute("SELECT count(*) FROM processed_files").fetchone()[0] == 3
    assert db.execute("SELECT count(*) FROM transactions").fetchone()[0] == 16


def test_sum_sent_by_iban_values(db):
    assert _totals(db, "sent_by_iban", "iban_origine", "total_envoye") == {
        A: Decimal("10740.91"), B: Decimal("12575.25"), C: Decimal("16200.00"),
        D: Decimal("2750.75"), E: Decimal("5450.00"), F: Decimal("2480.00"),
    }


def test_sum_sent_by_bank_values(db):
    assert _totals(db, "sent_by_bank", "banque_source", "total_envoye") == {
        "BNP Paribas": Decimal("23316.16"), "Commerzbank": Decimal("16200.00"),
        "NatWest": Decimal("2750.75"), "CaixaBank": Decimal("5450.00"),
        "Intesa Sanpaolo": Decimal("2480.00"),
    }


def test_sum_received_by_iban_values(db):
    assert _totals(db, "received_by_iban", "iban_destinataire", "total_recu") == {
        A: Decimal("11475.25"), B: Decimal("10000.00"), C: Decimal("9580.00"),
        D: Decimal("12800.50"), E: Decimal("5450.76"), F: Decimal("890.40"),
    }


def test_flags_are_correct(db):
    flagged = {r[0] for r in db.execute("SELECT montant FROM transactions WHERE flag")}
    assert flagged == {
        Decimal("6200.00"), Decimal("7500.00"), Decimal("5000.01"),
        Decimal("10000.00"), Decimal("5200.00"),
    }
    # Le montant pile au seuil (5000.00) ne doit PAS être flaggé
    at_threshold = db.execute(
        "SELECT flag FROM transactions WHERE montant = 5000.00"
    ).fetchone()[0]
    assert at_threshold is False
