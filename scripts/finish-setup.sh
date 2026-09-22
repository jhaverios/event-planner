#!/usr/bin/env bash
# Finish a deploy that has containers running but no event in pretix.
#
#   sudo bash scripts/finish-setup.sh
#
# Idempotent. Safe to re-run: every step checks before it creates.
#
# Written after jprod sat at /healthz "degraded" with a 403. The 403 did not
# mean the token lacked permission — pretix answers 403 for an event that does
# not exist, because it will not confirm or deny existence to a token. There
# was simply no event. Hence the verification below, which is careful to test
# only what it claims to.

set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)
ENVFILE="$ROOT/deploy/.env"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
warn() { printf '\033[33m!! %s\033[0m\n' "$1"; }
die()  { printf '\033[31mXX %s\033[0m\n' "$1" >&2; exit 1; }

[ -r "$ENVFILE" ] || die "cannot read $ENVFILE — run with sudo"
set -a; . "$ENVFILE"; set +a

cd "$ROOT/deploy"
FILES=(-f docker-compose.yml)
[ -e /proc/net/if_inet6 ] || FILES+=(-f docker-compose.no-ipv6.yml)
PORT=${PRETIX_PORT:-8345}
HOST=${PRETIX_HOST:-${PRETIX_DOMAIN:-localhost}}
ORG=${PRETIX_ORGANIZER:-jsl}
EV=${PRETIX_EVENT:-investor-events}
API="http://127.0.0.1:$PORT/api/v1"

# Does this token work? Listing an organizer's events answers that and nothing
# else. Asking for a specific event conflates "token is bad" with "event does
# not exist yet", which is the mistake that cost an hour.
token_works() {
  [ -n "${1:-}" ] || return 1
  [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
        -H "Authorization: Token $1" -H "Host: $HOST" \
        "$API/organizers/$ORG/events/")" = 200 ]
}

say "Finding a token that works"
TOKEN=${PRETIX_API_TOKEN:-}
if token_works "$TOKEN"; then
  echo "the token in .env is fine"
else
  warn "the token in .env cannot list events; asking pretix for one"
  OUT=$(docker compose "${FILES[@]}" exec -T \
          -e PRETIX_ADMIN_EMAIL="${PRETIX_ADMIN_EMAIL:-admin@jslwealth.in}" \
          -e PRETIX_ADMIN_PASSWORD="${PRETIX_ADMIN_PASSWORD:-}" \
          -e PRETIX_ORGANIZER="$ORG" \
          pretix python - < "$ROOT/scripts/bootstrap-pretix.py")
  echo "$OUT" | grep -v '^PRETIX_API_TOKEN='
  TOKEN=$(echo "$OUT" | grep -E '^PRETIX_API_TOKEN=' | tail -1 | cut -d= -f2-)
  token_works "$TOKEN" || die "even a freshly issued token cannot list events; run scripts/diagnose-pretix.sh"
  sed -i '/^PRETIX_API_TOKEN=/d' "$ENVFILE"
  printf 'PRETIX_API_TOKEN=%s\n' "$TOKEN" >> "$ENVFILE"
  echo "recorded a working token"
fi

say "Creating the event, if it is not already there"
COUNT=$(curl -s --max-time 10 -H "Authorization: Token $TOKEN" -H "Host: $HOST" \
        "$API/organizers/$ORG/events/" | sed -n 's/.*"count":\([0-9]*\).*/\1/p')
echo "events currently in pretix: ${COUNT:-unknown}"

PRETIX_API_BASE="$API" PRETIX_HOST="$HOST" PRETIX_API_TOKEN="$TOKEN" \
PRETIX_ORGANIZER="$ORG" PRETIX_EVENT="$EV" \
DIRECTORY_DSN="postgresql://directory:${DIRECTORY_DB_PASSWORD}@127.0.0.1:${POSTGRES_PORT:-5434}/directory" \
  python3 "$ROOT/scripts/bootstrap-event.py" || die "event setup failed"

say "Restarting the portal"
docker compose "${FILES[@]}" up -d --build portal
sleep 10

say "Health"
HEALTH=$(curl -s --max-time 15 "http://127.0.0.1:${PORTAL_PORT:-8090}/healthz" || true)
echo "$HEALTH"
case "$HEALTH" in
  *'"status":"ok"'*) ;;
  *) die "still not healthy — run scripts/diagnose-pretix.sh and send the output" ;;
esac

say "The links"
cd "$ROOT"
BASE=${PORTAL_BASE:-https://${PORTAL_DOMAIN:-localhost}}
for role in "desk shared 365" "admin nimish 90" "door entrance-1 7"; do
  set -- $role
  printf '%-6s ' "$1"
  BROKER_LINK_SECRET="$BROKER_LINK_SECRET" python3 portal/issue-link.py "$1" "$2" --days "$3" --base "$BASE"
done
