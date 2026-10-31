# Spec: Pipeline zapisów — DuckDB ELT + dashboard Streamlit

> Status: gotowe do implementacji (`ready-for-agent`)
> Źródło: ustrzylenie planu w rozmowie (grill-me), dane: `data/zapisy_grupy_placowka.csv` (snapshot 25.10.2026 z placówkami; archiwalnie `data/zapisy_grupy_historia.csv`)
> Wcześniejsze badania eksploracyjne: notebook `transform.ipynb` (pandas) — potwierdza kierunek unpivotu zdarzeń.

## Problem Statement

Szkoła językowa potrzebuje zbadać aktualną liczbę sumaryczną zapisów na kursy oraz ich podział na trzy kluczowe kategorie biznesowe: `AKTYWNY` (w procesie sprzedaży), `REZYGNACJA` (zrezygnowano z zapisu) i `ZAPISANY` (osoba zapisana pomyślnie na kurs). Dane źródłowe pochodzą z systemu operacyjnego w postaci zdenormalizowanego, semicolonowego CSV z polskimi formatami dat — zawierają 9 statusów (w tym dominujący technicznie `HISTORYCZNY`), zdublikowane wpisy zapisów wielo-grupowych oraz artefakty migracyjne (~3150 wierszy z identyczną minutą `15.02.2026 16:49`). Bez oczyszczenia i przebudowy modelu te dane nie odpowiadają wiarygodnie na pytanie biznesowe.

## Solution

Potok ELT: plik CSV ładowany jest w surowej postaci do DuckDB (warstwa staging), transformowany do znormalizowanego modelu tabel faktów (`fakty_zapisy` — jeden wiersz na zapis, z `id_placowki`; `zdarzenia` — nienormalizowany schemat `zapis–aktywność–czas`), z wymiarami `dim_status` i `dim_placowka`, kontrolowany asercjami fail-fast i materializowany do tabel marta, które konsumuje dashboard Streamlit (read-only). Dashboard ma dwie zakładki: **Przegląd** (liczebności kategorii — KPI, piechart, wykres liniowy — wszystkie liczone przez te same filtry: zakres dat, kategoria statusu i fragmentator placówki) oraz **Porównanie placówek** (grupowany bar 4 kategorii statusu × 2 realne placówki + tabela z liczbami, udziałami i różnicą A−B, w zakresie dat z sidebaru).

Zdefiniowany cel biznesowy procesu: doprowadzić status `AKTYWNY` do `ZAPISANY` — dashboard ma uwidaczniać oba oraz `REZYGNACJA` jako utratę leada.

## User Stories

