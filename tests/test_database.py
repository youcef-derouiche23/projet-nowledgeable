from decimal import Decimal

import psycopg
import pytest

from scripts.generate_csv import generate
from src.database import get_connection, init_schema, insert_processed_file
from src.loader import compute_file_hash, list_csv_files, load_transactions
from src.processing import (
    flag_large_transactions, sum_received_by_iban, sum_sent_by_bank, sum_sent_by_iban,
)


@pytest.fixture
def conn():
    try:
        connection = get_connection()
    except psycopg.OperationalError as exc:
        pytest.skip(f"Base de données indisponible : {exc}")
    connection.execute("DROP TABLE IF EXISTS received_by_iban, sent_by_bank, sent_by_iban, transactions, processed_files CASCADE")
    init_schema(connection)
    yield connection
    connection.close()


def test_insert_one_file(conn, tmp_path):
    generate(tmp_path)
    path = list_csv_files(tmp_path)[0]
    flagged = flag_large_transactions(load_transactions(path))

    insert_processed_file(
        conn, path.name, compute_file_hash(path), flagged,
        sum_sent_by_iban(flagged), sum_sent_by_bank(flagged), sum_received_by_iban(flagged),
    )

    assert conn.execute("SELECT count(*) FROM transactions").fetchone()[0] == 6
    assert conn.execute("SELECT count(*) FROM transactions WHERE flag").fetchone()[0] == 2
    total = conn.execute(
        "SELECT total_envoye FROM sent_by_bank WHERE banque_source = 'BNP Paribas'"
    ).fetchone()[0]
    assert total == Decimal("17500.50")


def test_rollback_on_error(conn, tmp_path):
    generate(tmp_path)
    path = list_csv_files(tmp_path)[0]
    flagged = flag_large_transactions(load_transactions(path))
    flagged[2]["montant"] = None  # provoque une erreur SQL (NOT NULL) en cours d'insertion

    with pytest.raises(psycopg.errors.NotNullViolation):
        insert_processed_file(conn, path.name, compute_file_hash(path), flagged, {}, {}, {})

    assert conn.execute("SELECT count(*) FROM processed_files").fetchone()[0] == 0
