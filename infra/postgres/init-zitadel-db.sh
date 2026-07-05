#!/bin/bash
# Creates the zitadel database on first postgres start.
# Runs automatically via docker-entrypoint-initdb.d/

set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE zitadel OWNER $POSTGRES_USER;
EOSQL

echo "Created zitadel database."
