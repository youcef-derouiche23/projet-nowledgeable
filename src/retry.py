"""Mécanisme de retry générique (réutilisé pour la base de données, puis pour S3)."""
import sys
import time
from typing import Callable, TypeVar

T = TypeVar("T")


class RetryError(Exception):
    """Toutes les tentatives ont échoué : la pipeline doit s'arrêter."""


def retry(
    func: Callable[[], T],
    *,
    max_attempts: int = 3,
    delay: float = 2.0,
    backoff: float = 2.0,
    retry_on: tuple = (Exception,),
    label: str = "opération",
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Exécute func(). En cas d'erreur 'retry_on', attend puis réessaie.

    - delay   : attente avant la 2e tentative (secondes)
    - backoff : multiplicateur de l'attente à chaque nouvel échec (2 -> 2s, 4s, 8s...)
    - Les erreurs qui ne sont PAS dans retry_on (ex : données invalides) ne sont jamais réessayées.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts doit être >= 1")

    wait = delay
    for attempt in range(1, max_attempts + 1):
        try:
            return func()
        except retry_on as exc:
            error = f"{type(exc).__name__}: {exc}"
            print(f"[RETRY] {label} : tentative {attempt}/{max_attempts} échouée -> {error}",
                  file=sys.stderr)
            if attempt == max_attempts:
                raise RetryError(
                    f"{label} : abandon après {max_attempts} tentatives (dernière erreur : {error})"
                ) from exc
            print(f"[RETRY] nouvelle tentative dans {wait:g}s...", file=sys.stderr)
            sleep(wait)
            wait *= backoff
