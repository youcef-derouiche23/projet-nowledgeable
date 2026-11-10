"""Stockage S3 local (SeaweedFS) : bucket, upload, listing, téléchargement avec retry."""
import os
import time
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError, ClientError, ConnectionClosedError, ConnectTimeoutError,
    EndpointConnectionError, IncompleteReadError, ReadTimeoutError, ResponseStreamingError,
)

from src.retry import retry

DEFAULT_ENDPOINT = "http://localhost:8333"
DEFAULT_BUCKET = os.environ.get("S3_BUCKET", "transactions")
DEFAULT_S3_MAX_ATTEMPTS = int(os.environ.get("S3_MAX_ATTEMPTS", "3"))
DEFAULT_S3_RETRY_DELAY = float(os.environ.get("S3_RETRY_DELAY", "2"))

# Simulation d'une panne S3 (pour la démo) :
# SIMULATE_S3_FAILURES=2 -> les 2 premières récupérations de fichier échouent.
_simulated_failures = int(os.environ.get("SIMULATE_S3_FAILURES", "0"))


class S3TransientError(Exception):
    """Erreur serveur S3 (HTTP 5xx) : temporaire, donc réessayable."""


class MissingFilesError(Exception):
    """Des fichiers attendus ne sont pas présents dans le bucket."""


# Pannes réseau / serveur temporaires : seules celles-ci sont réessayées.
# Un fichier absent (404) ou des identifiants refusés (403) ne le sont jamais.
TRANSIENT_S3_ERRORS = (
    EndpointConnectionError, ConnectionClosedError, ConnectTimeoutError,
    ReadTimeoutError, IncompleteReadError, ResponseStreamingError, S3TransientError,
)


def get_s3_client():
    endpoint = os.environ.get("S3_ENDPOINT_URL", DEFAULT_ENDPOINT)
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ.get("S3_ACCESS_KEY", "admin"),
        aws_secret_access_key=os.environ.get("S3_SECRET_KEY", "secret"),
        region_name="us-east-1",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 1, "mode": "standard"},   # le retry est géré par src/retry.py
            connect_timeout=5,
            read_timeout=15,
            request_checksum_calculation="when_required",       # compatibilité S3 non-AWS
            response_checksum_validation="when_required",
        ),
    )


def _simulate_failure_if_requested() -> None:
    global _simulated_failures
    if _simulated_failures > 0:
        _simulated_failures -= 1
        raise EndpointConnectionError(
            endpoint_url=os.environ.get("S3_ENDPOINT_URL", DEFAULT_ENDPOINT) + " (échec simulé)"
        )


def _call_s3(func):
    """Exécute un appel S3 ; une erreur HTTP 5xx devient une S3TransientError (réessayable)."""
    try:
        return func()
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        if status >= 500:
            raise S3TransientError(f"HTTP {status} : {exc}") from exc
        raise


def _with_retry(func, label, max_attempts, delay, sleep):
    return retry(
        lambda: _call_s3(func),
        max_attempts=max_attempts, delay=delay,
        retry_on=TRANSIENT_S3_ERRORS, label=label, sleep=sleep,
    )


def wait_for_s3(client, max_attempts: int = 30, delay: float = 2.0) -> None:
    """Attend que le service S3 (qui vient de démarrer) réponde."""
    retry(
        client.list_buckets,
        max_attempts=max_attempts, delay=delay, backoff=1.0,
        retry_on=(BotoCoreError, ClientError), label="démarrage du service S3",
    )


def ensure_bucket(client, bucket: str) -> None:
    """Crée le bucket s'il n'existe pas déjà."""
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as exc:
        if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404:
            client.create_bucket(Bucket=bucket)
        else:
            raise


def upload_file(client, bucket: str, path: Path) -> str:
    path = Path(path)
    client.put_object(Bucket=bucket, Key=path.name, Body=path.read_bytes(), ContentType="text/csv")
    return path.name


def list_csv_keys(
    client, bucket: str, *,
    max_attempts: int = DEFAULT_S3_MAX_ATTEMPTS,
    delay: float = DEFAULT_S3_RETRY_DELAY,
    sleep=time.sleep,
) -> list[str]:
    def _list():
        paginator = client.get_paginator("list_objects_v2")
        return sorted(
            obj["Key"]
            for page in paginator.paginate(Bucket=bucket)
            for obj in page.get("Contents", [])
            if obj["Key"].endswith(".csv")
        )
    return _with_retry(_list, f"liste des fichiers du bucket {bucket}", max_attempts, delay, sleep)


def verify_files_present(client, bucket: str, expected_keys: list[str]) -> None:
    present = set(list_csv_keys(client, bucket))
    missing = sorted(set(expected_keys) - present)
    if missing:
        raise MissingFilesError(f"Fichiers absents du bucket '{bucket}' : {missing}")


def download_bytes(
    client, bucket: str, key: str, *,
    max_attempts: int = DEFAULT_S3_MAX_ATTEMPTS,
    delay: float = DEFAULT_S3_RETRY_DELAY,
    sleep=time.sleep,
) -> bytes:
    """Récupère un fichier depuis S3, avec retry en cas de panne temporaire."""
    def attempt() -> bytes:
        _simulate_failure_if_requested()
        return client.get_object(Bucket=bucket, Key=key)["Body"].read()
    return _with_retry(attempt, f"récupération de {key} depuis S3", max_attempts, delay, sleep)


def empty_bucket(client, bucket: str) -> None:
    """Supprime tous les objets du bucket (utilisé par les tests)."""
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            client.delete_object(Bucket=bucket, Key=obj["Key"])
