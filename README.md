# Pipeline zapisów — DuckDB ELT + dashboard Streamlit

Szkoła językowa: liczebność zapisów na kursy i ich podział na kategorie
biznesowe `AKTYWNY` / `ZAPISANY` / `REZYGNACJA` (plus `INNE`).

## Uruchomienie

```bash
# 1. Potok ELT: CSV → staging → transform → marts (pełne, idempotentne przeładowanie)
python pipeline/run_pipeline.py            # opcje --csv / --db

# 2. Dashboard (czyta tę samą bazę w trybie read-only)
streamlit run app/dashboard.py
```

Domyślne ścieżki: źródło `data/zapisy_grupy_historia.csv`, baza
`data/warehouse.duckdb` (oba nadpisalne przez `--csv` / `--db`).

## Model danych

| Tabela | Rola |
|---|---|
| `staging_zapisy` | surowy CSV, wszystko `VARCHAR`, separator `;` |
| `dim_status` | słownik statusów z `kategoria_biznesowa` (fail-fast na nieznanych) |
| `fakty_zapisy` | jeden wiersz na `id_zapisu`; odrzuca `HISTORYCZNY`; dedup multi-grup |
| `zdarzenia` | unpivot aktywności: `id_zapisu / aktywnosc / czas_wystapienia / zrodlo_potwierdzenia` |
| `mart_liczby_statusu` | liczebności lifetime per status/kategoria (`COUNT(DISTINCT id_zapisu)`); materiał hurtowni, poza obiegiem dashboardu |
| `mart_nowe_zapisy_dziennie` | flow nowych zapisów na dzień; źródło KPI, pie i wykresu liniowego (filtrowalne) |

Uwaga biznesowa: miarą czasu jest **data utworzenia w Fenku**, nie data zapisu
w LangLionie. Status `HISTORYCZNY` (~3268 wierszy) to artefakt migracyjny i jest
odrzucany w transformacji.

## Dashboard

Karty KPI („Wszystkie zapisy" + liczebność per kategoria), piechart proporcji
i wykres liniowy nowych zapisów — wszystkie liczone z `mart_nowe_zapisy_dziennie`
dla tych samych filtrów: **zakres dat** i **kategoria statusu** (`AKTYWNY`,
`ZAPISANY`, `REZYGNACJA`, `INNE` w kolejności lejka, domyślnie wszystkie).
Grupowanie `INNE` (statusy `tylko_filter`) istnieje tylko w dashboardzie.
Dawniejszy filtr „Etap zapisu" usunięty; kolumna `etap_zapisu` pozostaje w danych.

## Testy

```bash
python -m pytest tests/
```

Szew testów: runner `run_pipeline.py` na fixture'ach + smoke-testy dashboardu
(AppTest: liczby KPI w stanie domyślnym, reakcja na filtry, stany puste).

## Specyfikacja

Pełne wymagania i decyzje: [`docs/spec-zapisy-pipeline.md`](docs/spec-zapisy-pipeline.md).
Notebook `transform.ipynb` to prior art eksploracyjny (poza potokiem).
