-- Wymiar placówek: distinct (ID, nazwa) z całego stagingu (też z wierszy
-- HISTORYCZNYCH — wymiar opisuje źródło, nie fakty po filtrach).
--   * nazwa_wyswietlana: normalizacja diacrityków + kubełek „(bez nazwy)" dla ID
--     bez nazwy źródłowej (np. 200); mapa jawna CASE — jedyne miejsce, gdzie
--     literówki nazw z systemu są poprawiane;
--   * typ: 'techniczna' = byt testowy wykluczony z dashboardu (206 X-Baza
--     Testowa), 'bez_nazwy' = ID bez nazwy (200), 'realna' = reszta — w tym
--     222 Dział Sprzedaży (awansowany: realna placówka, ma odniesienie na
--     dashboardzie). Rozłączenie realnych od reszty mieszka tutaj,
--     dashboard nie trzyma twardej listy ID.
-- Asercje spójności (jedna nazwa na ID, pełne pokrycie stagingu) uruchamia
-- runner PRZED materializacją faktów.

CREATE OR REPLACE TABLE dim_placowka (
    id_placowki       BIGINT PRIMARY KEY,
    nazwa_wyswietlana VARCHAR NOT NULL,
    typ               VARCHAR NOT NULL CHECK (typ IN ('realna', 'techniczna', 'bez_nazwy'))
);

INSERT INTO dim_placowka
SELECT
    try_cast(id_placowki AS BIGINT) AS id_placowki,
    CASE
        WHEN placowka_z_zapisu IS NULL   THEN '(bez nazwy)'
        WHEN placowka_z_zapisu = 'Poznan CJO'   THEN 'Poznań CJO'
        WHEN placowka_z_zapisu = 'Wroclaw CJO'  THEN 'Wrocław CJO'
        ELSE placowka_z_zapisu
    END AS nazwa_wyswietlana,
    CASE
        WHEN placowka_z_zapisu IS NULL   THEN 'bez_nazwy'
        WHEN id_placowki = '206' THEN 'techniczna'
        ELSE 'realna'
    END AS typ
FROM (
    SELECT DISTINCT id_placowki, placowka_z_zapisu
    FROM staging_zapisy
    WHERE id_placowki IS NOT NULL
);
