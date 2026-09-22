#!/usr/bin/env bash
# One-command deploy for the event portal. Safe to re-run; it never overwrites
# an existing .env or pretix.cfg, so secrets survive upgrades.
#
#   sudo bash scripts/deploy-jprod.sh
#
# Override any prompt by exporting it first, e.g.
#   PRETIX_DOMAIN=events.jslwealth.in SMTP_USER=events@jslwealth.in \
#   bash scripts/deploy-jprod.sh

set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)
DEPLOY=deploy
# Absolute, because the script changes directory into $DEPLOY partway through
# and a relative path silently becomes deploy/deploy/.env after that.
ENVFILE="$ROOT/$DEPLOY/.env"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
warn() { printf '\033[33m!! %s\033[0m\n' "$1"; }
die()  { printf '\033[31mXX %s\033[0m\n' "$1" >&2; exit 1; }
ask()  { # ask VAR "prompt" "default"
  local v=$1 p=$2 d=${3:-}
  if [ -n "${!v:-}" ]; then return; fi
  if [ -t 0 ]; then read -r -p "$p${d:+ [$d]}: " REPLY || true; else REPLY=""; fi
  printf -v "$v" '%s' "${REPLY:-$d}"
  [ -n "${!v}" ] || die "$v is required"
}

say "Checking prerequisites"
command -v docker >/dev/null || die "Docker is not installed. Install Docker Engine and the compose plugin first."
docker compose version >/dev/null 2>&1 || die "The docker compose plugin is missing."
docker info >/dev/null 2>&1 || die "Cannot talk to the Docker daemon. Are you root, or in the docker group?"
echo "docker $(docker --version | awk '{print $3}' | tr -d ,), compose $(docker compose version --short)"

# --- Configuration -------------------------------------------------------
if [ ! -f "$ENVFILE" ]; then
  say "First run: collecting configuration"
  # Two names because the portal and pretix both answer on /api and cannot
  # share a hostname. n8n gets no name at all: it holds every API credential
  # and only an administrator ever needs it, so it stays on loopback behind
  # an SSH tunnel.
  ask PORTAL_DOMAIN "Domain brokers and door staff open" "events.jslwealth.in"
  ask PRETIX_DOMAIN "Domain for pretix and pretixSCAN" "tickets.jslwealth.in"
  ask ACME_EMAIL    "Email for TLS certificate notices"
  # ZeptoMail's username is the literal string "emailapikey", which is NOT the
  # From address. Keep the two separate or pretix sends as the wrong sender.
  ask SMTP_HOST     "SMTP host" "smtp.zeptomail.in"
  ask SMTP_PORT     "SMTP port" "587"
  ask SMTP_USER     "SMTP username" "emailapikey"
  ask SMTP_PASSWORD "SMTP password (ZeptoMail send-mail token)"
  ask SMTP_FROM     "From address (must be a verified sender in ZeptoMail)" "events@jslwealth.in"

  gen() { openssl rand -hex "${1:-16}"; }
  PRETIX_DB_PASSWORD=$(gen 16)
  cat > "$ENVFILE" <<EOF
