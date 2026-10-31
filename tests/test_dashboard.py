"""Smoke-testy dashboardu: uruchamialność i liczby na kartach KPI.

Logika dashboardu to zapytania o już przetestowane materiały — testujemy
tylko, że aplikacja się renderuje bez wyjątków i pokazuje poprawne KPI.
"""

import os
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

from run_pipeline import run_pipeline  # noqa: E402

DASHBOARD = ROOT / "app" / "dashboard.py"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "zapisy_minimal.csv"


def odpal(tmp_path: Path) -> AppTest:
    db = tmp_path / "warehouse.duckdb"
    run_pipeline(FIXTURE, db)
    os.environ["DATAENG_DUCKDB"] = str(db)
    at = AppTest.from_file(str(DASHBOARD), default_timeout=30)
    at.run()
    return at


def test_dashboard_sie_uruchamia_i_klada_kpi(tmp_path):
    at = odpal(tmp_path)
    assert not at.exception
    karty = {m.label: int(m.value) for m in at.metric}
    assert karty["AKTYWNY"] == 1
    assert karty["ZAPISANY"] == 2
    assert karty["Rezygnacja"] == 1
    assert karty["Wszystkie zapisy"] == 4


def test_dashboardBezBazy_wskazowuje_runner_a_nie_wysadza_sie(tmp_path):
    os.environ["DATAENG_DUCKDB"] = str(tmp_path / "nie_istnieje.duckdb")
    at = AppTest.from_file(str(DASHBOARD), default_timeout=30)
    at.run()
    assert not at.exception
    assert any("run_pipeline" in e.value for e in at.error)
