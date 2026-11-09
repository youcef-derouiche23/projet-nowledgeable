import copy
from decimal import Decimal
from pathlib import Path

from scripts.generate_csv import generate
from src.loader import list_csv_files, load_transactions
from src.processing import (
    flag_large_transactions, sum_received_by_iban, sum_sent_by_bank, sum_sent_by_iban,
)


def _load_all(tmp_path: Path) -> list[dict]:
    generate(tmp_path)
    return [t for f in list_csv_files(tmp_path) for t in load_transactions(f)]


def test_sum_sent_by_iban(tmp_path):
    result = sum_sent_by_iban(_load_all(tmp_path))
    assert result["DE89370400440532013000"] == Decimal("16200.00")


def test_sum_sent_by_bank(tmp_path):
    result = sum_sent_by_bank(_load_all(tmp_path))
    assert result["BNP Paribas"] == Decimal("23316.16")


def test_sum_received_by_iban(tmp_path):
    result = sum_received_by_iban(_load_all(tmp_path))
    assert result["GB29NWBK60161331926819"] == Decimal("12800.50")


def test_flag_threshold(tmp_path):
    flagged = flag_large_transactions(_load_all(tmp_path))
    assert sum(t["flag"] for t in flagged) == 5
    by_amount = {t["montant"]: t["flag"] for t in flagged}
    assert by_amount[Decimal("5000.00")] is False   # pile au seuil
    assert by_amount[Decimal("5000.01")] is True    # juste au-dessus


def test_functions_are_pure(tmp_path):
    data = _load_all(tmp_path)
    snapshot = copy.deepcopy(data)
    flag_large_transactions(data)
    sum_sent_by_iban(data)
    assert data == snapshot  # entrée non modifiée
    assert "flag" not in data[0]
