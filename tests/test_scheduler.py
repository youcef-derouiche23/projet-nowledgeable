"""Tests du planificateur (traitement régulier des nouveaux fichiers)."""
import pytest

from src import scheduler, storage


# ---------- Tests unitaires (sans service externe) ----------

def test_run_forever_runs_requested_cycles_and_waits_between(monkeypatch):
    calls, sleeps = [], []
    monkeypatch.setattr(scheduler, "run_pipeline", lambda client, bucket: calls.append(bucket) or 0)

    scheduler.run_forever(object(), "mon-bucket", interval=5, max_cycles=3, sleep=sleeps.append)

    assert calls == ["mon-bucket"] * 3
    assert sleeps == [5, 5]                  # pas d'attente après le dernier cycle


def test_cycle_survives_errors_and_empty_bucket(monkeypatch):
    def empty(client, bucket):
        raise FileNotFoundError("bucket vide")

    def broken(client, bucket):
        raise RuntimeError("panne")

    monkeypatch.setattr(scheduler, "run_pipeline", empty)
    assert scheduler.run_cycle(object(), "b") == 0       # rien à traiter : pas une erreur

    monkeypatch.setattr(scheduler, "run_pipeline", broken)
    assert scheduler.run_cycle(object(), "b") is None    # erreur affichée, mais le planificateur continue


# ---------- Avec la vraie base et le vrai S3 ----------

NEW_CSV = (
    "datetime_transaction,iban_origine,pays_source,banque_source,"
    "iban_destinataire,pays_destinataire,montant\n"
    "2026-11-06 08:00:00,FR7630006000011234567890189,FR,BNP Paribas,DE89370400440532013000,DE,150.00\n"
)


def test_new_files_are_picked_up_between_cycles(db, uploaded_csv):
    client, bucket = uploaded_csv.client, uploaded_csv.bucket

    assert scheduler.run_cycle(client, bucket) == 3      # cycle 1 : les 3 fichiers présents
    assert scheduler.run_cycle(client, bucket) == 0      # cycle 2 : rien de nouveau

    client.put_object(Bucket=bucket, Key="transactions_004.csv", Body=NEW_CSV.encode())
    assert scheduler.run_cycle(client, bucket) == 1      # cycle 3 : seul le nouveau fichier

    assert db.execute("SELECT count(*) FROM processed_files").fetchone()[0] == 4
    assert db.execute("SELECT count(*) FROM transactions").fetchone()[0] == 17
