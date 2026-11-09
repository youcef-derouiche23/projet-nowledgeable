"""Pipeline : chargement -> traitement -> insertion en base (avec retry), pour chaque CSV."""
import os
import sys
from pathlib import Path

import psycopg

from src.database import get_connection, init_schema, insert_processed_file, is_file_processed
from src.loader import compute_file_hash, list_csv_files, load_transactions
from src.processing import (
    flag_large_transactions, sum_received_by_iban, sum_sent_by_bank, sum_sent_by_iban,
)
from src.retry import retry

DEFAULT_INPUT_DIR = Path("data/incoming")
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


def process_file(
    path: Path,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    retry_delay: float = DEFAULT_RETRY_DELAY,
) -> bool:
    """Traite un fichier. Retourne False s'il avait déjà été traité (ignoré)."""
    file_hash = compute_file_hash(path)
    transactions = load_transactions(path)           # chargement (I/O)
    flagged = flag_large_transactions(transactions)  # traitements (fonctions pures)
    results = (sum_sent_by_iban(flagged), sum_sent_by_bank(flagged), sum_received_by_iban(flagged))

    def attempt() -> bool:
        # Chaque tentative utilise une NOUVELLE connexion (l'ancienne peut être cassée)
        with get_connection() as conn:
            init_schema(conn)
            if is_file_processed(conn, file_hash):   # garde anti-doublon, y compris après un retry
                return False
            _simulate_failure_if_requested()
            insert_processed_file(conn, path.name, file_hash, flagged, *results)
            return True

    inserted = retry(
        attempt,
        max_attempts=max_attempts,
        delay=retry_delay,
        retry_on=(psycopg.OperationalError,),        # seulement les pannes temporaires
        label=f"insertion de {path.name}",
    )
    if inserted:
        print(f"[OK]   {path.name} : {len(flagged)} transactions, "
              f"{sum(t['flag'] for t in flagged)} flaggée(s)")
    else:
        print(f"[SKIP] {path.name} : déjà traité")
    return inserted


def run_pipeline(
    input_dir: Path = DEFAULT_INPUT_DIR,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    retry_delay: float = DEFAULT_RETRY_DELAY,
) -> int:
    files = list_csv_files(input_dir)
    if not files:
        raise FileNotFoundError(f"Aucun fichier CSV trouvé dans {input_dir}")
    processed = sum(process_file(f, max_attempts, retry_delay) for f in files)
    print(f"Pipeline terminée : {processed}/{len(files)} fichier(s) traité(s)")
    return processed


if __name__ == "__main__":
    try:
        run_pipeline()
    except Exception as exc:
        print(f"[ERREUR] {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
