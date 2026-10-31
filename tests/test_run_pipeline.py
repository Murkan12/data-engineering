"""Testy potoku ELT przez najwyższy szew: runner `run_pipeline.py`.

Każdy test odpala pełny potok na pliku fixture i asercjonuje zawartość
tabel wynikowych w DuckDB — nie strukturę skryptów SQL.
"""

import subprocess
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "pipeline" / "run_pipeline.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def run_pipeline(tmp_path: Path, csv_name: str) -> tuple[subprocess.CompletedProcess, duckdb.DuckDBPyConnection]:
    db = tmp_path / "warehouse.duckdb"
    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--csv", str(FIXTURES / csv_name), "--db", str(db)],
        capture_output=True,
        text=True,
    )
    return proc, db


def open_db(db: Path) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(db), read_only=True)


def test_potok_laduje_fixture_i_materializuje_fakty_w_ziarnie_zapis(tmp_path):
    """Zapis wielo-grupowy to jeden wiersz faktu; HISTORYCZNY jest odrzucany."""
    proc, db = run_pipeline(tmp_path, "zapisy_minimal.csv")
    assert proc.returncode == 0, proc.stderr

    con = open_db(db)
    try:
        ids = [row[0] for row in con.execute("SELECT id_zapisu FROM fakty_zapisy ORDER BY 1").fetchall()]
        assert ids == [1, 2, 3, 5], "ziarno = unikalny id_zapisu, bez HISTORYCZNY (id 4)"

        # dedup po ID zapisu deterministyczny: zapis 5 istniał w grupach 500 i 501
        id_grupy = con.execute("SELECT id_grupy FROM fakty_zapisy WHERE id_zapisu = 5").fetchone()[0]
        assert id_grupy == "500"
    finally:
        con.close()


def test_potok_license_zapisy_w_trzech_kategoriach_biznesowych(tmp_path):
    """Mart liczebności: AKTYWNY 1, ZAPISANY 2 (distinct po ID), REZYGNACJA 1."""
    proc, db = run_pipeline(tmp_path, "zapisy_minimal.csv")
    assert proc.returncode == 0, proc.stderr

    con = open_db(db)
    try:
        licznik = dict(
            con.execute(
                "SELECT kategoria_biznesowa, sum(liczba_zapisow) FROM mart_liczby_statusu GROUP BY 1"
            ).fetchall()
        )
        assert licznik == {"aktywny": 1, "zapisany": 2, "rezygnacja": 1}

        # statusy są ponumerowane individualnie z etykietą słownika
        wiersze = con.execute(
            "SELECT status_zapisu, liczba_zapisow FROM mart_liczby_statusu ORDER BY 1"
        ).fetchall()
        assert wiersze == [("AKTYWNY", 1), ("REZYGNACJA", 1), ("ZAPISANY", 2)]
    finally:
        con.close()


def test_zdarzenia_unpivot_kolumn_aktywnosci_z_polskimi_datami(tmp_path):
    """Każda niepusta kolumna aktywności staje się wierszem zdarzenia.

    Pokrywa: data jednocyfrowa (1.04.2026), puste aktywności → brak wiersza,
    LangLion przed „Utworzono" (skew) → oba zdarzenia niezależnie,
    dedup zdarzeń dla zapisu wielo-grupowego.
    """
    from datetime import datetime

    proc, db = run_pipeline(tmp_path, "zapisy_minimal.csv")
    assert proc.returncode == 0, proc.stderr

    con = open_db(db)
    try:
        kol = [row[0] for row in con.execute("SELECT * FROM zdarzenia LIMIT 0").description]
        assert kol == ["id_zapisu", "aktywnosc", "czas_wystapienia", "zrodlo_potwierdzenia"]

        # 1 utworzono + (LangLion, utworzono, umowa, nauczyciel) + 1 + 1 (zdedup.)
        assert con.execute("SELECT count(*) FROM zdarzenia").fetchone()[0] == 7

        # data jednocyfrowa „1.04.2026 9:30" sparsowana do poprawnego timestampu
        czas = con.execute(
            "SELECT czas_wystapienia FROM zdarzenia WHERE id_zapisu = 1"
        ).fetchone()[0]
        assert czas == datetime(2026, 4, 1, 9, 30)

        # pusta kolumna aktywności = brak wiersza (zapis 1 nie ma Przekazano do DOK)
        assert con.execute(
            "SELECT count(*) FROM zdarzenia WHERE id_zapisu = 1 AND aktywnosc = 'Przekazano do DOK'"
        ).fetchone()[0] == 0

        # skew: LangLion (14.02) przed Utworzono (15.02) — oba zdarzenia istnieją
        czasy = dict(
            con.execute(
                "SELECT aktywnosc, czas_wystapienia FROM zdarzenia WHERE id_zapisu = 2"
            ).fetchall()
        )
        assert czasy["Data zapisu w LangLionie"] < czasy["Utworzono zapis w Fenku"]
        assert czasy["Data zapisu w LangLionie"] == datetime(2026, 2, 14, 10, 0)

        # zapis wielo-grupowy nie mnoży zdarzeń (kolumny powtórzone w obu wierszach)
        assert con.execute(
            "SELECT count(*) FROM zdarzenia WHERE id_zapisu = 5"
        ).fetchone()[0] == 1
    finally:
        con.close()


