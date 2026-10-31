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


def test_potok_license_zapisy_w_kategoriach_biznesowych(tmp_path):
    """Mart liczebności: AKTYWNY 1, ZAPISANY 2 (distinct po ID) — zapis 3
    (REZYGNACJA) bez placówki spada, bo marty obejmują tylko realne placówki."""
    proc, db = run_pipeline(tmp_path, "zapisy_minimal.csv")
    assert proc.returncode == 0, proc.stderr

    con = open_db(db)
    try:
        licznik = dict(
            con.execute(
                "SELECT kategoria_biznesowa, sum(liczba_zapisow) FROM mart_liczby_statusu GROUP BY 1"
            ).fetchall()
        )
        assert licznik == {"aktywny": 1, "zapisany": 2}

        # statusy są ponumerowane individualnie z etykietą słownika
        wiersze = con.execute(
            "SELECT status_zapisu, liczba_zapisow FROM mart_liczby_statusu ORDER BY 1"
        ).fetchall()
        assert wiersze == [("AKTYWNY", 1), ("ZAPISANY", 2)]
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

        # 1 utworzono + (LangLion, utworzono, umowa, nauczyciel) + 1 + 2 (zdedup.)
        assert con.execute("SELECT count(*) FROM zdarzenia").fetchone()[0] == 8

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

        # zapis wielo-grupowy nie mnoży zdarzeń — dedup po trójce kluczy,
        # nawet gdy źródła potwierdzenia różnią się między zduplikowanymi wierszami
        umowa5 = con.execute(
            "SELECT count(*), max(zrodlo_potwierdzenia) FROM zdarzenia "
            "WHERE id_zapisu = 5 AND aktywnosc = 'Kliknięto przesłano umowę'"
        ).fetchone()
        assert umowa5 == (1, "portal")
        assert con.execute(
            "SELECT count(*) FROM zdarzenia WHERE id_zapisu = 5"
        ).fetchone()[0] == 2
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
            (date(2026, 2, 15), "ZAPISANY", "closed", "zapisany", 1),
            (date(2026, 3, 3), "ZAPISANY", "sales", "zapisany", 1),
            (date(2026, 4, 1), "AKTYWNY", "sales", "aktywny", 1),
        ]

        # HISTORYCZNY nie zostawia śladu na linii czasu (artefakt migracyjny 15.02 16:49);
        # REZYGNACJA (20.02, zapis 3 bez placówki) odpada przez filtr realnych placówek
        assert sum(w[4] for w in wiersze) == 3

        # suma dzienna to distinct po ID zapisu, nie wiersze źródłowe
        assert con.execute(
            "SELECT sum(liczba_nowych_zapisow) FROM mart_nowe_zapisy_dziennie "
            "WHERE data_utworzenia = DATE '2026-03-03'"
        ).fetchone()[0] == 1
    finally:
        con.close()


def test_dim_placowka_normalizuje_nazwy_i_rozroznia_typy(tmp_path):
    """Wymiar placówek: distinct ID→nazwa ze stagingu, mapa displayowa diacritików,
    typy: realna / techniczna (206) / bez_nazwy (ID 200 — pusta nazwa).
    Obejmuje też placówki tylko z wierszy HISTORYCZNYCH (206, 200) — wymiar
    budowany jest z całego stagingu.
    """
    proc, db = run_pipeline(tmp_path, "zapisy_minimal.csv")
    assert proc.returncode == 0, proc.stderr

    con = open_db(db)
    try:
        dim = con.execute(
            "SELECT id_placowki, nazwa_wyswietlana, typ FROM dim_placowka ORDER BY id_placowki"
        ).fetchall()
        assert dim == [
            (1, "Wrocław CJO", "realna"),
            (128, "Online Polska CJO", "realna"),
            (200, "(bez nazwy)", "bez_nazwy"),
            (206, "X-Baza Testowa", "techniczna"),
        ]
    finally:
        con.close()


def test_dim_placowka_awansuje_dzial_sprzedazy_na_realna(tmp_path):
    """Dział Sprzedaży (222) to realna placówka — ma odniesienie w dashboardzie.
    Niechciane byty pozostają poza 'realna': X-Baza Testowa (206) = techniczna,
    ID 200 = bez_nazwy. Fixture zawiera je w AKTYWNYCH wierszach (nie tylko
    HISTORYCZNY), żeby dowieść typologii na danych przechodzących dalej."""
    proc, db = run_pipeline(tmp_path, "zapisy_placowki_mix.csv")
    assert proc.returncode == 0, proc.stderr

    con = open_db(db)
    try:
        typy = dict(
            con.execute("SELECT id_placowki, typ FROM dim_placowka").fetchall()
        )
        assert typy == {
            1: "realna",
            200: "bez_nazwy",
            206: "techniczna",
            222: "realna",
        }
    finally:
        con.close()