1. Jako analityk sprzedaży, chcę widzieć aktualną łączną liczbę zapisów (unikalne `ID zapisu`), żeby znać realną skalę rekrutacji bez zawyżenia przez zapisy wielo-grupowe.
2. Jako analityk sprzedaży, chcę widzieć liczebność zapisów w statusie `AKTYWNY`, żeby mierzyć wielkość lejka w procesie sprzedaży.
3. Jako analityk sprzedaży, chcę widzieć liczebność zapisów w statusie `ZAPISANY`, żeby mierzyć liczbę faktycznie pozyskanych kursantów.
4. Jako analityk sprzedaży, chcę widzieć liczebność zapisów w statusie `REZYGNACJA`, żeby monitorować odpływ i badać jego przyczyny.
5. Jako menedżer, chcę piechart z proporcjami kategorii statusów, żeby jednym spojrzeniem ocenić zdrowie lejka.
6. Jako menedżer, chcę na piecharcie osobny plaster „pozostałe statusy", żeby proporcje sumowały się do 100% i nie wprowadzały w błąd.
7. Jako analityk, chcę wykres liniowy nowo utworzonych zapisów na dzień (wg `Utworzono zapis w Fenku`), żeby widzieć trendy napływu w czasie.
8. Jako analityk, chcę filtrować dashboard zakresem dat, żeby wycinać sezony rekrutacyjne.
9. Jako analityk, chcę filtrować dashboard po kategorii statusu (`AKTYWNY`, `ZAPISANY`, `REZYGNACJA`, `INNE`), żeby porównywać napływ kończący się zapisem vs. rezygnacją.
10. Jako użytkownik dashboardu, chcę kart KPI z liczbami bezwzględnymi dla wybranych kategorii (plus suma „Wszystkie zapisy"), dopasowanych do aktywnych filtrów, żeby nie liczyć z osi wykresów.
11. Jako inżynier danych, chcę, by wiersze `HISTORYCZNY` były odrzucane w transformacji, żeby status techniczny/migracyjny nie skażał żadnej metryki.
12. Jako inżynier danych, chcę deduplikować zapisy po `ID zapisu` (zapis w wielu grupach to nadal jeden zapis), żeby każda metryka była liczona w ziarnie „zapis".
13. Jako inżynier danych, chcę tabelę `zdarzenia` w schemacie `id_zapisu / aktywność / czas_wystąpienia`, żeby każda aktywność z kolumny źródłowej stała się pierwszym klasyfikiem zdarzeniem bez zdenormalizowanej kolumny.
14. Jako inżynier danych, chcę, by pusta kolumna aktywności oznaczała „aktywność nie wystąpiła" (brak wiersza), żeby nie zapełniać tabeli zdarzeń nullami.
15. Jako inżynier danych, chcę, by zdarzenia zdeduplikowane po `(id_zapisu, aktywność, czas)`, żeby zapisy wielo-grupowe nie mnożyły historii zdarzeń.
16. Jako inżynier danych, chcę kolumny „źródło potwierdzenia" powieszone na właściwych zdarzeniach (umowa, nauczyciel), żeby atrybuty jakościowe nie ginęły w relacji.
17. Jako developer, chcę asercję słownika statusów z przerwanym działaniem (fail-fast) po wystąpieniu nieznanego statusu, żeby cicho nie zafaszowywać nowych wartości z systemu źródłowego.
18. Jako developer, chcę centralną mapę statusów z kategorią biznesową (aktywny / zapisany / rezygnacja / tylko-filter), żeby `ZDUBLOWANY_ZAPIS` i `ZAPIS_GRUPOWY` służyły wyłącznie filtrowaniu.
19. Jako developer, chcę zmaterializowane tabele (nie widoki) w warstwie marta, żeby debugowanie i Development było pewniejsze i powtarzalne.
20. Jako developer, chcę pełnego, idempotentnego przeładowania pliku przy każdym uruchomieniu potoku, żeby stan hurtowni zawsze odpowiadał stanowi pliku.
21. Jako developer, chcę, by potok był odporny na polski format dat (`DD.MM.YYYY HH:MM`, dni/miesiące jednocyfrowe), żeby parsowanie nie zawieszało transformacji.
22. Jako developer, chcę warstwę staging z typem `VARCHAR` na wszystkim, żeby surowe dane nigdy nie były odrzucane przez parser przy imporcie.
23. Jako użytkownik dashboardu, chcę wiedzieć (etykietka/podpis), że miara czasu to data utworzenia w Fenku, żeby nie mylić jej z datą zapisu w LangLionie.
24. Jako właściciel danych, chcę, żeby artefakty migracyjne nie dominowały wykresu liniowego, żeby po odrzuceniu `HISTORYCZNY` nie pozostał fałszywy pik w jednej minucie.
25. Jako przyszły analityk, chcę dostęp do tabeli `zdarzenia` w DuckDB, żeby móc budować miary konwersji lejka (czas AKTYWNY → ZAPISANY) bez zmian w potoku.
26. Jako menedżer regionów, chcę filtrować zakładkę Przegląd po realnych placówkach (multiselect z listy `typ='realna'`, domyślnie wszystkie), żeby oceniać lejek wybranej grupy placówek bez przebudowy wykresów — KPI, pie i linia przeliczają się w miejscu.
27. Jako menedżer, chcę zakładkę porównującą dwie placówki w 4 kategoriach statusu (grupowany bar + tabela: liczba, % udziału wewnętrznego, różnica A−B), żeby konfrontować ich wyniki rekrutacyjne w tym samym zakresie dat.
28. Jako menedżer, chcę w selektorach porównania wyłącznie realne placówki (bez bytów testowych i placówki bez nazwy), żeby nie porównywać artefaktów systemowych z kursami; para A/B ma być zawsze rozłączna. „Dział Sprzedaży" (222) jest realną placówką i bierze udział w porównaniu.
29. Jako analityk, chcę znormalizowane etykiety displayowe placówek w hurtowni (diacritiki „Poznań/Wrocław", „(bez nazwy)" dla ID 200), a dane niefunkcjonalne (206 „X-Baza Testowa" — testowa, 200 — bez nazwy, wiersze bez placówki) chcę całkowicie poza martami i dashboardem (bez kubełka „(brak placówki)"), żeby wszystkie raporty używały jednej poprawnej nazwy i nie miały odniesienia artefaktów.
30. Jako developer, chcę asercje wymiaru placówek (jedna nazwa na ID, pełne pokrycie ID ze stagingu) z fail-fast przed faktami, żeby rozjazd nazw w źródle nie wszedł cicho do marta.

## Implementation Decisions

- **Architektura**: dwuczęściowy potok — (1) runner ELT `run_pipeline.py` + skrypty SQL DuckDB (staging → transform → marts → checks), (2) aplikacja Streamlit czytająca ten sam plik bazy w trybie read-only. Pełne przeładowanie; plik traktowany jako jednorazowy eksperyment (brak trybu inkrementalnego).
- **Ziarno metryki**: `COUNT(DISTINCT id_zapisu)` — decyzja „1b" z rozmowy.
- **Model L1 — `fakty_zapisy`**: kolumny źródłowe od `ID zapisu` do `Stan grupy`, jeden wiersz na zapis; filtr `status_zapisu != 'HISTORYCZNY'`; deduplikacja `QUALIFY row_number() OVER (PARTITION BY id_zapisu ...)`; znana konsekwencja: utrata relacji zapis–wiele-grup (w razie potrzeby ewentualna przyszła tabela asocjacyjna). Zweryfikowane w danych: duplikaty różnią się wyłącznie `ID grupy`, więc dedup po ID zapisu nie gubi statusu/etapu/dat.
- **Model L1 — `zdarzenia`**: unpivot wszystkich kolumn aktywności (w tym `Utworzono zapis w Fenku` jako aktywność) do trójkowego schematu `id_zapisu / aktywność / czas_wystąpienia`; tylko nie-null wartości; dedup po trójce kluczy; kolumny „Źródło potwierdzenia umowy/nauczyciela" jako atrybuty odpowiednich zdarzeń.
- **Źródło prawdy (runda 2026‑10‑31, trzecia)**: domyślne `--csv` to `data/zapisy_grupy_placowka.csv` — nowszy snapshot (25.10) będący nadzbiorem historia.csv po kluczu `ID zapisu`+`ID grupy`; `historia.csv` zostaje w repo jako archiwum (bez kolumn placówki — obecny potok na nim nie ruszy). Efekt: przesunięcie domyślnego zakresu dat (07.10 → 25.10), +227 zapisów, 153 statusy przesterowane między snapshotami.
- **Wymiar placówki — `dim_placowka`** (`05_dim_placowka.sql`): `id_placowki BIGINT PK`, `nazwa_wyswietlana`, `typ ∈ {realna, techniczna, bez_nazwy}`; budowana z distinct (id, nazwa) z CAŁEGO stagingu (też wiersze HISTORYCZNY). Normalizacja displayowa to jawna mapa CASE (`Poznan CJO→Poznań CJO`, `Wroclaw CJO→Wrocław CJO`, pusta nazwa→„(bez nazwy)"). Rozłączenie realnych od reszty mieszka w atrybucie `typ` — dashboard nie trzyma twardej listy ID. `techniczna` = 206 „X-Baza Testowa" (byt testowy), `bez_nazwy` = 200; 222 „Dział Sprzedaży" to **realna** placówka (rewizja 2026‑10‑31, czwarta tura — wcześniej błędnie klasyfikowana jako techniczna).
- **Placówka w faktach i martach**: staging dziedziczy `id_placowki` i `placowka_z_zapisu` (`nullif ''`); `fakty_zapisy` zyskuje `id_placowki` (dedup wielo-grup bez zmian — zweryfikowane w danych: 1 zapis = 1 placówka); oba marty mają `id_placowki` + `placowka` przez JOIN do `dim_placowka` z filtrem `typ='realna'` — byty testowe (206, 200) i wiersze bez placówki odpadają z marta (kubełek „(brak placówki)" zlikwidowany). Fakty i wymiar zostają kompletne do audytu; filtrowanie mieszka w warstwie marta.
- **Asercje placówek (fail-fast, jak słownik statusów)**: runner przed `03_transform` sprawdza, że pod jednym ID nie ma dwóch nazw źródłowych (pusta nazwa liczy się jako odrębna wartość), a po zbudowaniu `dim_placowka` — pełne pokrycie ID ze stagingu i unikalność nazw displayowych (filtr dashboarda idzie po etykiecie — duplikat grozi silent merge); naruszenie ⇒ nonzero exit PRZED materializacją faktów. (Asercja rezerwacji nazwy „(brak placówki)" usunięta — kubełek nie istnieje.)
- **Fragmentator placówki (Przegląd)**: globalny `multiselect("Placówka")` w sidebarze (lista `typ='realna'` z wymiaru, domyślnie wszystkie) — dołożony warunek `placowka IN (...)` do wspólnego zapytania `przeplyw`; zero nowych elementów wykresów, żadnych facetów. Dotyczy tylko Przeglądu.
- **Zakładka „Porównanie placówek"**: `st.tabs("Przegląd", "Porównanie placówek")`; selektory A/B tylko z `typ='realna'`, rozłączność przez odebranie A z listy B (kolizja ⇒ B przesuwa się na default liczony bez A); domyślna para = top‑2 realnych placówek po liczbie zapisów wyliczana z bazy z fallbackiem; kategorie w porównaniu stałe = wszystkie 4 (filtr kategorii NIE obowiązuje), zakres dat z sidebaru OWSZEM; metryka A vs B (liczba zapisów łącznie), grupowany bar kategoria × placówka (paleta `set2` spójna z plea), tabela liczba / % udziału wewn. / różnica (A−B). Puste stany → `st.info` w danej zakładce, bez `st.stop()` całej aplikacji (Przegląd żyje dalej).
- **Typowanie**: staging wszystko `VARCHAR`; w transformacji `try_strptime` z formatem `%d.%m.%Y %H:%M` (DuckDB `%d` akceptuje brak zera wiodącego; `try_` → null zamiast wyjątku). Separator `;`, polskie nazwy kolumn mapowane na snake_case.
- **Słownik statusów**: `dim_status` z kolumną `kategoria_biznesowa` ∈ {aktywny, zapisany, rezygnacja, tylko_filter}; etykieta UI dla `REZYGNACJA` to „REZYGNACJA" — jednolicie wielkimi literami jak `AKTYWNY`/`ZAPISANY` (poprawka 2026‑10‑31; wcześniej „Rezygnacja"). Asercja: dowolny status spoza znanej listy 9 wartości ⇒ błąd potoku i nonzero exit (fail-fast).
- **Materiały L2**: tabela liczebności per kategoria/`status` oraz tabela dziennych nowych zapisów (`date_trunc('day', utworzono)`, distinct po ID zapisu) z wymiarami do filtrowania (status, etap, kategoria). `mart_liczby_statusu` pozostaje materiałem hurtowni, ale dashboard liczy KPI i pie z `mart_nowe_zapisy_dziennie`.
- **Dashboard**: KPI cards („Wszystkie zapisy" + jedna karta na wybraną kategorię), piechart kategorii z plastrem „INNE" i wykres liniowy przepływu dziennego (flow, nie stock) — wszystkie trzy liczone z `mart_nowe_zapisy_dziennie` dla tej samej filtracji: zakres dat + wielokrotny wybór „Kategoria statusu" w kolejności lejka: `AKTYWNY`, `ZAPISANY`, `REZYGNACJA`, `INNE` (filtrowanie po `kategoria_biznesowa`, nie po surowych statusach). Grupowanie INNE (= `tylko_filter`) zostaje po stronie dashboardu. Złego/pustego stanu nie pokazujemy wykresów — jeden komunikat: „Wybierz zakres dat oraz co najmniej jedną kategorię statusu." lub „Brak zapisów dla wybranych filtrów." Filtr po etapie zapisu usunięty z dashboardu (2026‑10‑31); kolumna `etap_zapisu` pozostaje w modelach i marcie.
- **Weryfikacja jakościowa wykonana na surowym pliku**: historia.csv: 4602 wiersze, 4587 unikalnych ID zapisu, 11 duplikatów (wyłącznie multi-grupa, pola biznesowe identyczne), 9 statusów, rozkład: HISTORYCZNY 3268, REZYGNACJA 423, ZAPISANY 332, AKTYWNY 258, reszta niszowa. zapisy_grupy_placowka.csv: 4832 wiersze, poza kolumnami placówki ten sam klucz; 32 niepuste ID placówki (29 realnych + 1 testowa + 1 bez nazwy — po czwartej turze „Dział Sprzedaży" wliczona w realne), 3167 wierszy bez placówki (spod transformacji — ~3166 to HISTORYCZNY); nazwy powtarzalne bez diacritików („Poznan CJO", „Wroclaw CJO") pod jednym ID — spójnie.
- **Prior art**: notebook `transform.ipynb` zawiera działający eksploracyjny unpivot zdarzeń i listę statusów — jego logikę przenoszą skrypty SQL (to nie jest część docelowego potoku).

## Testing Decisions

- **Szew (seam) testów — jeden, najwyższy**: runner potoku `run_pipeline.py`. Testy odpalają pełny potok na kontrolowanym pliku fixture (mały CSV reprezentujący przypadki brzegowe) i asercjonują zawartość tabel wynikowych w DuckDB (ziarno, liczby, słownik). Dashboard poza smoke-testem uruchamialności ma testy AppTest liczb na kartach KPI w domyślnym stanie filtrów oraz reakcji KPI na filtr kategorii i zakres dat (w tym stany puste) — jego logika sprowadza się do zapytań o już przetestowane materiały.
- Dobre testy sprawdzają zachowanie zewnętrzne: liczby w `fakty_zapisy`/`zdarzeniach`/martach, porządek zdarzeń, odrzucenie `HISTORYCZNY`, wynik dedupu — nie strukturę plików SQL ani kolejność komend wewnątrz runera.
- **Przypadki obowiązkowe**: zapis w wielu grupach → jeden wiersz fakt i jedno zestawione zdarzenia; puste daty aktywności → brak wiersza zdarzenia; data jednocyfrowa (`1.04.2026`) → poprawny timestamp; nieznany status w fixture → nonzero exit z czyelnym komunikatem; miary liczone distinct po ID zapisu; LangLion przed `Utworzono` (skew znany w danych) → oba zdarzenia istnieją niezależnie. Od trzeciej rundy: `dim_placowka` (normalizacja nazw, typy, pokrycie ID), obecność `id_placowki` w faktach/martach, konflikt nazw pod jednym ID → fail-fast; dashboard: filtr placówki przelicza KPI w miejscu (struktura bez zmian), porównanie renderuje domyślną top‑2 realnych, zmiana pary przelicza wykres/tabelę, kolizja A=B obsłużona przesunięciem, pusty zakres dat → `st.info` bez stopowania drugiej zakładki. Od czwartej rundy: marty i dashboard tylko z `typ='realna'` — asercje nieobecności bytu testowego/bez nazwy/bez placówki w martach i na dashboardzie (fixture z aktywnymi „brudnymi" wierszami jako proof).
- Prior art w kodbase nie istnieje (brak testów) — zestaw testów pytest + tymczasowa baza DuckDB będzie pierwszym wzorcem.

## Out of Scope

- Inkrementalne ładowanie, detekcja zmian pliku, harmonogram/cron i orkiestracja.
- Wdrożenie produkcyjne (hosting dashboardu, autoryzacja, wielouszownikowość).
- Tabela asocjacyjna zapis–grupa i miary per grupa (np. zapełnienie grup).
- Miary konwersji/SLA lejka (czasy przejść AKTYWNY→ZAPISANY) — model `zdarzenia` je enables, ale poza zakresem tej wersji.
- Zmiana/naprawa danych w systemie źródłowym (numery grup „od 1", puste `ID grupy`, skew dat) — potok tylko rejestruje i obsługuje konsekwencje.
- Migracja notebooka eksploracyjnego do potoku (zostaje jako materiał badawczy).

## Further Notes

- Ryzyko otwarte: konsument dashboardu może mylić „zarejestrowano" z „zaimportowano" — jeśli po odrzuceniu HISTORYCZNY pozostaną duże klastry identycznych znaczników czasowych, rozważyć adnotację na wykresie.
- Decyzje, co do których rozmówca nie doprecyzował, a plan przyjął założenie (potwierdzone przy implementacji): piechart = kategorie + „INNE"; `Utworzono zapis w Fenku` traktowane jako aktywność w `zdarzeniach`; drilldown zdarzeń na dashboardzie = poza zakresem v1 (materiał tylko w bazie).
- Rewizja 2026‑10‑31 (implementacja drugiej tury): usunięto filtr „Etap zapisu" z dashboardu (story o filtrowaniu po etapie skreślona bez zamiennika — decyzja użytkownika), filtr „Status zapisu" zastąpiono filtrem „Kategoria statusu", KPI i pie uczyniono filtrowalnymi (liczone z `mart_nowe_zapisy_dziennie`), ujednolicono etykietę `REZYGNACJA` w słowniku.
- Rewizja 2026‑10‑31 (trzecia tura, stories 26–30): nowe źródło domyślne `zapisy_grupy_placowka.csv`; wymiar `dim_placowka` z typami i normalizacją nazw; placówka w faktach i martach z kubełkiem „(brak placówki)"; fragmentator placówki w Przeglądzie; zakładka „Porównanie placówek" (A/B rozłączne, top‑2 realnych jako default, 4 kategorie stałe, zakres dat wspólny, puste stany bez stopowania aplikacji).
- Rewizja 2026‑10‑31 (czwarta tura): dane testowe bez odniesienia w dashboardzie — marty i filtrowanie wyłącznie z `typ='realna'` (206 „X-Baza Testowa", 200 „(bez nazwy)" i wiersze bez placówki odpadają z marta; fakty i wymiar zostają kompletne); „Dział Sprzedaży" (222) awansowana z `techniczna` na `realna` (widoczna w Przeglądzie i Porównaniu); kubełek „(brak placówki)" zlikwidowany razem z jego asercją rezerwacji nazwy i stałą w dashboardzie.
- Dokument powstał z syntezy rozmowy planistycznej; dane liczbowe zweryfikowane bezpośrednio w pliku źródłowym 31.10.2026.
