-- Runs once, the first time the Postgres container starts with an empty volume.
-- A separate database for tests so `pytest` never touches dev data.
CREATE DATABASE revive_test;
