#!/usr/bin/env bash
# Creates or rotates the Postgres role `getonbrd_scraper` and writes its DSN to the file given as the only argument
# (the operator passes platform/.tools/sinks/scraping-jobs/dsn). Idempotent: every run sets a new random password,
# re-asserts the grants and the pg_hba rules. The password never reaches stdout or argv: it goes through stdin and
# printf (a shell builtin).
#   scripts/visualizer/create_scraper_role.sh <dsn file>
# Needs the db up with the override: docker compose -f docker-compose.yml -f docker-compose.visualizer.yml up -d db
set -euo pipefail

readonly ROLE=getonbrd_scraper
readonly DB_NAME=scraping_masive
readonly DB_HOST=172.19.250.50
readonly DB_PORT=5432
readonly CLUSTER_SUBNET=172.19.0.0/16
readonly HBA_MARK='# visualizer-managed'

[[ $# == 1 && "$1" != -* ]] || { echo "usage: $0 <dsn file>" >&2; exit 2; }
# Resolved before the cd below, so a relative path means relative to where the operator is. Never inside the backend
# repo: the file holds a password, and a stray `git add` would commit it.
dsn_file="$(realpath -m -- "$1")"
backend="$(cd "$(dirname "$0")/../.." && pwd -P)"
[[ "$dsn_file" != "$backend"/* ]] || { echo "refusing to write the DSN inside $backend (it holds a password)" >&2; exit 2; }

cd "$backend"
compose() { docker compose -f docker-compose.yml -f docker-compose.visualizer.yml "$@"; }
psql_admin() { compose exec -T db psql -X -q -v ON_ERROR_STOP=1 -U postgres -d "$DB_NAME" "$@"; }

psql_admin -tAc 'SELECT 1' >/dev/null || { echo "db is not up (see the header)" >&2; exit 1; }
# Started without the override, the DSN below would point at nothing.
attached="$(docker inspect --format '{{with index .NetworkSettings.Networks "visualizer-net"}}{{.IPAddress}}{{end}}' \
  "$(compose ps -q db)")"
[[ "$attached" == "$DB_HOST" ]] \
  || { echo "db is not on visualizer-net at $DB_HOST: start it with the docker-compose.visualizer.yml override" >&2; exit 1; }

password="$(openssl rand -hex 32)"  # [0-9a-f]: safe inside a SQL literal and a URL

# One transaction (-1): a failure leaves the role as it was. If the ALTER fails, the server log may show the statement,
# password included; the log stays on this host.
{
  cat <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$ROLE') THEN CREATE ROLE $ROLE LOGIN; END IF;
END \$\$;
ALTER ROLE $ROLE WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '$password';
SQL
  cat scripts/visualizer/scraper_role_grants.sql
} | psql_admin -1

# The role may only come from the cluster's network. The rules go first (first match wins) and are tagged, so a rerun
# replaces them. The data directory persists, so initdb scripts cannot do this.
hba_file="$(psql_admin -tAc 'SHOW hba_file')"
compose exec -T db sh -s "$hba_file" "$HBA_MARK" "$ROLE" "$DB_NAME" "$CLUSTER_SUBNET" <<'IN_CONTAINER'
set -eu
file="$1" mark="$2" role="$3" db="$4" subnet="$5"
tmp="$(mktemp)"
{
  printf 'host %s %s %s scram-sha-256 %s\n' "$db" "$role" "$subnet" "$mark"
  printf 'host all %s all reject %s\n' "$role" "$mark"
  printf 'local all %s reject %s\n' "$role" "$mark"
  grep -vF "$mark" "$file"
} >"$tmp"
cat "$tmp" >"$file"  # keeps the file's owner and mode
rm -f "$tmp"
IN_CONTAINER
# pg_hba_file_rules reads the file on disk, not the loaded config: validate the syntax before the reload applies it.
bad="$(psql_admin -tAc 'SELECT count(*) FROM pg_hba_file_rules WHERE error IS NOT NULL')"
[[ "$bad" == 0 ]] || { echo "pg_hba.conf has $bad rule errors" >&2; exit 1; }
psql_admin -c 'SELECT pg_reload_conf()' >/dev/null

umask 077
mkdir -p "$(dirname "$dsn_file")"
tmp="$(mktemp "$dsn_file.XXXXXX")"
printf 'postgresql://%s:%s@%s:%d/%s\n' "$ROLE" "$password" "$DB_HOST" "$DB_PORT" "$DB_NAME" >"$tmp"
mv -f "$tmp" "$dsn_file"
echo "role $ROLE ready; DSN written to $dsn_file (mode 0600)"