PRETIX_DOMAIN=$PRETIX_DOMAIN
PORTAL_DOMAIN=$PORTAL_DOMAIN
PORTAL_BASE=https://$PORTAL_DOMAIN
N8N_DOMAIN=${N8N_DOMAIN:-flow.invalid}
ACME_EMAIL=$ACME_EMAIL
POSTGRES_SUPERUSER=postgres
POSTGRES_SUPERUSER_PASSWORD=$(gen 16)
PRETIX_DB_PASSWORD=$PRETIX_DB_PASSWORD
N8N_DB_PASSWORD=$(gen 16)
N8N_ENCRYPTION_KEY=$(gen 32)
N8N_PROXY_HOPS=1
N8N_EXECUTION_RETENTION_HOURS=720
N8N_SAVE_ON_SUCCESS=all
TZ=Asia/Kolkata
PRETIX_CRON_INTERVAL=900
BROKER_LINK_SECRET=$(gen 32)
DIRECTORY_DB_PASSWORD=$(gen 24)
PRETIX_ADMIN_EMAIL=${PRETIX_ADMIN_EMAIL:-admin@$PRETIX_DOMAIN}
PRETIX_ADMIN_PASSWORD=$(gen 12)
PRETIX_ORGANIZER=${PRETIX_ORGANIZER:-jsl}
PRETIX_EVENT=${PRETIX_EVENT:-investor-events}
PRETIX_HOST=$PRETIX_DOMAIN
PRETIX_API_BASE=http://pretix:80/api/v1
# 5433 is already taken on this host by another stack's postgres.
POSTGRES_PORT=5434
PORTAL_PORT=8090
WATI_TEMPLATE=jsl_event_v3
QR_BASE='https://api.qrserver.com/v1/create-qr-code/?size=600x600&margin=20&data='
EOF
  chmod 600 "$ENVFILE"

  sed -e "s|^url=.*|url=https://$PRETIX_DOMAIN|" \
      -e "s|^password=CHANGEME|password=$PRETIX_DB_PASSWORD|" \
      -e "s|^from=.*|from=$SMTP_FROM|" \
      -e "s|^host=smtp.example.com|host=$SMTP_HOST|" \
      -e "s|^port=587|port=$SMTP_PORT|" \
      -e "s|^user=$|user=$SMTP_USER|" \
      -e "s|^password=$|password=$SMTP_PASSWORD|" \
      "$DEPLOY/pretix/pretix.cfg.example" > "$DEPLOY/pretix/pretix.cfg"
  # pretix runs as an unprivileged user inside the container (uid 15371). A
  # root-owned 0600 config is unreadable to it, and pretix does NOT error: it
  # silently falls back to built-in defaults, so mail stops working and the
  # database settings are ignored, with nothing in the log to say why.
  # Give the file to that uid so it stays secret AND readable.
  # On a first deploy the image is not pulled yet, so this lookup fails. With
  # set -e a failing command substitution takes the whole script down mid-way
  # through writing the configuration, which is how this failed the first time
  # it was run against an empty machine. Fall back to the known uid instead.
  PRETIX_UID=$(docker compose "${FILES[@]:-}" run --rm --no-deps --entrypoint id pretix -u 2>/dev/null | tr -d '\r\n' || true)
  PRETIX_UID=${PRETIX_UID:-15371}
  chown "$PRETIX_UID" "$DEPLOY/pretix/pretix.cfg" 2>/dev/null || \
    warn "could not chown pretix.cfg to uid $PRETIX_UID; pretix may ignore it"
  chmod 600 "$DEPLOY/pretix/pretix.cfg"
  echo "wrote $ENVFILE (0600 root) and $DEPLOY/pretix/pretix.cfg (0600, uid $PRETIX_UID)"
else
  say "Existing configuration found; leaving .env and pretix.cfg untouched"
fi

# --- Keys that cannot exist on a first run -------------------------------
# The pretix API token is created inside pretix, which does not exist yet the
# first time this runs. So rather than demanding everything up front, top up
# whatever is still missing on each run. Re-running after setting pretix up is
# the intended path, not a workaround.
top_up() { # top_up KEY "prompt" [optional]
  local k=$1 prompt=$2 optional=${3:-}
  if grep -qE "^$k=.+" "$ENVFILE"; then return; fi
  local val="${!k:-}"
  if [ -z "$val" ] && [ -t 0 ]; then
    read -r -p "$prompt${optional:+ (blank to skip)}: " val || true
  fi
  if [ -z "$val" ]; then
    [ -n "$optional" ] && { warn "$k left unset; the portal will report it as missing"; return; }
    die "$k is required"
  fi
  sed -i "/^$k=/d" "$ENVFILE"
  printf '%s=%s\n' "$k" "$val" >> "$ENVFILE"
  echo "recorded $k"
}
say "Checking the portal's configuration"
top_up PRETIX_API_TOKEN "pretix API token (create it in pretix: Team settings then API tokens)" optional
top_up WATI_API_BASE    "WATI API base, e.g. https://live-mt-server.wati.io/111557" optional
top_up WATI_TOKEN       "WATI API token" optional
top_up ZEPTO_SMTP_PASS  "ZeptoMail send-mail token" optional
top_up MAIL_FROM        "From address for the pass emails" optional
# shellcheck disable=SC1091
set -a; . "$ENVFILE"; set +a

