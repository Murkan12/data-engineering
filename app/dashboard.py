"""Dashboard Streamlit — konsumuje tabele marta z potoku ELT (tylko do odczytu).

Wymaganie bazy: najpierw uruchom potok:
    python pipeline/run_pipeline.py

Uruchomienie:
    streamlit run app/dashboard.py
"""

from __future__ import annotations

import os
from pathlib import Path

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("DATAENG_DUCKDB", BASE_DIR / "data" / "warehouse.duckdb"))

KATEGORIA_ETYKIETA = {
    "aktywny": "AKTYWNY",
    "zapisany": "ZAPISANY",
    "rezygnacja": "Rezygnacja",
    "tylko_filter": "INNE",
}

st.set_page_config(page_title="Zapisy na kursy", layout="wide")


@st.cache_data(ttl=300)
def query(sql: str, params: list | None = None) -> pd.DataFrame:
    """Odczyt read-only z bazy DuckDB zmaterializowanej przez potok."""
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return con.execute(sql, params or []).df()
    finally:
        con.close()


def brak_bazy() -> bool:
    if not DB_PATH.exists():
        return True
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        tabel = {
            row[0]
            for row in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
        }
        return {"fakty_zapisy", "mart_liczby_statusu", "mart_nowe_zapisy_dziennie"} - tabel != set()
    except duckdb.Error:
        return True
    finally:
        con.close()


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

# --- KPI: liczebności bezwzględne trzech kategorii (story 1, 11) --------------
liczby_kategorie = query(
    """
    SELECT kategoria_biznesowa, sum(liczba_zapisow) AS liczba
    FROM mart_liczby_statusu
    GROUP BY 1
    """
)
do_kategorii = liczby_kategorie.set_index("kategoria_biznesowa")["liczba"].to_dict()

kolumny = st.columns(4)
kolumny[0].metric("Wszystkie zapisy", int(query("SELECT count(*) AS n FROM fakty_zapisy")["n"][0]))
for kolumna, kategoria in zip(kolumny[1:], ["aktywny", "zapisany", "rezygnacja"]):
    kolumna.metric(KATEGORIA_ETYKIETA[kategoria], int(do_kategorii.get(kategoria, 0)))

# --- Piechart: 3 kategorie + plaster „INNE" (story 5, 6) ----------------------
pie = liczby_kategorie.copy()
pie["kategoria"] = pie["kategoria_biznesowa"].map(KATEGORIA_ETYKIETA)
pie = pie.sort_values("liczba", ascending=False)

co, pie_kolumna = st.columns([1, 2])
with co:
    st.subheader("Proporcje kategorii statusów")
    st.caption("Statusy HISTORYCZNY (migracyjne) są odrzucane w potoku i nie wchodzą do proporcji.")
with pie_kolumna:
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

# --- Wykres liniowy: nowo utworzone zapisy w czasie, z filtrami (story 7–10) --
st.subheader("Nowo utworzone zapisy na dzień")
st.caption("To napływ (flow), nie stan (stock). Oś czasu: data utworzenia w Fenku.")

zakres = query(
    "SELECT min(data_utworzenia) AS od, max(data_utworzenia) AS do FROM mart_nowe_zapisy_dziennie"
)
statusy = query(
    """
    SELECT DISTINCT f.status_zapisu AS status, d.etykieta_ui AS etykieta
    FROM fakty_zapisy f JOIN dim_status d ON d.status_zapisu = f.status_zapisu
    ORDER BY 2
    """
)
etapy = query("SELECT DISTINCT etap_zapisu FROM mart_nowe_zapisy_dziennie ORDER BY 1")

z_sidebarem = st.sidebar
od_do = z_sidebarem.date_input(
    "Zakres dat",
    value=(zakres["od"][0], zakres["do"][0]),
    min_value=zakres["od"][0],
    max_value=zakres["do"][0],
)
wybrane_statusy = z_sidebarem.multiselect(
    "Status zapisu", statusy["status"].tolist(), default=statusy["status"].tolist()
)
wybrane_etapy = z_sidebarem.multiselect(
    "Etap zapisu", etapy["etap_zapisu"].tolist(), default=etapy["etap_zapisu"].tolist()
)

if len(od_do) == 2 and wybrane_statusy and wybrane_etapy:
    przeplyw = query(
        """
        SELECT data_utworzenia, kategoria_biznesowa, sum(liczba_nowych_zapisow) AS liczba
        FROM mart_nowe_zapisy_dziennie
        WHERE data_utworzenia BETWEEN ? AND ?
          AND status_zapisu IN ({})
          AND etap_zapisu IN ({})
        GROUP BY 1, 2
        ORDER BY 1
        """.format(
            ", ".join("?" * len(wybrane_statusy)), ", ".join("?" * len(wybrane_etapy))
        ),
        [od_do[0], od_do[1], *wybrane_statusy, *wybrane_etapy],
    )
    if przeplyw.empty:
        st.info("Brak zapisów dla wybranych filtrów.")
    else:
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
else:
    st.info("Wybierz zakres dat oraz co najmniej jeden status i etap.")
