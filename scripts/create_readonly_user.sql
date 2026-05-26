-- =============================================================================
-- Kreiranje read-only DB usera za izvršavanje LLM-generiranih SQL upita.
-- =============================================================================
--
-- Ovaj user ima samo `SELECT` privilegije nad public schemom. Backend ga
-- koristi za sve upite koje generira LLM — čak i u (hipotetskom) slučaju da
-- bi prošla zlonamjerna naredba kroz validation pipeline, baza fizički
-- ne može izvršiti UPDATE / DELETE / DROP.
--
-- Ovo je glavni "defense-in-depth" sloj na razini baze.
--
-- Skripta se izvršava automatski pri prvom pokretanju postgres kontejnera
-- (vidi docker-compose.yml -> volumes -> /docker-entrypoint-initdb.d/).
--
-- Svaka init skripta dobiva NOVU psql sesiju spojenu na POSTGRES_DB
-- (=`postgres`). GRANT-i moraju vrijediti za `chinook` bazu, zato
-- eksplicitno prebacujemo konekciju prije svih GRANT statementa.

\c chinook;

-- CREATE ROLE je globalan (vrijedi u svim bazama), GRANT statementi se
-- odnose na trenutno aktivnu bazu — što je sada chinook.
CREATE ROLE nl2sql_readonly WITH LOGIN PASSWORD 'readonly';

-- Dozvola spajanja na bazu chinook.
GRANT CONNECT ON DATABASE chinook TO nl2sql_readonly;

-- Dozvola korištenja public scheme (ne kreiranja u njoj).
GRANT USAGE ON SCHEMA public TO nl2sql_readonly;

-- SELECT nad svim postojećim tablicama public sheme.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO nl2sql_readonly;

-- Default privileges — automatski daje SELECT na BUDUĆE tablice. Korisno
-- ako se shema kasnije promijeni (npr. dodaju benchmark tablice).
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO nl2sql_readonly;

-- Eksplicitno nemamo nikakav INSERT/UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER
-- grant — read-only znači STVARNO read-only.
