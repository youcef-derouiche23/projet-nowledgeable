"""Traitements des transactions : uniquement des fonctions PURES.

Pures = ne modifient pas leurs entrées, ne lisent aucun fichier / état externe,
et retournent toujours le même résultat pour les mêmes entrées.
"""
from collections import defaultdict
from decimal import Decimal

FLAG_THRESHOLD = Decimal("5000")


def _sum_by(transactions: list[dict], group_key: str) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for t in transactions:
        totals[t[group_key]] += t["montant"]
    return dict(totals)


def sum_sent_by_iban(transactions: list[dict]) -> dict[str, Decimal]:
    return _sum_by(transactions, "iban_origine")


def sum_sent_by_bank(transactions: list[dict]) -> dict[str, Decimal]:
    return _sum_by(transactions, "banque_source")


def sum_received_by_iban(transactions: list[dict]) -> dict[str, Decimal]:
    return _sum_by(transactions, "iban_destinataire")


def flag_large_transactions(
    transactions: list[dict], threshold: Decimal = FLAG_THRESHOLD
) -> list[dict]:
    """Retourne de NOUVELLES transactions avec un champ 'flag' (montant > seuil)."""
    return [{**t, "flag": t["montant"] > threshold} for t in transactions]