# --- Keys an older .env predates ----------------------------------------
# .env is written once, on the first run, and preserved on every run after.
# So every key added to this script SINCE a given machine's first deploy is
# missing on that machine — silently, with no error anywhere. It has bitten
# twice on jprod: ADMIN_PASSWORD, so the admin page could not be signed into,
# and PRETIX_ADMIN_*, so nobody could log in to pretix to pair a door scanner.
#
# Backfill what can be derived or safely invented. Say plainly what cannot.
rnd() { openssl rand -hex "${1:-16}"; }
backfill() { # backfill KEY VALUE
  grep -qE "^$1=.+" "$ENVFILE" && return 0
  sed -i "/^$1=/d" "$ENVFILE"
  printf '%s=%s\n' "$1" "$2" >> "$ENVFILE"
  echo "backfilled $1"
}
backfill PRETIX_ADMIN_EMAIL "admin@${PRETIX_DOMAIN:-localhost}"
# Safe to invent: the portal reads it at startup and nothing else knows it.
backfill ADMIN_PASSWORD "$(rnd 8)"
# NOT safe to invent: it belongs to a user that already exists inside pretix,
# so writing a fresh value here would leave the file confidently wrong.
if ! grep -qE '^PRETIX_ADMIN_PASSWORD=.+' "$ENVFILE"; then
  warn "PRETIX_ADMIN_PASSWORD is missing and cannot be guessed — it belongs to a
       user inside pretix. Reset it with:  sudo scripts/reset-pretix-admin.sh"
fi
# shellcheck disable=SC1091
set -a; . "$ENVFILE"; set +a

# --- Pick the compose files ---------------------------------------------
FILES=(-f docker-compose.yml)
if [ ! -e /proc/net/if_inet6 ]; then
  warn "No IPv6 on this kernel. Adding the IPv4-only nginx overlay, or pretix will serve nothing."
  FILES+=(-f docker-compose.no-ipv6.yml)
fi

# --- Edge: our own Caddy, or an existing proxy? -------------------------
# Fail safe: only claim 80/443 when we can positively prove they are free.
# jprod already serves other jslwealth.in sites, and a Caddy that grabs those
# ports would take them down.
PROFILE=()
listeners() {
  if   command -v ss      >/dev/null 2>&1; then ss      -lnt 2>/dev/null | awk 'NR>1{print $4}'
  elif command -v netstat >/dev/null 2>&1; then netstat -lnt 2>/dev/null | awk 'NR>2{print $4}'
  else return 1
  fi
}
if ! PORTS=$(listeners); then
  warn "Neither ss nor netstat is available, so I cannot tell what owns 80/443."
  warn "Not starting Caddy. If those ports really are free, re-run with: EDGE=caddy"
elif printf '%s\n' "$PORTS" | grep -qE ':(80|443)$'; then
  warn "Something already listens on 80/443. Not starting Caddy."
  warn "Point that proxy at 127.0.0.1:8345 (events) and 127.0.0.1:5678 (automation)."
  warn "A ready-made nginx server block is in deploy/reverse-proxy/nginx-snippet.conf"
else
  echo "Nothing on 80/443. Starting Caddy for automatic TLS."
  EDGE=${EDGE:-caddy}
fi
[ "${EDGE:-}" = caddy ] && PROFILE=(--profile edge)

say "Starting the stack"
cd "$DEPLOY"
docker compose "${FILES[@]}" "${PROFILE[@]}" up -d --build

say "Broker and client directory"
# Idempotent: creating the database, the role and the schema can all be re-run.
for i in $(seq 1 30); do
  docker compose "${FILES[@]}" exec -T postgres pg_isready -U "$POSTGRES_SUPERUSER" >/dev/null 2>&1 && break
  [ "$i" = 30 ] && die "postgres did not become ready"
  sleep 2
done
if ! docker compose "${FILES[@]}" exec -T postgres \
     psql -U "$POSTGRES_SUPERUSER" -tAc "SELECT 1 FROM pg_database WHERE datname='directory'" | grep -q 1; then
  docker compose "${FILES[@]}" exec -T postgres createdb -U "$POSTGRES_SUPERUSER" directory
  echo "created the directory database"
fi
docker compose "${FILES[@]}" exec -T postgres \
  psql -U "$POSTGRES_SUPERUSER" -v ON_ERROR_STOP=1 -q <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='directory') THEN
    CREATE ROLE directory LOGIN PASSWORD '$DIRECTORY_DB_PASSWORD';
  ELSE
    ALTER ROLE directory PASSWORD '$DIRECTORY_DB_PASSWORD';
  END IF;
END \$\$;
GRANT CONNECT ON DATABASE directory TO directory;
SQL
docker compose "${FILES[@]}" exec -T postgres \
  psql -U "$POSTGRES_SUPERUSER" -d directory -v ON_ERROR_STOP=1 -q < postgres/directory-schema.sql
docker compose "${FILES[@]}" exec -T postgres \
  psql -U "$POSTGRES_SUPERUSER" -d directory -v ON_ERROR_STOP=1 -q <<'SQL'
GRANT USAGE ON SCHEMA public TO directory;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO directory;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO directory;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO directory;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO directory;
SQL
echo "directory schema applied"

