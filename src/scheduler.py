"""Planificateur : relance la pipeline à intervalle régulier pour traiter les nouveaux fichiers.

Un fichier déjà traité est ignoré grâce à son hash (anti-doublon) : à chaque cycle, seuls les
NOUVEAUX fichiers déposés dans le bucket S3 sont traités.
"""
import os
import sys
import time

from src.pipeline import run_pipeline
from src.storage import DEFAULT_BUCKET, ensure_bucket, get_s3_client, wait_for_s3

DEFAULT_INTERVAL = float(os.environ.get("SCHEDULER_INTERVAL", "60"))


def run_cycle(client, bucket: str):
    """Un passage de la pipeline. Ne lève jamais : en cas d'erreur, le cycle suivant réessaiera."""
    try:
        return run_pipeline(client, bucket)
    except FileNotFoundError:
        print("[SCHEDULER] aucun fichier à traiter pour le moment")
        return 0
    except Exception as exc:
        print(f"[SCHEDULER] ERREUR pendant le cycle : {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def run_forever(client, bucket: str, interval: float, max_cycles=None, sleep=time.sleep) -> None:
    cycle = 0
    while max_cycles is None or cycle < max_cycles:
        cycle += 1
        print(f"[SCHEDULER] cycle {cycle} : recherche de nouveaux fichiers dans le bucket '{bucket}'",
              flush=True)
        run_cycle(client, bucket)
        if max_cycles is None or cycle < max_cycles:
            print(f"[SCHEDULER] prochain cycle dans {interval:g}s", flush=True)
            sleep(interval)


def main() -> None:
    client = get_s3_client()
    wait_for_s3(client)                      # le service S3 démarre en même temps que nous
    ensure_bucket(client, DEFAULT_BUCKET)
    print(f"[SCHEDULER] démarré : un cycle toutes les {DEFAULT_INTERVAL:g}s", flush=True)
    run_forever(client, DEFAULT_BUCKET, DEFAULT_INTERVAL)


if __name__ == "__main__":
    main()
