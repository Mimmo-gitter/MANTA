CREATE EXTENSION IF NOT EXISTS vector;

-- Dedicated database for Keycloak identity provider (isolated from manta_db)
SELECT 'CREATE DATABASE keycloak'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'keycloak')\gexec

