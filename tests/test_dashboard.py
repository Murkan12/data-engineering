"""Smoke-testy dashboardu: uruchamialność i liczby KPI/pie względem filtrów.

Logika dashboardu to zapytania o już przetestowane materiały — testujemy, że
aplikacja się renderuje bez wyjątków, pokazuje poprawne KPI w stanie domyślnym
i że KPI oraz pie reagują na filtry (kategoria, zakres dat, stany puste).
"""

import json
import re
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

from run_pipeline import run_pipeline  # noqa: E402

DASHBOARD = ROOT / "app" / "dashboard.py"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "zapisy_minimal.csv"
FIXTURE_MIX = Path(__file__).resolve().parent / "fixtures" / "zapisy_placowki_mix.csv"


def odpal(tmp_path: Path, monkeypatch, fixture: Path = FIXTURE) -> AppTest:
    db = tmp_path / "warehouse.duckdb"
    run_pipeline(fixture, db)
    monkeypatch.setenv("DATAENG_DUCKDB", str(db))
    at = AppTest.from_file(str(DASHBOARD), default_timeout=30)
    at.run()
    return at


def karty(at: AppTest) -> dict[str, int]:
    """Karty KPI zakładki Przegląd — bez metryk porównania (prefiks „A: "/"B: ")."""
    return {
        m.label: int(m.value)
        for m in at.metric
        if not re.match(r"^[AB]: ", m.label)
    }


def plaster_pie(at: AppTest) -> dict[str, int]:
    """Dane plastra (kategoria -> liczba) z wykresu kołowego, zdekodowane z_arrow."""
    return {r["kategoria"]: int(r["liczba"]) for r in dane_wykresu(at, "arc")}


def dane_wykresu(at: AppTest, mark: str) -> list[dict]:
    """Dane wykresu altair o zadanym mark (arc/line/bar), zdekodowane z_arrow."""
    for wykres in at.get("vega_lite_chart"):
        spec = json.loads(wykres.proto.spec)
        m = spec.get("mark")
        m = m["type"] if isinstance(m, dict) else m
        if m != mark:
            continue
        name = spec["data"]["name"]
        for ds in wykres.proto.datasets:
            if ds.name == name:
                return pa.ipc.open_stream(ds.data.data).read_all().to_pylist()
    raise AssertionError(f"brak wykresu {mark} w dashboardzie")


def karta_wyjety(at: AppTest, prefix: str) -> dict[str, int]:
    """Metryki o wskazanym prefiksie etykiety (np. 'A: ' z kart porównania)."""
    return {m.label: int(m.value) for m in at.metric if m.label.startswith(prefix)}


def test_dashboard_sie_uruchamia_i_pokazuje_kpi(tmp_path, monkeypatch):
    """Domyślne filtry: 5 kart KPI (wszystkie + 4 kategorie w kolejności lejka).

    Liczby niezależnie przepracowane z fixture: 4 unikalne zapisy po odrzuceniu
    HISTORYCZNY (id 4) i dedupie (id 5), ale w martach zostają 3 — zapis 3
    (REZYGNACJA) nie ma placówki i odpada przez filtr realnych.
    AKTYWNY 1, ZAPISANY 2, REZYGNACJA 0, tylko_filter (INNE) 0.
    """
    at = odpal(tmp_path, monkeypatch)
    assert not at.exception
    assert karty(at) == {
        "Wszystkie zapisy": 3,
        "AKTYWNY": 1,
        "ZAPISANY": 2,
        "REZYGNACJA": 0,
        "INNE": 0,
    }
    # pie pomija kategorie bez zapisów (INNE=0 i REZYGNACJA=0 — zapis 3
    # bez placówki spada z martów) — plaster o polu 0 nie istnieje
    assert plaster_pie(at) == {"ZAPISANY": 2, "AKTYWNY": 1}
    # filtry sidebaru: kategoria statusu (zastąpiła status+etap) + fragmentator placówki
    assert [w.label for w in at.multiselect] == ["Kategoria statusu", "Placówka"]
    assert [w.label for w in at.date_input] == ["Zakres dat"]


