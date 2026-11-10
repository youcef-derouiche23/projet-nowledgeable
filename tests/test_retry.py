"""Tests du retry à l'insertion en base et de l'anti-doublon."""
import psycopg
import pytest

import src.pipeline as pipeline
from src import storage
from src.retry import RetryError, retry


# ---------- Tests unitaires du retry (sans service externe) ----------

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


# ---------- Tests avec vraie base + vrai S3 : retry + pas de doublons ----------

def _count(db, table):
    return db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def _run(env):
    return pipeline.run_pipeline(env.client, env.bucket, max_attempts=3, retry_delay=0,
                                 s3_max_attempts=3, s3_retry_delay=0)


def test_pipeline_recovers_from_temporary_db_failure(db, uploaded_csv, monkeypatch):
    real_insert, state = pipeline.insert_processed_file, {"failures_left": 2}

    def flaky_insert(*args, **kwargs):
        if state["failures_left"] > 0:
            state["failures_left"] -= 1
            raise psycopg.OperationalError("panne simulée")
        return real_insert(*args, **kwargs)

    monkeypatch.setattr(pipeline, "insert_processed_file", flaky_insert)
    _run(uploaded_csv)

    assert _count(db, "processed_files") == 3
    assert _count(db, "transactions") == 16      # aucune transaction en double


def test_failure_after_insert_does_not_create_duplicates(db, uploaded_csv, monkeypatch):
    """Panne APRÈS l'insertion mais avant la validation : tout est annulé, puis réessayé une fois."""
    real_insert, state = pipeline.insert_processed_file, {"failed": False}

    def insert_then_fail(*args, **kwargs):
        result = real_insert(*args, **kwargs)
        if not state["failed"]:
            state["failed"] = True
            raise psycopg.OperationalError("connexion perdue avant le commit")
        return result

    monkeypatch.setattr(pipeline, "insert_processed_file", insert_then_fail)
    _run(uploaded_csv)

    assert _count(db, "processed_files") == 3
    assert _count(db, "transactions") == 16


def test_pipeline_stops_after_max_db_attempts(db, uploaded_csv, monkeypatch):
    def always_down(*args, **kwargs):
        raise psycopg.OperationalError("base indisponible")

    monkeypatch.setattr(pipeline, "insert_processed_file", always_down)
    with pytest.raises(RetryError):
        _run(uploaded_csv)

    assert _count(db, "processed_files") == 0    # rien n'a été enregistré


def test_same_file_is_never_processed_twice(db, uploaded_csv):
    assert _run(uploaded_csv) == 3
    assert _run(uploaded_csv) == 0               # 2e lancement : tout est ignoré

    # Même contenu sous un autre nom de fichier : reconnu grâce au hash, donc ignoré aussi
    content = storage.download_bytes(uploaded_csv.client, uploaded_csv.bucket,
                                     "transactions_001.csv")
    uploaded_csv.client.put_object(Bucket=uploaded_csv.bucket, Key="copie.csv", Body=content)
    assert _run(uploaded_csv) == 0

    assert _count(db, "processed_files") == 3
    assert _count(db, "transactions") == 16
