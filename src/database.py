"""Accès à la base de données PostgreSQL (schéma + insertion des résultats)."""
import os
from pathlib import Path

import psycopg

DEFAULT_DATABASE_URL = "postgresql://pipeline:pipeline@localhost:5432/pipeline"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def get_connection() -> psycopg.Connection:
    return psycopg.connect(os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))


def init_schema(conn: psycopg.Connection) -> None:
    with conn.transaction():
        conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))


def insert_processed_file(
    conn: psycopg.Connection,
    filename: str,
    file_hash: str,
    transactions: list[dict],
    sent_by_iban: dict,
    sent_by_bank: dict,
    received_by_iban: dict,
) -> int:
    """Insère un fichier traité et tous ses résultats dans UNE seule transaction SQL.

    Si une étape échoue, tout est annulé (rollback) : le fichier n'est donc
    jamais marqué comme traité à moitié.
    """
    with conn.transaction():
        file_id = conn.execute(
            "INSERT INTO processed_files (filename, file_hash) VALUES (%s, %s) RETURNING id",
            (filename, file_hash),
        ).fetchone()[0]

        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO transactions
                   (file_id, datetime_transaction, iban_origine, pays_source, banque_source,
                    iban_destinataire, pays_destinataire, montant, flag)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                [
                    (file_id, t["datetime_transaction"], t["iban_origine"], t["pays_source"],
                     t["banque_source"], t["iban_destinataire"], t["pays_destinataire"],
                     t["montant"], t["flag"])
                    for t in transactions
                ],
            )
            cur.executemany(
                "INSERT INTO sent_by_iban (file_id, iban_origine, total_envoye) VALUES (%s, %s, %s)",
                [(file_id, k, v) for k, v in sent_by_iban.items()],
            )
            cur.executemany(
                "INSERT INTO sent_by_bank (file_id, banque_source, total_envoye) VALUES (%s, %s, %s)",
                [(file_id, k, v) for k, v in sent_by_bank.items()],
            )
            cur.executemany(
                "INSERT INTO received_by_iban (file_id, iban_destinataire, total_recu) VALUES (%s, %s, %s)",
                [(file_id, k, v) for k, v in received_by_iban.items()],
            )
    return file_id


def is_file_processed(conn: psycopg.Connection, file_hash: str) -> bool:
    """Vrai si un fichier au contenu identique a déjà été traité avec succès."""
    row = conn.execute(
        "SELECT 1 FROM processed_files WHERE file_hash = %s", (file_hash,)
    ).fetchone()
    return row is not None
