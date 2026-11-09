"""Tests du mécanisme de retry et de l'anti-doublon."""
import os
import shutil

import psycopg
import pytest

import src.pipeline as pipeline
from scripts.generate_csv import generate
from src.database import DEFAULT_DATABASE_URL
from src.retry import RetryError, retry


# ---------- Tests unitaires du retry (sans base de données) ----------

def test_retry_succeeds_after_transient_failures():
    calls, sleeps = {"n": 0}, []

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("panne temporaire")
        return "ok"

    result = retry(flaky, max_attempts=5, delay=1, backoff=2,
                   retry_on=(ConnectionError,), sleep=sleeps.append)
    assert result == "ok"
    assert calls["n"] == 3
    assert sleeps == [1, 2]          # attente croissante entre les tentatives


def test_retry_gives_up_after_max_attempts():
    calls, sleeps = {"n": 0}, []

    def always_fails():
        calls["n"] += 1
        raise ConnectionError("toujours en panne")

    with pytest.raises(RetryError, match="abandon après 3 tentatives"):
        retry(always_fails, max_attempts=3, delay=1, retry_on=(ConnectionError,),
              sleep=sleeps.append)
    assert calls["n"] == 3
    assert sleeps == [1, 2]          # pas d'attente après le dernier échec


def test_retry_does_not_retry_other_errors():
    calls = {"n": 0}

    def bad_data():
        calls["n"] += 1
        raise ValueError("donnée invalide")

    with pytest.raises(ValueError):
        retry(bad_data, retry_on=(ConnectionError,), sleep=lambda s: None)
    assert calls["n"] == 1           # une erreur de données n'est jamais réessayée


# ---------- Tests avec la vraie base : retry + pas de doublons ----------

@pytest.fixture
def db():
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
    yield conn
    conn.close()


def _count(db, table):
    return db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_pipeline_recovers_from_temporary_failure(db, tmp_path, monkeypatch):
    generate(tmp_path)
    real_insert, state = pipeline.insert_processed_file, {"failures_left": 2}

    def flaky_insert(*args, **kwargs):
        if state["failures_left"] > 0:
            state["failures_left"] -= 1
            raise psycopg.OperationalError("panne simulée")
        return real_insert(*args, **kwargs)

    monkeypatch.setattr(pipeline, "insert_processed_file", flaky_insert)
    pipeline.run_pipeline(tmp_path, max_attempts=3, retry_delay=0)

    assert _count(db, "processed_files") == 3
    assert _count(db, "transactions") == 16      # aucune transaction en double


def test_failure_after_insert_does_not_create_duplicates(db, tmp_path, monkeypatch):
    """Panne APRÈS l'insertion mais avant la validation : tout est annulé, puis réessayé une fois."""
    generate(tmp_path)
    real_insert, state = pipeline.insert_processed_file, {"failed": False}

    def insert_then_fail(*args, **kwargs):
        result = real_insert(*args, **kwargs)
        if not state["failed"]:
            state["failed"] = True
            raise psycopg.OperationalError("connexion perdue avant le commit")
        return result

    monkeypatch.setattr(pipeline, "insert_processed_file", insert_then_fail)
    pipeline.run_pipeline(tmp_path, max_attempts=3, retry_delay=0)

    assert _count(db, "processed_files") == 3
    assert _count(db, "transactions") == 16


def test_pipeline_stops_after_max_attempts(db, tmp_path, monkeypatch):
    generate(tmp_path)

    def always_down(*args, **kwargs):
        raise psycopg.OperationalError("base indisponible")

    monkeypatch.setattr(pipeline, "insert_processed_file", always_down)
    with pytest.raises(RetryError):
        pipeline.run_pipeline(tmp_path, max_attempts=3, retry_delay=0)

    assert _count(db, "processed_files") == 0    # rien n'a été enregistré


def test_same_file_is_never_processed_twice(db, tmp_path):
    generate(tmp_path)
    assert pipeline.run_pipeline(tmp_path, retry_delay=0) == 3
    assert pipeline.run_pipeline(tmp_path, retry_delay=0) == 0   # 2e lancement : tout ignoré

    # Même contenu sous un autre nom : reconnu grâce au hash, donc ignoré aussi
    shutil.copy(tmp_path / "transactions_001.csv", tmp_path / "copie.csv")
    assert pipeline.run_pipeline(tmp_path, retry_delay=0) == 0

    assert _count(db, "processed_files") == 3
    assert _count(db, "transactions") == 16
