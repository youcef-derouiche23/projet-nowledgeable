"""Pipeline : chargement -> traitement -> insertion en base, pour chaque CSV."""
import sys
from pathlib import Path

from src.database import get_connection, init_schema, insert_processed_file, is_file_processed
from src.loader import compute_file_hash, list_csv_files, load_transactions
from src.processing import (
    flag_large_transactions, sum_received_by_iban, sum_sent_by_bank, sum_sent_by_iban,
)

DEFAULT_INPUT_DIR = Path("data/incoming")


def process_file(conn, path: Path) -> bool:
    """Traite un fichier. Retourne False s'il avait déjà été traité (ignoré)."""
    file_hash = compute_file_hash(path)
    if is_file_processed(conn, file_hash):
        print(f"[SKIP] {path.name} : déjà traité")
        return False

    transactions = load_transactions(path)           # chargement (I/O)
    flagged = flag_large_transactions(transactions)  # traitements (fonctions pures)
    insert_processed_file(
        conn, path.name, file_hash, flagged,
        sum_sent_by_iban(flagged), sum_sent_by_bank(flagged), sum_received_by_iban(flagged),
    )
    print(f"[OK]   {path.name} : {len(flagged)} transactions, "
          f"{sum(t['flag'] for t in flagged)} flaggée(s)")
    return True


def run_pipeline(input_dir: Path = DEFAULT_INPUT_DIR) -> int:
    files = list_csv_files(input_dir)
    if not files:
        raise FileNotFoundError(f"Aucun fichier CSV trouvé dans {input_dir}")
    with get_connection() as conn:
        init_schema(conn)
        processed = sum(process_file(conn, f) for f in files)
    print(f"Pipeline terminée : {processed}/{len(files)} fichier(s) traité(s)")
    return processed


if __name__ == "__main__":
    try:
        run_pipeline()
    except Exception as exc:
        print(f"[ERREUR] {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
