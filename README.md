# Pipeline zapisów — DuckDB ELT + dashboard Streamlit

Szkoła językowa: liczebność zapisów na kursy i ich podział na kategorie
biznesowe `AKTYWNY` / `ZAPISANY` / `REZYGNACJA` (plus `INNE`), z filtrem
placówek i porównaniem dwóch placówek.

## Uruchomienie

```bash
# 1. Potok ELT: CSV → staging → słowniki+wymiary → transform → marts
#    (pełne, idempotentne przeładowanie)
python pipeline/run_pipeline.py            # opcje --csv / --db

# 2. Dashboard (czyta tę samą bazę w trybie read-only)
streamlit run app/dashboard.py
```

Domyślne ścieżki: źródło `data/zapisy_grupy_placowka.csv` (snapshot 25.10.2026
z kolumnami placówki), baza `data/warehouse.duckdb` (oba nadpisalne przez
`--csv` / `--db`). Archiwalny `data/zapisy_grupy_historia.csv` zostaje w repo
jako starszy snapshot (07.10) — **bez** kolumn placówki, więc nie da się na nim
uruchomić obecnej wersji potoku. Różnice między snapshotami: +227 zapisów oraz
153 statusy przesterowane w nowym pliku.

## Model danych

| Tabela | Rola |
|---|---|
| `staging_zapisy` | surowy CSV, wszystko `VARCHAR`, separator `;`; kolumny `id_placowki`, `placowka_z_zapisu` (`nullif ''`) |
| `dim_status` | słownik statusów z `kategoria_biznesowa` (fail-fast na nieznanych) |
| `dim_placowka` | wymiar placówek: `id_placowki PK`, `nazwa_wyswietlana` (normalizacja diacritików, „(bez nazwy)"), `typ ∈ {realna, techniczna, bez_nazwy}` |
| `fakty_zapisy` | jeden wiersz na `id_zapisu` (+ `id_placowki`); odrzuca `HISTORYCZNY`; dedup multi-grup |
| `zdarzenia` | unpivot aktywności: `id_zapisu / aktywnosc / czas_wystapienia / zrodlo_potwierdzenia` |
| `mart_liczby_statusu` | liczebności lifetime per status/kategoria × placówka (`COUNT(DISTINCT id_zapisu)`); tylko realne placówki (`typ='realna'`); materiał hurtowni, poza obiegiem dashboardu |
| `mart_nowe_zapisy_dziennie` | flow nowych zapisów na dzień z wymiarem `placowka` (tylko realne place — dane testowe i wiersze bez placówki spadają); źródło KPI, pie, linii i porównania (filtrowalne) |

Asercje potoku (fail-fast): nieznany status spoza słownika oraz rozjazd
nazw pod jednym `id_placowki` (też wariant „nazwa + pusta nazwa"), duplikat
nazwy displayowej pod różnymi ID oraz brak pokrycia `dim_placowka` ze
stagingiem przerywają działanie z nonzero exit code, przed materializacją
faktów.

Uwaga biznesowa: miarą czasu jest **data utworzenia w Fenku**, nie data zapisu
w LangLionie. Status `HISTORYCZNY` (~3270 wierszy) to artefakt migracyjny i jest
odrzucany w transformacji.

## Dashboard

Dwie zakładki: **Przegląd** i **Porównanie placówek**. Filtry w sidebarze
(obowiązują od lewej):

- **Zakres dat** — obie zakładki,
- **Kategoria statusu** (`AKTYWNY`, `ZAPISANY`, `REZYGNACJA`, `INNE`) — tylko Przegląd,
- **Placówka** (multiselect z listy realnych placówek wymiaru
  `typ='realna'`, domyślnie wszystkie) — tylko Przegląd; przelicza
  KPI/pie/linię w miejscu, bez nowych elementów wykresów. Byt testowy
  („X-Baza Testowa"), placówka bez nazwy („(bez nazwy)") i wiersze bez
  placówki nie mają odniesienia w dashboardzie — odpada też
  kubełek „(brak placówki)".

Przegląd: karty KPI („Wszystkie zapisy" + liczebność per kategoria), piechart
proporcji i wykres liniowy nowych zapisów — wszystkie liczone z
`mart_nowe_zapisy_dziennie` dla tych samych filtrów. Grupowanie `INNE`
(statusy `tylko_filter`) istnieje tylko w dashboardzie.

Porównanie placówek: selektory A/B z **wyłącznie realnych** placówek
(`typ='realna'` w `dim_placowka` — z „Dział Sprzedaży", bez „X-Baza Testowa"
i „(bez nazwy)"), para rozłączna, domyślnie top-2 wg liczby
zapisów wyliczana z bazy. Grupowany bar 4 kategorie × 2 placówki + tabela
(liczba, % udziału wewn., różnica A−B). Zakres dat z sidebaru obowiązuje,
filtr kategorii — nie. Puste stany pokazują `st.info` bez stopowania drugiej
zakładki.

## Testy

```bash
python -m pytest tests/
```

Szew testów: runner `run_pipeline.py` na fixture'ach (w tym asercje placówek i
normalizacja nazw w `dim_placowka`) + smoke-testy dashboardu (AppTest: liczby
KPI, filtr placówki, porównanie A/B — domyślna para, zmiana pary, kolizja,
stany puste).

## Specyfikacja

Pełne wymagania i decyzje: [`docs/spec-zapisy-pipeline.md`](docs/spec-zapisy-pipeline.md).
Notebook `transform.ipynb` to prior art eksploracyjny (poza potokiem).