def test_marty_tylko_realne_placowki_bez_kubelkow(tmp_path):
    """Marty materializują wyłącznie placówki typ='realna': X-Baza Testowa (206),
    (bez nazwy) (200) i wiersz bez placówki (NULL id) spadają — dane testowe
    nie mają odniesienia w dashboardzie. Fakty zostają kompletne (audyt):
    w mix-fixture 5 aktywnych zapisów, w martach tylko 2 realne placówki."""
    proc, db = run_pipeline(tmp_path, "zapisy_placowki_mix.csv")
    assert proc.returncode == 0, proc.stderr

    con = open_db(db)
    try:
        assert con.execute("SELECT count(*) FROM fakty_zapisy").fetchone()[0] == 5, (
            "fakty nie są miejscem filtra — pełne dane do audytu"
        )

        for tabela in ("mart_liczby_statusu", "mart_nowe_zapisy_dziennie"):
            placowki = {
                row[0]
                for row in con.execute(f"SELECT DISTINCT placowka FROM {tabela}").fetchall()
            }
            assert placowki == {"Wrocław CJO", "Dział Sprzedaży"}, tabela
            assert (
                con.execute(f"SELECT count(*) FROM {tabela} WHERE id_placowki IS NULL").fetchone()[0]
                == 0
            ), tabela
    finally:
        con.close()


def test_fakty_nosza_placowke_a_marty_odrzucaja_bez_placowki(tmp_path):
    """Fakt dziedziczy id_placowki (dedup multi-grupa bez zmian — 1 zapis = 1 placówka),
    w tym NULL — fakty są kompletne do audytu; marty mają id_placowki + displayową
    etykietę tylko dla realnych placówek (kubełek „(brak placówki)" zniknął).
    """
    proc, db = run_pipeline(tmp_path, "zapisy_minimal.csv")
    assert proc.returncode == 0, proc.stderr

    con = open_db(db)
    try:
        fakty = dict(
            con.execute("SELECT id_zapisu, id_placowki FROM fakty_zapisy ORDER BY 1").fetchall()
        )
        assert fakty == {1: 128, 2: 1, 3: None, 5: 1}

        dzienne = con.execute(
            """
            SELECT f.placowka, sum(f.liczba_nowych_zapisow)
            FROM mart_nowe_zapisy_dziennie f
            GROUP BY 1 ORDER BY 1
            """
        ).fetchall()
        assert dzienne == [
            ("Online Polska CJO", 1),
            ("Wrocław CJO", 2),
        ]

        # ani jednego wiersza bez placówki w marcie (join do dim typ='realna')
        assert con.execute(
            "SELECT count(*) FROM mart_nowe_zapisy_dziennie WHERE id_placowki IS NULL"
        ).fetchone()[0] == 0

        liczby = con.execute(
            """
            SELECT placowka, status_zapisu, liczba_zapisow
            FROM mart_liczby_statusu ORDER BY 1, 2
            """
        ).fetchall()
        assert liczby == [
            ("Online Polska CJO", "AKTYWNY", 1),
            ("Wrocław CJO", "ZAPISANY", 2),
        ]
    finally:
        con.close()


def test_konflikt_nazw_tego_samego_id_placowki_przerywa_potok(tmp_path):
    """Asercja dim_placowka: dwa różne nazwy pod jednym ID ⇒ nonzero exit,
    komunikat wskazuje ID, potok staje PRZED faktami (fail-fast, jak słownik statusów).
    Wariant NULL-ślepy: pod jednym ID nazwa niepusta i pusta ⇒ tez konflikt.
    """
    proc, db = run_pipeline(tmp_path, "zapisy_placowka_konflikt_nazw.csv")
    assert proc.returncode != 0
    assert "128" in proc.stderr
    assert "144" in proc.stderr, "pusta nazwa + nazwa pod tym samym ID to tez konflikt"

    con = duckdb.connect(str(db), read_only=True)
    try:
        istniejace = {
            row[0]
            for row in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
        }
        assert "fakty_zapisy" not in istniejace
    finally:
        con.close()


def test_ten_sama_nazwa_displayowa_pod_dwiema_id_przerywa_potok(tmp_path):
    """Unikalność nazw displayowych w dim: dwa ID z ta sama nazwa grozilby
    silent merge przy filtrowaniu po etykiecie — asercja stalluje potok."""
    proc, db = run_pipeline(tmp_path, "zapisy_placowka_dupe_nazwa.csv")
    assert proc.returncode != 0
    assert "Poznań CJO" in proc.stderr

    con = duckdb.connect(str(db), read_only=True)
    try:
        istniejace = {
            row[0]
            for row in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
        }
        assert "fakty_zapisy" not in istniejace
    finally:
        con.close()


def test_dim_placowka_objaemia_wszystkie_id_ze_stagingu(tmp_path):
    """Pokrycie: każde niepuste ID placówki ze stagingu ma wiersz w dim_placowka."""
    proc, db = run_pipeline(tmp_path, "zapisy_minimal.csv")
    assert proc.returncode == 0, proc.stderr

    con = open_db(db)
    try:
        braki = con.execute(
            """
            SELECT count(*)
            FROM (SELECT DISTINCT try_cast(id_placowki AS BIGINT) AS id FROM staging_zapisy
                  WHERE id_placowki IS NOT NULL) s
            WHERE s.id NOT IN (SELECT id_placowki FROM dim_placowka)
            """
        ).fetchone()[0]
        assert braki == 0
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
            ("zdarzenia", 8),
            ("mart_liczby_statusu", 2),
            ("mart_nowe_zapisy_dziennie", 3),
        ]:
            assert con.execute(f"SELECT count(*) FROM {tabela}").fetchone()[0] == oczekiwana, tabela
    finally:
        con.close()
