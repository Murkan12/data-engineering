-- Warstwa staging: surowy CSV semicolonowy, wszystko VARCHAR,
-- żeby import nigdy nie odrzucił wierszy (decyzja typowania ze spec).
-- Nazwy kolumn źródłowych (polskie) mapowane są na snake_case.
-- Runner ustawia ścieżkę pliku przez `SET VARIABLE csv_path`.

CREATE OR REPLACE TABLE staging_zapisy AS
SELECT
    "ID zapisu"                         AS id_zapisu,
    "Status zapisu"                     AS status_zapisu,
    "Etap zapisu"                       AS etap_zapisu,
    "ID grupy"                          AS id_grupy,
    "ID usługi"                         AS id_uslugi,
    "Rodzaj grupy"                      AS rodzaj_grupy,
    "Okres grupy"                       AS okres_grupy,
    "Grupa od"                          AS grupa_od,
    "Grupa do"                          AS grupa_do,
    "Stan grupy"                        AS stan_grupy,
    "Utworzono zapis w Fenku"           AS utworzono_zapis_fenku,
    "Data zapisu w LangLionie"          AS data_zapisu_langlionie,
    "Przekazano do DOK"                 AS przekazano_do_dok,
    "Kliknięto przesłano umowę"         AS kliknieto_przeslano_umowe,
    "Nauczyciel potwierdził realizację" AS nauczyciel_potwierdzil_realizacje,
    "Źródło potwierdzenia umowy"        AS zrodlo_potwierdzenia_umowy,
    "Źródło potwierdzenia nauczyciela"  AS zrodlo_potwierdzenia_nauczyciela,
    nullif("ID placówki (z zapisu)", '')   AS id_placowki,
    nullif("Placówka (z zapisu)", '')       AS placowka_z_zapisu
FROM read_csv(
    getvariable('csv_path'),
    delim = ';',
    header = true,
    all_varchar = true,
    quote = '"',
    escape = '"',
    encoding = 'utf-8',
);