def test_kpi_odzwierciedlaja_odfiltrowana_kategorie(tmp_path, monkeypatch):
    """Odznaczanie kategorii usuwa jej kartę i plaster pie, pomniejsza „Wszystkie zapisy".

    Niezależnie przepracowane: bez rezygnacji i INNE zostaje AKTYWNY 1 + ZAPISANY 2.
    """
    at = odpal(tmp_path, monkeypatch)
    at.multiselect[0].set_value(["AKTYWNY", "ZAPISANY"]).run()
    assert not at.exception
    assert karty(at) == {"Wszystkie zapisy": 3, "AKTYWNY": 1, "ZAPISANY": 2}
    assert plaster_pie(at) == {"ZAPISANY": 2, "AKTYWNY": 1}


def test_kpi_odzwierciedlaja_zakres_dat(tmp_path, monkeypatch):
    """KPI liczone z przefiltrowanego marta dziennego, nie z lifetime.

    Okno 1.03–1.04.2026 wycina AKTYWNY (1.04) i ZAPISANY (3.03); REZYGNACJA
    i drugi ZAPISANY (15.02) wypadają z liczenia.
    """
    at = odpal(tmp_path, monkeypatch)
    at.date_input[0].set_value((date(2026, 3, 1), date(2026, 4, 1))).run()
    assert not at.exception
    assert karty(at) == {
        "Wszystkie zapisy": 2,
        "AKTYWNY": 1,
        "ZAPISANY": 1,
        "REZYGNACJA": 0,
        "INNE": 0,
    }
    assert plaster_pie(at) == {"AKTYWNY": 1, "ZAPISANY": 1}


def test_brak_kategorii_pokazuje_jeden_komunikat_bez_wykresow(tmp_path, monkeypatch):
    """Puste kategorie: Przegląd gaśnie komunikatem i kartami KPI, ale
    porównanie żyje dalej (filtr kategorii go nie dotyczy)."""
    at = odpal(tmp_path, monkeypatch)
    at.multiselect[0].set_value([]).run()
    assert not at.exception
    assert not karty(at)  # brak kart KPI Przeglądu; metryki porównania to inny świat
    assert [i.value for i in at.info] == ["Wybierz zakres dat oraz co najmniej jedną kategorię statusu."]


def test_pusty_zakres_dat_komunikaty_w_obu_zakladkach_bez_wykresow(tmp_path, monkeypatch):
    at = odpal(tmp_path, monkeypatch)
    at.date_input[0].set_value((date(2026, 2, 16), date(2026, 2, 19))).run()
    assert not at.exception
    assert not at.metric
    assert not at.subheader
    assert [i.value for i in at.info] == [
        "Brak zapisów dla wybranych filtrów.",
        "Brak zapisów w porównywanych placówkach dla wybranego zakresu dat.",
    ]


def test_filtr_placowki_uzupelnia_karte_kpi_w_calosci(tmp_path, monkeypatch):
    """Sidebar ma multiselect „Placówka" z listą realnych placówek wymiaru,
    domyślnie wszystkie → liczby identyczne jak bez filtra (kubełek
    „(brak placówki)" zniknął razem z danymi testowymi).
    """
    at = odpal(tmp_path, monkeypatch)
    assert not at.exception
    assert [w.label for w in at.multiselect] == ["Kategoria statusu", "Placówka"]
    plac = at.multiselect[1]
    assert sorted(plac.options) == sorted(["Online Polska CJO", "Wrocław CJO"])
    assert sorted(plac.value) == sorted(plac.options)
    assert karty(at) == {
        "Wszystkie zapisy": 3,
        "AKTYWNY": 1,
        "ZAPISANY": 2,
        "REZYGNACJA": 0,
        "INNE": 0,
    }


