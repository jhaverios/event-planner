#!/bin/bash
# Runs once, on first boot of an empty postgres volume.
# Creates one role + database per application so they cannot read each other's tables.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
  CREATE ROLE pretix LOGIN PASSWORD '${PRETIX_DB_PASSWORD}';
  CREATE DATABASE pretix OWNER pretix ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0;

  CREATE ROLE n8n LOGIN PASSWORD '${N8N_DB_PASSWORD}';
  CREATE DATABASE n8n OWNER n8n ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0;
SQL

echo "init-databases.sh: created pretix and n8n databases"
