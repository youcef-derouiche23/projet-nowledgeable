"""Pipeline : S3 -> récupération -> chargement -> traitement -> insertion en base (avec retry)."""
import os
import sys

import psycopg

from src.database import get_connection, init_schema, insert_processed_file, is_file_processed
from src.loader import InvalidCsvError, compute_content_hash, parse_transactions
from src.processing import (
    flag_large_transactions, sum_received_by_iban, sum_sent_by_bank, sum_sent_by_iban,
)
from src.retry import retry
from src.storage import (
    DEFAULT_BUCKET, DEFAULT_S3_MAX_ATTEMPTS, DEFAULT_S3_RETRY_DELAY,
    download_bytes, get_s3_client, list_data_keys,
)

DEFAULT_MAX_ATTEMPTS = int(os.environ.get("DB_MAX_ATTEMPTS", "3"))
DEFAULT_RETRY_DELAY = float(os.environ.get("DB_RETRY_DELAY", "2"))

# Simulation d'une panne temporaire de la base (pour la démo) :
# SIMULATE_DB_FAILURES=2 -> les 2 premières insertions échouent, puis tout fonctionne.
_simulated_failures = int(os.environ.get("SIMULATE_DB_FAILURES", "0"))


def _simulate_failure_if_requested() -> None:
    global _simulated_failures
    if _simulated_failures > 0:
        _simulated_failures -= 1
        raise psycopg.OperationalError("échec simulé : base temporairement indisponible")


def process_content(
    name: str,
    data: bytes,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    retry_delay: float = DEFAULT_RETRY_DELAY,
) -> bool:
    """Charge, traite et insère un fichier CSV. Retourne False s'il avait déjà été traité."""
    file_hash = compute_content_hash(data)
    if name.lower().endswith((".png", ".jpg", ".jpeg")):
        from src.ocr import image_to_csv             # image -> OCR -> CSV au format standard
        text = image_to_csv(data, name)
        print(f"[OCR]  {name} : informations extraites de l'image")
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidCsvError(f"{name}: encodage invalide ({exc})") from exc
    transactions = parse_transactions(text, name)    # chargement / validation (même flux que les CSV)
    flagged = flag_large_transactions(transactions)  # traitements (fonctions pures)
    results = (sum_sent_by_iban(flagged), sum_sent_by_bank(flagged), sum_received_by_iban(flagged))

    def attempt() -> bool:
        # Chaque tentative utilise une NOUVELLE connexion (l'ancienne peut être cassée)
        with get_connection() as conn:
            init_schema(conn)
            if is_file_processed(conn, file_hash):   # garde anti-doublon, y compris après un retry
                return False
            _simulate_failure_if_requested()
            insert_processed_file(conn, name, file_hash, flagged, *results)
            return True

    inserted = retry(
        attempt,
        max_attempts=max_attempts,
        delay=retry_delay,
        retry_on=(psycopg.OperationalError,),        # seulement les pannes temporaires
        label=f"insertion de {name}",
    )
    if inserted:
        print(f"[OK]   {name} : {len(flagged)} transactions, "
              f"{sum(t['flag'] for t in flagged)} flaggée(s)")
    else:
        print(f"[SKIP] {name} : déjà traité")
    return inserted


def run_pipeline(
    client=None,
    bucket: str | None = None,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    s3_max_attempts: int = DEFAULT_S3_MAX_ATTEMPTS,
    s3_retry_delay: float = DEFAULT_S3_RETRY_DELAY,
) -> int:
    client = client or get_s3_client()
    bucket = bucket or DEFAULT_BUCKET

    keys = list_data_keys(client, bucket, max_attempts=s3_max_attempts, delay=s3_retry_delay)
    if not keys:
        raise FileNotFoundError(f"Aucun fichier (CSV ou image) trouvé dans le bucket S3 '{bucket}'")

    processed = 0
    for key in keys:
        data = download_bytes(client, bucket, key,
                              max_attempts=s3_max_attempts, delay=s3_retry_delay)
        processed += process_content(key, data, max_attempts, retry_delay)
    print(f"Pipeline terminée : {processed}/{len(keys)} fichier(s) traité(s)")
    return processed


if __name__ == "__main__":
    try:
        run_pipeline()
    except Exception as exc:
        print(f"[ERREUR] {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
