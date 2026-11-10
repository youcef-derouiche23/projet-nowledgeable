"""Tests du stockage S3 : upload, vérification, lecture, retry sur panne S3."""
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

import src.pipeline as pipeline
from scripts.generate_csv import generate
from src import storage
from src.retry import RetryError


class FlakyClient:
    """Faux client S3 : get_object échoue N fois, puis délègue au vrai client."""

    def __init__(self, real, failures=0, error=None):
        self._real = real
        self.failures_left = failures
        self.calls = 0
        self._error = error or EndpointConnectionError(endpoint_url="http://s3-en-panne:8333")

    def get_object(self, **kwargs):
        self.calls += 1
        if self.failures_left > 0:
            self.failures_left -= 1
            raise self._error
        return self._real.get_object(**kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _http_error(status, code):
    return ClientError(
        {"Error": {"Code": code, "Message": "test"}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "GetObject",
    )


# ---------- Upload et vérification ----------

def test_upload_then_list_and_verify(s3, tmp_path):
    paths = generate(tmp_path)
    keys = [storage.upload_file(s3.client, s3.bucket, p) for p in paths]

    assert storage.list_csv_keys(s3.client, s3.bucket) == sorted(keys)
    storage.verify_files_present(s3.client, s3.bucket, keys)          # ne lève rien
    with pytest.raises(storage.MissingFilesError):
        storage.verify_files_present(s3.client, s3.bucket, keys + ["absent.csv"])


def test_download_returns_exact_content(s3, tmp_path):
    path = generate(tmp_path)[0]
    key = storage.upload_file(s3.client, s3.bucket, path)
    assert storage.download_bytes(s3.client, s3.bucket, key) == path.read_bytes()


# ---------- Retry à la récupération depuis S3 ----------

def test_download_retries_on_network_error(uploaded_csv):
    flaky = FlakyClient(uploaded_csv.client, failures=2)
    data = storage.download_bytes(flaky, uploaded_csv.bucket, "transactions_001.csv",
                                  max_attempts=3, delay=0)
    assert data.startswith(b"datetime_transaction")
    assert flaky.calls == 3                      # 2 échecs puis 1 succès


def test_download_retries_on_server_error_5xx(uploaded_csv):
    flaky = FlakyClient(uploaded_csv.client, failures=1, error=_http_error(503, "SlowDown"))
    storage.download_bytes(flaky, uploaded_csv.bucket, "transactions_001.csv",
                           max_attempts=3, delay=0)
    assert flaky.calls == 2


def test_download_gives_up_after_max_attempts(uploaded_csv):
    flaky = FlakyClient(uploaded_csv.client, failures=999)
    with pytest.raises(RetryError, match="abandon après 3 tentatives"):
        storage.download_bytes(flaky, uploaded_csv.bucket, "transactions_001.csv",
                               max_attempts=3, delay=0)
    assert flaky.calls == 3


def test_missing_file_is_not_retried(uploaded_csv):
    flaky = FlakyClient(uploaded_csv.client, failures=0)
    with pytest.raises(ClientError):             # 404 : inutile de réessayer
        storage.download_bytes(flaky, uploaded_csv.bucket, "absent.csv",
                               max_attempts=3, delay=0)
    assert flaky.calls == 1


# ---------- Pipeline complète avec panne S3 ----------

def test_pipeline_recovers_from_temporary_s3_failure(db, uploaded_csv):
    flaky = FlakyClient(uploaded_csv.client, failures=2)
    processed = pipeline.run_pipeline(flaky, uploaded_csv.bucket, retry_delay=0,
                                      s3_max_attempts=3, s3_retry_delay=0)
    assert processed == 3
    assert db.execute("SELECT count(*) FROM transactions").fetchone()[0] == 16


def test_pipeline_stops_cleanly_if_s3_stays_down(db, uploaded_csv):
    flaky = FlakyClient(uploaded_csv.client, failures=999)
    with pytest.raises(RetryError):
        pipeline.run_pipeline(flaky, uploaded_csv.bucket, retry_delay=0,
                              s3_max_attempts=3, s3_retry_delay=0)

    table_exists = db.execute("SELECT to_regclass('processed_files')").fetchone()[0] is not None
    assert not table_exists or db.execute("SELECT count(*) FROM processed_files").fetchone()[0] == 0
