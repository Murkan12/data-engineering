"""Dashboard Streamlit — konsumuje tabele marta z potoku ELT (tylko do odczytu).

Wymaganie bazy: najpierw uruchom potok:
    python pipeline/run_pipeline.py

Uruchomienie:
    streamlit run app/dashboard.py
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("DATAENG_DUCKDB", BASE_DIR / "data" / "warehouse.duckdb"))

st.set_page_config(page_title="Zapisy na kursy", layout="wide")


@contextlib.contextmanager
def polaczenie() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        yield con
    finally:
        con.close()


def query(sql: str, params: list | None = None) -> pd.DataFrame:
    """Odczyt read-only z bazy DuckDB zmaterializowanej przez potok."""
    with polaczenie() as con:
        return con.execute(sql, params or []).df()


def brak_bazy() -> bool:
    if not DB_PATH.exists():
        return True
    try:
        with polaczenie() as con:
            tabel = {
                row[0]
                for row in con.execute(
                    "SELECT table_name FROM information_schema.tables"
                ).fetchall()
            }
        return {"fakty_zapisy", "mart_nowe_zapisy_dziennie"} - tabel != set()
    except duckdb.Error:
        return True


# Etykiety kategorii biznesowych bierzemy ze słownika (dim_status);
# plaster „INNE" to etykieta własna dashboardu dla kategorii tylko_filter.
KATEGORIA_INNE = "INNE"


if brak_bazy():
    st.error(
        f"Baza {DB_PATH} nie istnieje lub nie zawiera tabel potoku. "
        "Uruchom najpierw potok ELT: `python pipeline/run_pipeline.py`."
    )
    st.stop()

st.title("Zapisy na kursy — lejek rekrutacyjny")
st.caption(
    "Cel procesu: doprowadzić status AKTYWNY do ZAPISANY. "
    "Miara czasu w całym dashboardzie to **data utworzenia zapisu w Fenku** "
    "(nie data zapisu w LangLionie)."
)

KATEGORIA_ETYKIETA = {
    **dict(
        query(
            """
            SELECT kategoria_biznesowa, min(etykieta_ui)
            FROM dim_status
            WHERE kategoria_biznesowa <> 'tylko_filter'
            GROUP BY 1
            """
        ).values
    ),
    "tylko_filter": KATEGORIA_INNE,
}

# --- Filtry: zakres dat + kategoria statusu (story 8, 9) -----------------------
# Kategorie w kolejności lejka; filtruje się po kategoria_biznesowa, nie po
# surowych statusach. Etap zapisu nie jest już filtrem (pozostaje w danych).
KATEGORIE_LEJEK = ["aktywny", "zapisany", "rezygnacja", "tylko_filter"]
etykiety_kategorii = [KATEGORIA_ETYKIETA[k] for k in KATEGORIE_LEJEK]
ETYKIETA_DO_KATEGORIA = dict(zip(etykiety_kategorii, KATEGORIE_LEJEK))

sidebar = st.sidebar
zakres = query(
    "SELECT min(data_utworzenia) AS od, max(data_utworzenia) AS do FROM mart_nowe_zapisy_dziennie"
)
od_do = sidebar.date_input(
    "Zakres dat",
    value=(zakres["od"][0], zakres["do"][0]),
    min_value=zakres["od"][0],
    max_value=zakres["do"][0],
)
wybrane_etykiety = sidebar.multiselect(
    "Kategoria statusu", etykiety_kategorii, default=etykiety_kategorii
)

if len(od_do) != 2 or not wybrane_etykiety:
    st.info("Wybierz zakres dat oraz co najmniej jedną kategorię statusu.")
    st.stop()

wybrane_kategorie = [k for k in KATEGORIE_LEJEK if k in {ETYKIETA_DO_KATEGORIA[e] for e in wybrane_etykiety}]

# Wszystkie wykresy i KPI liczone z jednego przefiltrowanego napływu dziennego,
# więc KPI i pie odzwierciedlają wybrany okres, nie cały zbiór danych.
przeplyw = query(
    """
    SELECT data_utworzenia, kategoria_biznesowa, sum(liczba_nowych_zapisow) AS liczba
    FROM mart_nowe_zapisy_dziennie
    WHERE data_utworzenia BETWEEN ? AND ?
      AND kategoria_biznesowa IN ({})
    GROUP BY 1, 2
    ORDER BY 1
    """.format(", ".join("?" * len(wybrane_kategorie))),
    [od_do[0], od_do[1], *wybrane_kategorie],
)
if przeplyw.empty:
    st.info("Brak zapisów dla wybranych filtrów.")
    st.stop()

liczby_kategorie = przeplyw.groupby("kategoria_biznesowa")["liczba"].sum()
# (etykieta, liczba) per wybrana kategoria — wspólne dla kart KPI i plastra pie.
liczebnosci = [
    (KATEGORIA_ETYKIETA[k], int(liczby_kategorie.get(k, 0))) for k in wybrane_kategorie
]

# --- KPI: suma wybranych kategorii + liczebność per kategoria (story 1, 10) ---
kolumny = st.columns(1 + len(liczebnosci))
kolumny[0].metric("Wszystkie zapisy", int(przeplyw["liczba"].sum()))
for kolumna, (etykieta, liczba) in zip(kolumny[1:], liczebnosci):
    kolumna.metric(etykieta, liczba)

# --- Piechart: wybrane kategorie, plaster „INNE" (story 5, 6) -----------------
pie = pd.DataFrame(liczebnosci, columns=["kategoria", "liczba"])
pie = pie[pie["liczba"] > 0].sort_values("liczba", ascending=False)

opis_kolumna, wykres_kolumna = st.columns([1, 2])
with opis_kolumna:
    st.subheader("Proporcje kategorii statusów")
    st.caption(
        "Proporcje liczone dla wybranego zakresu dat i kategorii. "
        "Statusy HISTORYCZNY (migracyjne) są odrzucane w potoku i nie wchodzą do proporcji."
    )
with wykres_kolumna:
    wykres_pie = (
        alt.Chart(pie)
        .mark_arc(innerRadius=60)
        .encode(
            theta=alt.Theta(field="liczba", type="quantitative"),
            color=alt.Color(
                field="kategoria",
                type="nominal",
                scale=alt.Scale(domain=list(pie["kategoria"]), scheme="set2"),
                legend=alt.Legend(title="Kategoria"),
            ),
            tooltip=["kategoria", "liczba"],
        )
        .properties(height=280)
    )
    st.altair_chart(wykres_pie, width='stretch')

# --- Wykres liniowy: nowo utworzone zapisy w czasie (story 7) ------------------
st.subheader("Nowo utworzone zapisy na dzień")
st.caption("To napływ (flow), nie stan (stock). Oś czasu: data utworzenia w Fenku.")

przeplyw["kategoria"] = przeplyw["kategoria_biznesowa"].map(KATEGORIA_ETYKIETA)
wykres_linia = (
    alt.Chart(przeplyw)
    .mark_line(point=True)
    .encode(
        x=alt.X("data_utworzenia:T", title="Data utworzenia w Fenku"),
        y=alt.Y("liczba:Q", title="Nowe zapisy"),
        color=alt.Color("kategoria:N", legend=alt.Legend(title="Kategoria")),
        tooltip=["data_utworzenia", "kategoria", "liczba"],
    )
    .properties(height=320)
)
st.altair_chart(wykres_linia, width='stretch')