say "Waiting for pretix"
for i in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $PRETIX_DOMAIN" http://127.0.0.1:8345/control/login || true)
  case "$code" in
    200|302) echo "pretix is serving (HTTP $code)"; break ;;
  esac
  [ "$i" = 60 ] && { docker compose "${FILES[@]}" logs --tail=40 pretix; die "pretix did not come up"; }
  sleep 5
done

# --- Set pretix up without anyone clicking through its admin ------------
# The portal needs an API token, and a token normally means four trips through
# the pretix UI. That would make this deploy wait on a person. These two do the
# same work, and both are idempotent, so a re-run is safe.
# A token inherited from the environment can be a stale one from an earlier
# install. Prove it authenticates before trusting it; otherwise bootstrap.
TOKEN_OK=no
if grep -qE '^PRETIX_API_TOKEN=.+' "$ENVFILE"; then
  set -a; . "$ENVFILE"; set +a
  code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Token $PRETIX_API_TOKEN" \
         -H "Host: $PRETIX_DOMAIN" "http://127.0.0.1:8345/api/v1/organizers/" || true)
  if [ "$code" = 200 ]; then TOKEN_OK=yes; else
    warn "the stored pretix token returns HTTP $code; replacing it"
    sed -i '/^PRETIX_API_TOKEN=/d' "$ENVFILE"
  fi
fi
if [ "$TOKEN_OK" != yes ]; then
  say "Setting pretix up"
  TOKEN_LINE=$(docker compose "${FILES[@]}" exec -T \
      -e PRETIX_ADMIN_EMAIL="$PRETIX_ADMIN_EMAIL" \
      -e PRETIX_ADMIN_PASSWORD="$PRETIX_ADMIN_PASSWORD" \
      -e PRETIX_ORGANIZER="$PRETIX_ORGANIZER" \
      pretix python - < "$ROOT/scripts/bootstrap-pretix.py" | tee /dev/stderr \
      | grep -E '^PRETIX_API_TOKEN=' | tail -1)
  [ -n "$TOKEN_LINE" ] || die "could not create a pretix API token"
  sed -i '/^PRETIX_API_TOKEN=/d' "$ENVFILE"
  printf '%s\n' "$TOKEN_LINE" >> "$ENVFILE"
  set -a; . "$ENVFILE"; set +a
  echo "recorded PRETIX_API_TOKEN"

  say "Creating the event series and the first event date"
  PRETIX_API_BASE="http://127.0.0.1:8345/api/v1" \
  PRETIX_HOST="$PRETIX_DOMAIN" \
  PRETIX_API_TOKEN="$PRETIX_API_TOKEN" \
  PRETIX_ORGANIZER="$PRETIX_ORGANIZER" \
  PRETIX_EVENT="$PRETIX_EVENT" \
  DIRECTORY_DSN="postgresql://directory:${DIRECTORY_DB_PASSWORD}@127.0.0.1:${POSTGRES_PORT:-5434}/directory" \
    python3 "$ROOT/scripts/bootstrap-event.py" || die "event setup failed"

  say "Restarting the portal with its token"
  docker compose "${FILES[@]}" "${PROFILE[@]}" up -d --build portal
fi

say "Waiting for the portal"
for i in $(seq 1 30); do
  body=$(curl -s --max-time 5 "http://127.0.0.1:${PORTAL_PORT:-8090}/healthz" || true)
  if [ -n "$body" ]; then echo "$body"; break; fi
  [ "$i" = 30 ] && { docker compose "${FILES[@]}" logs --tail=40 portal; die "the portal did not come up"; }
  sleep 3
done

say "Done"
# An .env written before the portal existed has no PORTAL_DOMAIN. Report what
# we do know rather than failing on the last line of a successful deploy.
PORTAL_DOMAIN=${PORTAL_DOMAIN:-"(not set - add PORTAL_DOMAIN to deploy/.env)"}
cat <<EOF
Portal            https://$PORTAL_DOMAIN/
Pretix control    https://$PRETIX_DOMAIN/control/
n8n               loopback only, reach it with:
                    ssh -N -L 5678:127.0.0.1:5678 $(id -un)@$(hostname)

Mint the links people actually use:
  cd $(pwd)/.. && python3 portal/issue-link.py desk  shared     --days 365
                  python3 portal/issue-link.py admin nimish     --days 90
                  python3 portal/issue-link.py door  entrance-1 --days 7

The pretix admin user, organizer, teams, API token, event series, questions,
quota and check-in list were all created by this script. The admin sign-in is
PRETIX_ADMIN_EMAIL / PRETIX_ADMIN_PASSWORD in $ENVFILE.
EOF
