"""Fixtures partagées : base PostgreSQL propre et bucket S3 de test propre.

En CI (variable CI=true), un service injoignable fait ÉCHOUER les tests au lieu de les ignorer.
"""
import os
from dataclasses import dataclass

import psycopg
import pytest
from botocore.exceptions import BotoCoreError, ClientError

from scripts.generate_csv import generate
from src import storage
from src.database import DEFAULT_DATABASE_URL
from src.retry import RetryError

TEST_BUCKET = "test-transactions"
TABLES = "received_by_iban, sent_by_bank, sent_by_iban, transactions, processed_files"


@pytest.fixture
def db():
    """Connexion à une base vide (tables supprimées avant ET après chaque test)."""
    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    try:
        conn = psycopg.connect(url, autocommit=True)
    except psycopg.OperationalError as exc:
        if os.environ.get("CI"):
            raise
        pytest.skip(f"Base de données indisponible : {exc}")
    conn.execute(f"DROP TABLE IF EXISTS {TABLES} CASCADE")
    yield conn
    conn.execute(f"DROP TABLE IF EXISTS {TABLES} CASCADE")
    conn.close()


@dataclass
class S3Env:
    client: object
    bucket: str


@pytest.fixture
def s3():
    """Client S3 + bucket de test vide."""
    client = storage.get_s3_client()
    in_ci = bool(os.environ.get("CI"))
    try:
        storage.wait_for_s3(client, max_attempts=30 if in_ci else 2, delay=2.0 if in_ci else 1.0)
        storage.ensure_bucket(client, TEST_BUCKET)
    except (RetryError, BotoCoreError, ClientError) as exc:
        if in_ci:
            raise
        pytest.skip(f"Service S3 indisponible : {exc}")
    storage.empty_bucket(client, TEST_BUCKET)
    yield S3Env(client, TEST_BUCKET)
    storage.empty_bucket(client, TEST_BUCKET)


@pytest.fixture
def uploaded_csv(s3, tmp_path):
    """Génère les 3 CSV de test et les envoie dans le bucket de test."""
    for path in generate(tmp_path):
        storage.upload_file(s3.client, s3.bucket, path)
    return s3
