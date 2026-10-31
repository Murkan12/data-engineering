-- Warstwa marta: zmaterializowane tabele konsumowane przez dashboard (read-only).
-- Filtr typ='realna' (join do wymiaru placówki) odrzuca byt testowy (206 X-Baza
-- Testowa), placówkę bez nazwy (200) i wiersze bez placówki — dashboard operuje
-- wyłącznie na realnych placówkach; fakty i wymiar zostają kompletne do audytu.

-- Liczebności per kategoria biznesowa i status (ziarno metryki = distinct ID zapisu),
-- z wymiarem placówki (tylko realne).
CREATE OR REPLACE TABLE mart_liczby_statusu AS
SELECT
    d.kategoria_biznesowa,
    d.etykieta_ui,
    f.status_zapisu,
    f.id_placowki,
    p.nazwa_wyswietlana AS placowka,
    count(DISTINCT f.id_zapisu) AS liczba_zapisow
FROM fakty_zapisy f
JOIN dim_status d ON d.status_zapisu = f.status_zapisu
JOIN dim_placowka p ON p.id_placowki = f.id_placowki AND p.typ = 'realna'
GROUP BY 1, 2, 3, 4, 5;

-- Dzienny napływ nowo utworzonych zapisów (flow, nie stock) — miara czasu to
-- „Utworzono zapis w Fenku", wymiary do filtrowania: status, etap, kategoria, placówka.
CREATE OR REPLACE TABLE mart_nowe_zapisy_dziennie AS
SELECT
    CAST(date_trunc('day', z.czas_wystapienia) AS DATE) AS data_utworzenia,
    f.status_zapisu,
    f.etap_zapisu,
    d.kategoria_biznesowa,
    f.id_placowki,
    p.nazwa_wyswietlana AS placowka,
    count(DISTINCT z.id_zapisu) AS liczba_nowych_zapisow
FROM zdarzenia z
JOIN fakty_zapisy f USING (id_zapisu)
JOIN dim_status d ON d.status_zapisu = f.status_zapisu
JOIN dim_placowka p ON p.id_placowki = f.id_placowki AND p.typ = 'realna'
WHERE z.aktywnosc = 'Utworzono zapis w Fenku'
GROUP BY 1, 2, 3, 4, 5, 6;
