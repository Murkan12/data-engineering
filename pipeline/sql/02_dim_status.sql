-- Słownik statusów z centralnym mapowaniem na kategorie biznesowe.
-- kategoria_biznesowa ∈ {aktywny, zapisany, rezygnacja, tylko_filter}
--   tylko_filter = statusy nienależące do 3 kategorii (m.in. duplikaty
--   ZDUBLOWANY_ZAPIS / ZAPIS_GRUPOWY oraz techniczny HISTORYCZNY).
-- Lista znanych statusów jest źródłem prawdy dla asercji fail-fast w runnerze.

CREATE OR REPLACE TABLE dim_status (
    status_zapisu       VARCHAR PRIMARY KEY,
    kategoria_biznesowa VARCHAR NOT NULL,
    etykieta_ui         VARCHAR NOT NULL
);

INSERT INTO dim_status VALUES
    ('AKTYWNY',                      'aktywny',      'AKTYWNY'),
    ('ZAPISANY',                     'zapisany',     'ZAPISANY'),
    ('REZYGNACJA',                   'rezygnacja',   'REZYGNACJA'),
    ('KONTAKT_NIGDY_NIE_OSIAGNIETY', 'tylko_filter', 'Kontakt nigdy nie osiągnięty'),
    ('NIEPRAWIDLOWE_DANE_KONTAKTOWE','tylko_filter', 'Nieprawidłowe dane kontaktowe'),
    ('UTRACONY_KONTAKT_30D',         'tylko_filter', 'Utracony kontakt 30 dni'),
    ('ZDUBLOWANY_ZAPIS',             'tylko_filter', 'Zdublowany zapis'),
    ('ZAPIS_GRUPOWY',                'tylko_filter', 'Zapis grupowy'),
    ('HISTORYCZNY',                  'tylko_filter', 'HISTORYCZNY (artefakt migracyjny)');
