-- The PostgreSQL entrypoint creates POSTGRES_DB, which is the development
-- database. The test suite needs a separate database whose name ends in _test.
-- The guard in the backend tests refuses any other name.
SELECT 'CREATE DATABASE ticketplus_test'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'ticketplus_test')\gexec