def test_odfiltrowanie_placowki_przelicza_kpi_w_miejscu(tmp_path, monkeypatch):
    """Fragmentator: odznaczenie placówki przelicza KPI i pie w miejscu —
    te same karty, zero nowych elementów wykresów, żadnych facetów.

    Bez „Wrocław CJO" wypada ZAPISANY 2 → zostaje AKTYWNY 1 (Online).
    """
    at = odpal(tmp_path, monkeypatch)
    at.multiselect[1].set_value(["Online Polska CJO"]).run()
    assert not at.exception
    assert karty(at) == {
        "Wszystkie zapisy": 1,
        "AKTYWNY": 1,
        "ZAPISANY": 0,
        "REZYGNACJA": 0,
        "INNE": 0,
    }
    assert plaster_pie(at) == {"AKTYWNY": 1}


def test_filtr_placowki_oferuje_tylko_realne(tmp_path, monkeypatch):
    """Sidebar „Placówka" bierze opcje z wymiaru z filtrem typ='realna' —
    bez bytów testowych, placówki bez nazwy i kubełka „(brak placówki)";
    domyślnie wszystkie realne są zaznaczone."""
    at = odpal(tmp_path, monkeypatch, FIXTURE_MIX)
    assert not at.exception
    plac = at.multiselect[1]
    assert sorted(plac.options) == sorted(["Wrocław CJO", "Dział Sprzedaży"])
    assert sorted(plac.value) == sorted(plac.options)
    for brud in ("X-Baza Testowa", "(bez nazwy)", "(brak placówki)"):
        assert brud not in plac.options


def test_dane_testowe_nigdy_nie_maja_odniesienia_na_dashboardzie(tmp_path, monkeypatch):
    """Cały ślad danych testowych znika: mix-fixture ma aktywne zapisy pod
    206/200/NULL — KPI liczą tylko realne place (AKTYWNY 1 Wrocław,
    ZAPISANY 1 Dział Sprzedaży), a selektory porównania oferują Dział Sprzedaży."""
    at = odpal(tmp_path, monkeypatch, FIXTURE_MIX)
    assert not at.exception
    assert karty(at) == {
        "Wszystkie zapisy": 2,
        "AKTYWNY": 1,
        "ZAPISANY": 1,
        "REZYGNACJA": 0,
        "INNE": 0,
    }
    assert plaster_pie(at) == {"AKTYWNY": 1, "ZAPISANY": 1}
    assert "Dział Sprzedaży" in at.selectbox[0].options
    assert "X-Baza Testowa" not in str([m.label for m in at.metric])


def test_porownanie_renderuje_domyslna_reale_pare(tmp_path, monkeypatch):
    """Zakładka „Porównanie placówek": selektory A/B tylko z realnymi placówkami
    (bez technicznych/bez nazwy/brak), domyślnie top-2 wg liczby zapisów.

    fixture: Wrocław CJO = 2 zapisy (A), Online Polska CJO = 1 (B).
    Tabela: 4 kategorie × (liczba A, % A, liczba B, % B, różnica A−B).
    """
    at = odpal(tmp_path, monkeypatch)
    assert not at.exception
    assert [t.label for t in at.get("tab")] == ["Przegląd", "Porównanie placówek"]

    a, b = at.selectbox[0], at.selectbox[1]
    assert a.label == "Placówka A" and a.value == "Wrocław CJO"
    assert b.label == "Placówka B" and b.value == "Online Polska CJO"
    # tylko realne — kubełek/techniczne/bez nazwy wykluczone z listy selektorów
    assert set(a.options) == {"Wrocław CJO", "Online Polska CJO"}

    assert karta_wyjety(at, "A: ") == {"A: Wrocław CJO": 2}
    assert karta_wyjety(at, "B: ") == {"B: Online Polska CJO": 1}

    paski = dane_wykresu(at, "bar")
    assert {(r["placowka"], r["kategoria"]): r["liczba"] for r in paski} == {
        ("Wrocław CJO", "AKTYWNY"): 0,
        ("Wrocław CJO", "ZAPISANY"): 2,
        ("Wrocław CJO", "REZYGNACJA"): 0,
        ("Wrocław CJO", "INNE"): 0,
        ("Online Polska CJO", "AKTYWNY"): 1,
        ("Online Polska CJO", "ZAPISANY"): 0,
        ("Online Polska CJO", "REZYGNACJA"): 0,
        ("Online Polska CJO", "INNE"): 0,
    }

    tabela = at.dataframe[0].value
    assert list(tabela.columns) == [
        "Kategoria", "Liczba A", "% A", "Liczba B", "% B", "Różnica (A−B)",
    ]
    assert tabela["Kategoria"].tolist() == ["AKTYWNY", "ZAPISANY", "REZYGNACJA", "INNE"]
    assert tabela["Liczba A"].tolist() == [0, 2, 0, 0]
    assert tabela["Liczba B"].tolist() == [1, 0, 0, 0]
    assert tabela["Różnica (A−B)"].tolist() == [-1, 2, 0, 0]
    assert tabela["% A"].tolist() == [0.0, 1.0, 0.0, 0.0]
    assert tabela["% B"].tolist() == [1.0, 0.0, 0.0, 0.0]


