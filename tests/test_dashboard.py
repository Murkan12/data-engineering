"""Smoke-testy dashboardu: uruchamialność i liczby KPI/pie względem filtrów.

Logika dashboardu to zapytania o już przetestowane materiały — testujemy, że
aplikacja się renderuje bez wyjątków, pokazuje poprawne KPI w stanie domyślnym
i że KPI oraz pie reagują na filtry (kategoria, zakres dat, stany puste).
"""

import json
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


def odpal(tmp_path: Path, monkeypatch) -> AppTest:
    db = tmp_path / "warehouse.duckdb"
    run_pipeline(FIXTURE, db)
    monkeypatch.setenv("DATAENG_DUCKDB", str(db))
    at = AppTest.from_file(str(DASHBOARD), default_timeout=30)
    at.run()
    return at


def karty(at: AppTest) -> dict[str, int]:
    return {m.label: int(m.value) for m in at.metric}


def plaster_pie(at: AppTest) -> dict[str, int]:
    """Dane plastra (kategoria -> liczba) z wykresu kołowego, zdekodowane z_arrow."""
    for wykres in at.get("vega_lite_chart"):
        spec = json.loads(wykres.proto.spec)
        mark = spec["mark"]
        if (mark["type"] if isinstance(mark, dict) else mark) != "arc":
            continue
        name = spec["data"]["name"]
        for ds in wykres.proto.datasets:
            if ds.name == name:
                wiersze = pa.ipc.open_stream(ds.data.data).read_all().to_pylist()
                return {r["kategoria"]: int(r["liczba"]) for r in wiersze}
    raise AssertionError("brak wykresu kołowego (arc) w dashboardzie")


def test_dashboard_sie_uruchamia_i_pokazuje_kpi(tmp_path, monkeypatch):
    """Domyślne filtry: 5 kart KPI (wszystkie + 4 kategorie w kolejności lejka).

    Liczby niezależnie przepracowane z fixture: 4 unikalne zapisy po odrzuceniu
    HISTORYCZNY (id 4) i dedupie (id 5) — AKTYWNY 1, ZAPISANY 2, REZYGNACJA 1,
    tylko_filter (INNE) 0.
    """
    at = odpal(tmp_path, monkeypatch)
    assert not at.exception
    assert karty(at) == {
        "Wszystkie zapisy": 4,
        "AKTYWNY": 1,
        "ZAPISANY": 2,
        "REZYGNACJA": 1,
        "INNE": 0,
    }
    # pie pomija kategorie bez zapisów (INNE=0) — plaster o polu 0 nie istnieje
    assert plaster_pie(at) == {"ZAPISANY": 2, "AKTYWNY": 1, "REZYGNACJA": 1}
    # filtr „Kategoria statusu" zastępuje filtry po statusie i etapie
    assert [w.label for w in at.multiselect] == ["Kategoria statusu"]
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
    at = odpal(tmp_path, monkeypatch)
    at.multiselect[0].set_value([]).run()
    assert not at.exception
    assert not at.metric
    assert not at.subheader
    assert [i.value for i in at.info] == ["Wybierz zakres dat oraz co najmniej jedną kategorię statusu."]


def test_pusty_zakres_dat_pokazuje_jeden_komunikat_bez_wykresow(tmp_path, monkeypatch):
    at = odpal(tmp_path, monkeypatch)
    at.date_input[0].set_value((date(2026, 2, 16), date(2026, 3, 2))).run()
    assert not at.exception
    assert not at.metric
    assert not at.subheader
    assert [i.value for i in at.info] == ["Brak zapisów dla wybranych filtrów."]


def test_dashboard_bez_bazy_wskazuje_runner_a_nie_wysadza_sie(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAENG_DUCKDB", str(tmp_path / "nie_istnieje.duckdb"))
    at = AppTest.from_file(str(DASHBOARD), default_timeout=30)
    at.run()
    assert not at.exception
    assert any("run_pipeline" in e.value for e in at.error)