def test_zdarzenia_nosza_zrodla_potwierdzenia_na_wlasciwych_aktywnosciach(tmp_path):
    """Kolumny „Źródło potwierdzenia" powieszone na zdarzeniach umowa/nauczyciel."""
    proc, db = run_pipeline(tmp_path, "zapisy_minimal.csv")
    assert proc.returncode == 0, proc.stderr

    con = open_db(db)
    try:
        zrodla = dict(
            con.execute(
                "SELECT aktywnosc, zrodlo_potwierdzenia FROM zdarzenia WHERE id_zapisu = 2"
            ).fetchall()
        )
        assert zrodla["Kliknięto przesłano umowę"] == "email"
        assert zrodla["Nauczyciel potwierdził realizację"] == "aplikacja_nauczyciela"
        assert zrodla["Utworzono zapis w Fenku"] is None
        assert zrodla["Data zapisu w LangLionie"] is None
    finally:
        con.close()


def test_mart_dziennych_nowych_zapisow_z_wymiarami_do_filtrowania(tmp_path):
    """Linia czasu = flow nowo utworzonych zapisów na dzień (wg Utworzono w Fenku),
    z wymiarami status i etap oraz kategorią biznesową do filtrowania."""
    from datetime import date

    proc, db = run_pipeline(tmp_path, "zapisy_minimal.csv")
    assert proc.returncode == 0, proc.stderr

    con = open_db(db)
    try:
        wiersze = con.execute(
            """
            SELECT data_utworzenia, status_zapisu, etap_zapisu, kategoria_biznesowa,
                   liczba_nowych_zapisow
            FROM mart_nowe_zapisy_dziennie
            ORDER BY 1, 2
            """
        ).fetchall()
        assert wiersze == [
            (date(2026, 2, 15), "REZYGNACJA", "customer_service", "rezygnacja", 1),
            (date(2026, 2, 15), "ZAPISANY", "closed", "zapisany", 1),
            (date(2026, 3, 3), "ZAPISANY", "sales", "zapisany", 1),
            (date(2026, 4, 1), "AKTYWNY", "sales", "aktywny", 1),
        ]

        # HISTORYCZNY nie leave śladu na linii czasu (artefakt migracyjny 15.02 16:49)
        assert sum(w[4] for w in wiersze) == 4

        # suma dzienna to distinct po ID zapisu, nie wiersze źródłowe
        assert con.execute(
            "SELECT sum(liczba_nowych_zapisow) FROM mart_nowe_zapisy_dziennie "
            "WHERE data_utworzenia = DATE '2026-03-03'"
        ).fetchone()[0] == 1
    finally:
        con.close()


def test_nieznany_status_przerywa_potok_fail_fast(tmp_path):
    """Status spoza słownika ⇒ nonzero exit, czytelny komunikat, brak transformacji."""
    proc, db = run_pipeline(tmp_path, "zapisy_nieznany_status.csv")
    assert proc.returncode != 0
    assert "NOWY_STATUS_Z_SYSTEMU" in proc.stderr

    con = duckdb.connect(str(db), read_only=True)
    try:
        istniejace = {
            row[0]
            for row in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
        }
        assert "staging_zapisy" in istniejace, "staging ładowany zanim asercja zadziałała"
        assert "fakty_zapisy" not in istniejace, "potok musi stanąć PRZED materializacją faktów"
    finally:
        con.close()


def test_potok_jest_idempotentny_pelne_przeladowanie(tmp_path):
    """Dwa uruchomienia na tej samej bazie = identyczne liczby (pełne przeładowanie)."""
    _, db = run_pipeline(tmp_path, "zapisy_minimal.csv")
    proc2 = subprocess.run(
        [
            sys.executable, str(RUNNER),
            "--csv", str(FIXTURES / "zapisy_minimal.csv"),
            "--db", str(db),
        ],
        capture_output=True,
        text=True,
    )
    assert proc2.returncode == 0, proc2.stderr

    con = open_db(db)
    try:
        for tabela, oczekiwana in [
            ("fakty_zapisy", 4),
            ("zdarzenia", 7),
            ("mart_liczby_statusu", 3),
            ("mart_nowe_zapisy_dziennie", 4),
        ]:
            assert con.execute(f"SELECT count(*) FROM {tabela}").fetchone()[0] == oczekiwana, tabela
    finally:
        con.close()
