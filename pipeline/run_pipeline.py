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

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_ASSERTION = 2  # asercja fail-fast (słownik statusów, wymiar placówek)


class PipelineError(RuntimeError):
    """Błąd potoku przerywający działanie z nonzero exit code."""


class UsageError(PipelineError):
    """Błąd wywołania (np. brak pliku źródłowego) — nie asercja słownika."""


def _wykonaj(con: duckdb.DuckDBPyConnection, skrypt: str) -> None:
    con.execute((SQL_DIR / skrypt).read_text(encoding="utf-8"))


def run_pipeline(csv_path: Path, db_path: Path) -> dict[str, int]:
    if not csv_path.exists():
        raise UsageError(f"Plik CSV nie istnieje: {csv_path}")

    con = duckdb.connect(str(db_path))
    try:
        # Ścieżka pliku widoczna dla skryptów SQL przez getvariable('csv_path').
        con.execute("SET VARIABLE csv_path = ?", [str(csv_path)])
        _wykonaj(con, "01_staging.sql")
        _wykonaj(con, "02_dim_status.sql")
        # Asercja słownika po zasileniu stagingu, PRZED materializacją faktów.
        _assert_znane_statusy(con, csv_path)
        # Asercje wymiaru placówek: spójność id→nazwa na stagingu, potem budowa
        # dim i pokrycie/unikalność nazw — wszystko PRZED faktami (fail-fast jak słownik statusów).
        _assert_spojnosc_placowek(con, csv_path)
        _wykonaj(con, "05_dim_placowka.sql")
        _assert_dim_placowka(con)
        _wykonaj(con, "03_transform.sql")
        _wykonaj(con, "04_marts.sql")
        summary = _podsumowanie(con)
    finally:
        con.close()
    return summary


def _assert_znane_statusy(con: duckdb.DuckDBPyConnection, csv_path: Path) -> None:
    """Fail-fast: każdy status spoza słownika przerywa potok (story 17)."""
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


def _assert_spojnosc_placowek(con: duckdb.DuckDBPyConnection, csv_path: Path) -> None:
    """Fail-fast: jedno ID placówki nie może mieć wielu nazw źródłowych (story 30).
    Pusta nazwa liczy się jako odrębna wartość — ID z nazwą i bez nazwy to tez konflikt
    (count(DISTINCT) ignoruje NULL-e, stad koalesce)."""
    konflikty = con.execute(
        """
        SELECT id_placowki, list(DISTINCT coalesce(placowka_z_zapisu, '(pusty)')) AS nazwy
        FROM staging_zapisy
        WHERE id_placowki IS NOT NULL
        GROUP BY id_placowki
        HAVING count(DISTINCT coalesce(placowka_z_zapisu, '(pusty)')) > 1
        ORDER BY min(try_cast(id_placowki AS BIGINT))
        """
    ).fetchall()
    if konflikty:
        opis = "; ".join(
            f"ID {id_placowki}: {', '.join(sorted(nazwy))}" for id_placowki, nazwy in konflikty
        )
        raise PipelineError(
            f"Konflikt nazw placówek w pliku {csv_path}: {opis}. "
            "To samo ID placówki ma wiele nazw źródłowych — wyjaśnij ze źródłem "
            "lub dodaj normalizację do pipeline/sql/05_dim_placowka.sql."
        )


def _assert_dim_placowka(con: duckdb.DuckDBPyConnection) -> None:
    """Asercje wymiaru: pokrycie wszystkich ID ze stagingu oraz unikalnosc
    nazw displayowych — filtrowanie i selektory dashboarda ida po etykiecie,
    wiec dwoch ID z ta sama nazwa (takze po normalizacji) daloby silent merge."""
    braki = con.execute(
        """
        SELECT count(*)
        FROM (
            SELECT DISTINCT try_cast(id_placowki AS BIGINT) AS id
            FROM staging_zapisy
            WHERE id_placowki IS NOT NULL
        ) s
        WHERE s.id NOT IN (SELECT id_placowki FROM dim_placowka)
        """
    ).fetchone()[0]
    if braki:
        raise PipelineError(
            f"Wymiar dim_placowka nie pokrywa {braki} ID placówek ze stagingu "
            "— sprawdź pipeline/sql/05_dim_placowka.sql."
        )

    duplikaty = con.execute(
        """
        SELECT nazwa_wyswietlana, list(id_placowki) AS ids
        FROM dim_placowka
        GROUP BY nazwa_wyswietlana
        HAVING count(*) > 1
        ORDER BY 1
        """
    ).fetchall()
    if duplikaty:
        opis = "; ".join(
            f"„{nazwa}”: ID {', '.join(map(str, ids))}" for nazwa, ids in duplikaty
        )
        raise PipelineError(
            f"Dim_placowka ma powtórzone nazwy displayowe pod różnymi ID: {opis}. "
            "Dashboard filtruje i porównuje placówki po etykiecie — rozjazd grozi "
            "silent merge; rozstrzygnij nazwy w pipeline/sql/05_dim_placowka.sql."
        )


def _podsumowanie(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    tabele = [
        "staging_zapisy",
        "fakty_zapisy",
        "zdarzenia",
        "dim_status",
        "dim_placowka",
        "mart_liczby_statusu",
        "mart_nowe_zapisy_dziennie",
    ]
    return {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tabele}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        type=Path,
        default=BASE_DIR / "data" / "zapisy_grupy_placowka.csv",
        help="Plik źródłowy CSV (separator ';', snapshot z placówkami; "
        "archiwalny zapisy_grupy_historia.csv nie ma kolumn placówki)",
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
    except UsageError as err:
        print(f"BŁĄD POTOKU: {err}", file=sys.stderr)
        return EXIT_USAGE
    except PipelineError as err:
        print(f"BŁĄD POTOKU: {err}", file=sys.stderr)
        return EXIT_ASSERTION

    for tabela, liczba in summary.items():
        print(f"{tabela}: {liczba} wierszy")
    print(f"Potok zakończony sukcesem → {args.db}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
