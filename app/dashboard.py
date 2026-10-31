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
        return {"fakty_zapisy", "mart_nowe_zapisy_dziennie", "dim_placowka"} - tabel != set()
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

# --- Filtry: zakres dat + kategoria statusu (story 8, 9) + placówka (story 26) --
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

# Fragmentator placówki: globalny multiselect z pełną listą z wymiaru
# + kubełek „(brak placówki)", domyślnie wszystkie — przelicza KPI/pie/linia
# w miejscu, bez nowych elementów wykresów. Dotyczy tylko zakładki „Przegląd".
PLACOWKA_BRAK = "(brak placówki)"
etykiety_placowek = list(
    dict.fromkeys(
        query("SELECT nazwa_wyswietlana FROM dim_placowka ORDER BY 1")["nazwa_wyswietlana"].tolist()
        + [PLACOWKA_BRAK]
    )
)
wybrane_placowki = sidebar.multiselect(
    "Placówka", etykiety_placowek, default=etykiety_placowek
)

# --- Zakładki -------------------------------------------------------------------
# Filtry kategorii i placówki z sidebaru NIE dotyczą porównania (placówki mają
# własne selektory, kategorie są zawsze 4). Puste stany nie stopują całej aplikacji.
tab_przeglad, tab_porownanie = st.tabs(["Przegląd", "Porównanie placówek"])

with tab_przeglad:
    if len(od_do) != 2 or not wybrane_etykiety:
        st.info("Wybierz zakres dat oraz co najmniej jedną kategorię statusu.")
    else:
        wybrane_kategorie = [
            k for k in KATEGORIE_LEJEK if k in {ETYKIETA_DO_KATEGORIA[e] for e in wybrane_etykiety}
        ]

        # Wszystkie wykresy i KPI liczone z jednego przefiltrowanego napływu
        # dziennego, więc KPI i pie odzwierciedlają wybrane filtry, nie cały zbiór.
        if wybrane_placowki:
            przeplyw = query(
                """
                SELECT data_utworzenia, kategoria_biznesowa, sum(liczba_nowych_zapisow) AS liczba
                FROM mart_nowe_zapisy_dziennie
                WHERE data_utworzenia BETWEEN ? AND ?
                  AND kategoria_biznesowa IN ({})
                  AND placowka IN ({})
                GROUP BY 1, 2
                ORDER BY 1
                """.format(
                    ", ".join("?" * len(wybrane_kategorie)),
                    ", ".join("?" * len(wybrane_placowki)),
                ),
                [od_do[0], od_do[1], *wybrane_kategorie, *wybrane_placowki],
            )
        else:
            przeplyw = pd.DataFrame(columns=["data_utworzenia", "kategoria_biznesowa", "liczba"])

        if przeplyw.empty:
            st.info("Brak zapisów dla wybranych filtrów.")
        else:
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
                    "Proporcje liczone dla wybranego zakresu dat, kategorii i placówek. "
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

