#!/usr/bin/env python3
"""Runner potoku ELT: staging → słownik+asercja → transform → marts.

Pełne, idempotentne przeładowanie: każde uruchomienie odtwarza wszystkie
tabele od zera na podstawie pliku CSV, więc stan bazy zawsze odpowiada
stanowi pliku.

Użycie:
    python pipeline/run_pipeline.py [--csv PATH] [--db PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

BASE_DIR = Path(__file__).resolve().parent.parent
SQL_DIR = Path(__file__).resolve().parent / "sql"

# Kolejność ma znaczenie: słownik przed transformacją (check), marts po fakcie.
SQL_SCRIPTS = [
    "01_staging.sql",
    "02_dim_status.sql",
    "03_transform.sql",
    "04_marts.sql",
]

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_STATUS_DICTIONARY = 2  # nieznany status → fail-fast


class PipelineError(RuntimeError):
    """Błąd potoku przerywający działanie z nonzero exit code."""


def run_pipeline(csv_path: Path, db_path: Path) -> dict[str, int]:
    if not csv_path.exists():
        raise PipelineError(f"Plik CSV nie istnieje: {csv_path}")

    con = duckdb.connect(str(db_path))
    try:
        # Ścieżka pliku widoczna dla skryptów SQL przez getvariable('csv_path').
        con.execute("SET VARIABLE csv_path = ?", [str(csv_path)])
        for script in SQL_SCRIPTS:
            sql = (SQL_DIR / script).read_text(encoding="utf-8")
            con.execute(sql)
            if script == "02_dim_status.sql":
                # Asercja tuż po zasileniu stagingu i słownika, przed transformacją.
                _assert_znane_statusy(con, csv_path)
        summary = _podsumowanie(con)
    finally:
        con.close()
    return summary


def _assert_znane_statusy(con: duckdb.DuckDBPyConnection, csv_path: Path) -> None:
    """Fail-fast: każdy status spoza słownika przerywa potok (story 18)."""
    nieznane = con.execute(
        """
        SELECT DISTINCT coalesce(nullif(status_zapisu, ''), '(pusty)') AS status
        FROM staging_zapisy
        WHERE coalesce(status_zapisu, '') NOT IN (SELECT status_zapisu FROM dim_status)
        ORDER BY 1
        """
    ).fetchall()
    if nieznane:
        lista = ", ".join(status for (status,) in nieznane)
        raise PipelineError(
            f"Nieznane statusy zapisu w pliku {csv_path}: {lista}. "
            "Uzupełnij słownik dim_status (pipeline/sql/02_dim_status.sql) "
            "i zaktualizuj kategorię biznesową, potem uruchom potok ponownie."
        )


def _podsumowanie(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    tabele = [
        "staging_zapisy",
        "fakty_zapisy",
        "zdarzenia",
        "dim_status",
        "mart_liczby_statusu",
        "mart_nowe_zapisy_dziennie",
    ]
    return {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tabele}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        type=Path,
        default=BASE_DIR / "data" / "zapisy_grupy_historia.csv",
        help="Plik źródłowy CSV (separator ';')",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=BASE_DIR / "data" / "warehouse.duckdb",
        help="Plik bazy DuckDB (tworzony/nadpisywany)",
    )
    args = parser.parse_args(argv)

    args.db.parent.mkdir(parents=True, exist_ok=True)
    try:
        summary = run_pipeline(args.csv, args.db)
    except PipelineError as err:
        print(f"BŁĄD POTOKU: {err}", file=sys.stderr)
        return EXIT_STATUS_DICTIONARY

    for tabela, liczba in summary.items():
        print(f"{tabela}: {liczba} wierszy")
    print(f"Potok zakończony sukcesem → {args.db}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
