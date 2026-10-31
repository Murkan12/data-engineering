-- Warstwa transform: znormalizowany model dwutabelowy.
--
-- fakty_zapisy: jeden wiersz na zapis (ziarno = id_zapisu),
-- kolumny źródłowe od "ID zapisu" do "Stan grupy".
--   * odrzucamy status HISTORYCZNY (artefakt techniczny/migracyjny),
--   * deduplikujemy zapisy wielo-grupowe (duplikaty różnią się tylko ID grupy).

CREATE OR REPLACE TABLE fakty_zapisy AS
SELECT
    try_cast(id_zapisu AS BIGINT)  AS id_zapisu,
    try_cast(id_placowki AS BIGINT) AS id_placowki,
    nullif(status_zapisu, '')      AS status_zapisu,
    nullif(etap_zapisu, '')        AS etap_zapisu,
    nullif(id_grupy, '')           AS id_grupy,
    nullif(id_uslugi, '')          AS id_uslugi,
    nullif(rodzaj_grupy, '')       AS rodzaj_grupy,
    nullif(okres_grupy, '')        AS okres_grupy,
    nullif(grupa_od, '')           AS grupa_od,
    nullif(grupa_do, '')           AS grupa_do,
    nullif(stan_grupy, '')         AS stan_grupy
FROM staging_zapisy
WHERE nullif(status_zapisu, '') <> 'HISTORYCZNY'
QUALIFY row_number() OVER (
    PARTITION BY try_cast(id_zapisu AS BIGINT)
    ORDER BY nullif(id_grupy, '')
) = 1;

-- zdarzenia: unpivot wszystkich kolumn aktywności (w tym „Utworzono zapis w Fenku")
-- do schematu id_zapisu / aktywnosc / czas_wystapienia.
--   * tylko wartości niepuste i sparsowane (pusta aktywność = brak wiersza),
--   * polskie daty „D.M.YYYY H:MM" przez try_strptime (null zamiast wyjątku),
--   * dedup po trójce kluczy — zapisy wielo-grupowe nie mnożą historii,
--   * „Źródło potwierdzenia umowy/nauczyciela" jako atrybuty właściwych zdarzeń.

CREATE OR REPLACE TABLE zdarzenia AS
SELECT
    id_zapisu,
    aktywnosc,
    czas_wystapienia,
    max(zrodlo_potwierdzenia) AS zrodlo_potwierdzenia
FROM (
    SELECT
        try_cast(id_zapisu AS BIGINT) AS id_zapisu,
        unnest([
            'Utworzono zapis w Fenku',
            'Data zapisu w LangLionie',
            'Przekazano do DOK',
            'Kliknięto przesłano umowę',
            'Nauczyciel potwierdził realizację'
        ]) AS aktywnosc,
        unnest([
            try_strptime(nullif(utworzono_zapis_fenku, ''), '%d.%m.%Y %H:%M'),
            try_strptime(nullif(data_zapisu_langlionie, ''), '%d.%m.%Y %H:%M'),
            try_strptime(nullif(przekazano_do_dok, ''), '%d.%m.%Y %H:%M'),
            try_strptime(nullif(kliknieto_przeslano_umowe, ''), '%d.%m.%Y %H:%M'),
            try_strptime(nullif(nauczyciel_potwierdzil_realizacje, ''), '%d.%m.%Y %H:%M')
        ]) AS czas_wystapienia,
        unnest([
            NULL::VARCHAR,
            NULL::VARCHAR,
            NULL::VARCHAR,
            nullif(zrodlo_potwierdzenia_umowy, ''),
            nullif(zrodlo_potwierdzenia_nauczyciela, '')
        ]) AS zrodlo_potwierdzenia
    FROM staging_zapisy
    WHERE nullif(status_zapisu, '') <> 'HISTORYCZNY'
)
WHERE czas_wystapienia IS NOT NULL
GROUP BY 1, 2, 3;