def test_porownanie_przelicza_sie_po_zmianie_pary(tmp_path, monkeypatch):
    """Zamiana A/B odwraca słupki, metryki i różnicę w tabeli."""
    at = odpal(tmp_path, monkeypatch)
    at.selectbox[0].set_value("Online Polska CJO").run()
    assert not at.exception
    # kolizja nie istnieje: B zjechało na Wrocław CJO
    assert at.selectbox[0].value == "Online Polska CJO"
    assert at.selectbox[1].value == "Wrocław CJO"
    assert karta_wyjety(at, "A: ") == {"A: Online Polska CJO": 1}
    paski = dane_wykresu(at, "bar")
    assert {(r["placowka"], r["kategoria"]): r["liczba"] for r in paski}[
        ("Online Polska CJO", "AKTYWNY")
    ] == 1
    tabela = at.dataframe[0].value
    assert tabela["Różnica (A−B)"].tolist() == [1, -2, 0, 0]


def test_porownanie_ignoruje_filtry_kategorii_i_placowki(tmp_path, monkeypatch):
    """Filtry sidebaru nie wchodzą w grę w porównaniu: 4 kategorie zawsze,
    placówki z własnych selektorów. Przegląd przy pustych kategoriach gaśnie
    komunikatem, ale aplikacja żyje dalej."""
    at = odpal(tmp_path, monkeypatch)
    at.multiselect[0].set_value([]).run()
    assert not at.exception
    assert karta_wyjety(at, "A: ") == {"A: Wrocław CJO": 2}
    assert [i.value for i in at.info] == [
        "Wybierz zakres dat oraz co najmniej jedną kategorię statusu."
    ]
    assert at.dataframe[0].value["Kategoria"].tolist() == ["AKTYWNY", "ZAPISANY", "REZYGNACJA", "INNE"]

    # filtr placówki z sidebaru też nie dotyczy porównania
    at2 = odpal(tmp_path, monkeypatch)
    at2.multiselect[1].set_value(["Online Polska CJO"]).run()
    assert not at2.exception
    assert karta_wyjety(at2, "A: ") == {"A: Wrocław CJO": 2}


def test_porownanie_pusty_zakres_dat_komunikat_przeglad_zyje(tmp_path, monkeypatch):
    """Pusty zakres dat w porównaniu → st.info, bez stopowania całej aplikacji."""
    at = odpal(tmp_path, monkeypatch)
    at.date_input[0].set_value((date(2026, 2, 16), date(2026, 3, 2))).run()
    assert not at.exception
    assert "Brak zapisów w porównywanych placówkach dla wybranego zakresu dat." in [
        i.value for i in at.info
    ]
    # Przegląd dla 16.02–2.03: zapis 3 (REZYGNACJA) nie ma placówki i odpada
    # z martów → obie zakładki gaśnie komunikatem, aplikacja bez wyjątku
    assert not karty(at)
    assert "Brak zapisów dla wybranych filtrów." in [i.value for i in at.info]


def test_dashboard_bez_bazy_wskazuje_runner_a_nie_wysadza_sie(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAENG_DUCKDB", str(tmp_path / "nie_istnieje.duckdb"))
    at = AppTest.from_file(str(DASHBOARD), default_timeout=30)
    at.run()
    assert not at.exception
    assert any("run_pipeline" in e.value for e in at.error)