with tab_porownanie:
    # Porównanie dwóch placówek (story 27, 28). Selektory tylko z realnymi
    # placówkami — wykluczenie technicznych/bez nazwy/kubełka idzie przez
    # atrybut typ w dim_placowka, nie twardą listę tutaj.
    realne_placowki = query(
        "SELECT nazwa_wyswietlana FROM dim_placowka WHERE typ = 'realna' ORDER BY 1"
    )["nazwa_wyswietlana"].tolist()

    if len(realne_placowki) < 2:
        st.info("Porównanie wymaga co najmniej dwóch realnych placówek w hurtowni.")
    else:
        # Domyślna para: top-2 realnych placówek po liczbie zapisów, wyliczana
        # z bazy przy każdym runie (para może się zmienić przy kolejnym
        # dociągnięciu danych) — z fallbackiem na pierwszą realną placówkę.
        top2 = query(
            """
            SELECT p.nazwa_wyswietlana AS placowka
            FROM mart_nowe_zapisy_dziennie m
            JOIN dim_placowka p USING (id_placowki)
            WHERE p.typ = 'realna'
            GROUP BY 1
            ORDER BY sum(m.liczba_nowych_zapisow) DESC, 1
            LIMIT 2
            """
        )["placowka"].tolist()
        a_domysl = top2[0] if top2 else realne_placowki[0]
        b_domysl = next((x for x in top2[1:] + realne_placowki if x != a_domysl), a_domysl)

        kol_a, kol_b = st.columns(2)
        placowka_a = kol_a.selectbox(
            "Placówka A", realne_placowki, index=realne_placowki.index(a_domysl)
        )
        # Rozłączność par: B nigdy nie oferuje wybranej A; gdy A wskoczy na stare
        # B, selektor B przesuwa się na default liczony bez kolizji.
        opcje_b = [x for x in realne_placowki if x != placowka_a]
        placowka_b = kol_b.selectbox(
            "Placówka B",
            opcje_b,
            index=opcje_b.index(b_domysl) if b_domysl in opcje_b else 0,
        )

        if len(od_do) != 2:
            st.info("Wybierz pełny zakres dat w sidebarze.")
        else:
            # Zakres dat z sidebaru obowiązuje; kategorie zawsze wszystkie 4
            # (filtr kategorii z sidebaru nie wchodzi w grę).
            dane_pary = query(
                """
                SELECT placowka, kategoria_biznesowa, sum(liczba_nowych_zapisow) AS liczba
                FROM mart_nowe_zapisy_dziennie
                WHERE placowka IN (?, ?)
                  AND data_utworzenia BETWEEN ? AND ?
                GROUP BY 1, 2
                """,
                [placowka_a, placowka_b, od_do[0], od_do[1]],
            )

            siatka = {(pl, et): 0 for pl in (placowka_a, placowka_b) for et in etykiety_kategorii}
            for wiersz in dane_pary.itertuples(index=False):
                etykieta = KATEGORIA_ETYKIETA[wiersz.kategoria_biznesowa]
                siatka[(wiersz.placowka, etykieta)] += int(wiersz.liczba)

            suma_a = sum(v for (pl, _), v in siatka.items() if pl == placowka_a)
            suma_b = sum(v for (pl, _), v in siatka.items() if pl == placowka_b)

            if suma_a == 0 and suma_b == 0:
                st.info("Brak zapisów w porównywanych placówkach dla wybranego zakresu dat.")
            else:
                metryki = st.columns(2)
                metryki[0].metric(f"A: {placowka_a}", suma_a)
                metryki[1].metric(f"B: {placowka_b}", suma_b)

                st.subheader("Kategorie statusu — A vs B")
                st.caption(
                    "Grupowane słupki: 4 kategorie statusu × 2 placówki, zakres dat "
                    "z sidebaru. Paleta spójna z plea kategorii (set2)."
                )
                pary = pd.DataFrame(
                    [
                        {"kategoria": et, "placowka": pl, "liczba": siatka[(pl, et)]}
                        for et in etykiety_kategorii
                        for pl in (placowka_a, placowka_b)
                    ]
                )
                wykres_pary = (
                    alt.Chart(pary)
                    .mark_bar()
                    .encode(
                        x=alt.X("kategoria:N", sort=list(etykiety_kategorii), title="Kategoria"),
                        xOffset="placowka:N",
                        y=alt.Y("liczba:Q", title="Zapisy"),
                        color=alt.Color(
                            "placowka:N",
                            scale=alt.Scale(scheme="set2"),
                            legend=alt.Legend(title="Placówka"),
                        ),
                        tooltip=["placowka", "kategoria", "liczba"],
                    )
                    .properties(height=320)
                )
                st.altair_chart(wykres_pary, width='stretch')

                # % = udział wewnętrzny placówki w 4 kategoriach; różnica A−B co do liczby.
                tabela = pd.DataFrame(
                    {
                        "Kategoria": list(etykiety_kategorii),
                        "Liczba A": [siatka[(placowka_a, et)] for et in etykiety_kategorii],
                        "% A": [
                            siatka[(placowka_a, et)] / suma_a if suma_a else 0.0
                            for et in etykiety_kategorii
                        ],
                        "Liczba B": [siatka[(placowka_b, et)] for et in etykiety_kategorii],
                        "% B": [
                            siatka[(placowka_b, et)] / suma_b if suma_b else 0.0
                            for et in etykiety_kategorii
                        ],
                        "Różnica (A−B)": [
                            siatka[(placowka_a, et)] - siatka[(placowka_b, et)]
                            for et in etykiety_kategorii
                        ],
                    }
                )
                st.dataframe(
                    tabela,
                    hide_index=True,
                    width='stretch',
                    column_config={
                        "% A": st.column_config.NumberColumn(format="percent"),
                        "% B": st.column_config.NumberColumn(format="percent"),
                    },
                )
