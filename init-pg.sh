#!/bin/bash
set -e

# Create databases
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE algovibe_live;
    CREATE DATABASE algovibe_dryrun;
    CREATE DATABASE algovibe;
EOSQL

# Enable TimescaleDB extension on all databases
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "algovibe_live"   -c "CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "algovibe_dryrun" -c "CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "algovibe"        -c "CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;"
