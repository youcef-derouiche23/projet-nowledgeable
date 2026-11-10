"""Envoie les CSV générés dans le bucket S3 (le crée si besoin), puis vérifie leur présence.

Usage : python -m scripts.upload_to_s3 [--input-dir data/incoming]
"""
import argparse
import sys
from pathlib import Path

from src.loader import list_csv_files
from src.storage import (
    DEFAULT_BUCKET, ensure_bucket, get_s3_client, upload_file, verify_files_present, wait_for_s3,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", default="data/incoming")
    args = parser.parse_args()

    files = list_csv_files(Path(args.input_dir))
    if not files:
        print(f"[ERREUR] Aucun CSV dans {args.input_dir} : lancer d'abord scripts/generate_csv.py",
              file=sys.stderr)
        return 1

    client = get_s3_client()
    wait_for_s3(client)                         # le service S3 vient peut-être de démarrer
    ensure_bucket(client, DEFAULT_BUCKET)
    print(f"[S3] bucket '{DEFAULT_BUCKET}' prêt")

    keys = [upload_file(client, DEFAULT_BUCKET, path) for path in files]
    for key in keys:
        print(f"[S3] envoyé : {key}")

    verify_files_present(client, DEFAULT_BUCKET, keys)
    print(f"[S3] vérification : {len(keys)}/{len(keys)} fichier(s) présent(s) dans le bucket")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"[ERREUR] {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
