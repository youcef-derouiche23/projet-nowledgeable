"""Génère des fichiers CSV de transactions DÉTERMINISTES."""
import argparse
import csv
from pathlib import Path

COLUMNS = [
    "datetime_transaction", "iban_origine", "pays_source", "banque_source",
    "iban_destinataire", "pays_destinataire", "montant",
]

# Comptes fictifs : (iban, pays, banque)
A = ("FR7630006000011234567890189", "FR", "BNP Paribas")
B = ("FR7612548029989876543210917", "FR", "BNP Paribas")  # même banque que A
C = ("DE89370400440532013000", "DE", "Commerzbank")
D = ("GB29NWBK60161331926819", "GB", "NatWest")
E = ("ES9121000418450200051332", "ES", "CaixaBank")
F = ("IT60X0542811101000000123456", "IT", "Intesa Sanpaolo")

# (datetime, compte émetteur, compte destinataire, montant)
SCENARIO = {
    "transactions_001.csv": [
        ("2026-11-02 09:15:00", A, C, "1200.00"),
        ("2026-11-02 10:30:00", A, D, "3800.50"),
        ("2026-11-02 11:45:00", B, C, "5000.00"),   # pile au seuil : PAS de flag
        ("2026-11-02 14:00:00", C, A, "6200.00"),   # flag
        ("2026-11-02 16:20:00", D, E, "450.75"),
        ("2026-11-02 17:05:00", B, D, "7500.00"),   # flag
    ],
    "transactions_002.csv": [
        ("2026-11-03 08:00:00", A, E, "5000.01"),   # juste au-dessus : flag
        ("2026-11-03 09:30:00", E, F, "250.00"),
        ("2026-11-03 12:10:00", F, C, "980.00"),
        ("2026-11-03 15:45:00", A, C, "100.00"),
        ("2026-11-03 18:00:00", C, B, "10000.00"),  # flag
    ],
    "transactions_003.csv": [
        ("2026-11-04 08:40:00", B, A, "75.25"),
        ("2026-11-04 10:00:00", D, C, "2300.00"),
        ("2026-11-04 13:30:00", E, A, "5200.00"),   # flag
        ("2026-11-04 15:15:00", F, D, "1500.00"),
        ("2026-11-04 16:50:00", A, F, "640.40"),
    ],
}


def write_csv(path: Path, rows) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        for dt, src, dst, montant in rows:
            writer.writerow([dt, src[0], src[1], src[2], dst[0], dst[1], montant])


def generate(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, rows in SCENARIO.items():
        path = output_dir / name
        write_csv(path, rows)
        written.append(path)
    return written


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="data/incoming")
    args = parser.parse_args()
    for p in generate(Path(args.output_dir)):
        print(f"OK {p}")
