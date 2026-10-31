-- Warstwa marta: zmaterializowane tabele konsumowane przez dashboard (read-only).

-- Liczebności per kategoria biznesowa i status (ziarno metryki = distinct ID zapisu).
CREATE OR REPLACE TABLE mart_liczby_statusu AS
SELECT
    d.kategoria_biznesowa,
    d.etykieta_ui,
    f.status_zapisu,
    count(DISTINCT f.id_zapisu) AS liczba_zapisow
FROM fakty_zapisy f
JOIN dim_status d ON d.status_zapisu = f.status_zapisu
GROUP BY 1, 2, 3;

-- Dzienny napływ nowo utworzonych zapisów (flow, nie stock) — miara czasu to
-- „Utworzono zapis w Fenku", wymiary do filtrowania: status, etap, kategoria.
CREATE OR REPLACE TABLE mart_nowe_zapisy_dziennie AS
SELECT
    CAST(date_trunc('day', z.czas_wystapienia) AS DATE) AS data_utworzenia,
    f.status_zapisu,
    f.etap_zapisu,
    d.kategoria_biznesowa,
    count(DISTINCT z.id_zapisu) AS liczba_nowych_zapisow
FROM zdarzenia z
JOIN fakty_zapisy f USING (id_zapisu)
JOIN dim_status d ON d.status_zapisu = f.status_zapisu
WHERE z.aktywnosc = 'Utworzono zapis w Fenku'
GROUP BY 1, 2, 3, 4;
