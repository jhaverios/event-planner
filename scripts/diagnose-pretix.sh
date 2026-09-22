#!/usr/bin/env bash
# Read-only. Answers "why is the portal getting 403 from pretix".
#
#   sudo bash scripts/diagnose-pretix.sh
#
# Tries every active token against every endpoint the portal uses, and prints
# the response BODY rather than only the status, because pretix explains
# itself there and a bare 403 does not.

set -uo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)
ENVFILE="$ROOT/deploy/.env"
[ -r "$ENVFILE" ] || { echo "cannot read $ENVFILE — run with sudo" >&2; exit 1; }
set -a; . "$ENVFILE"; set +a

cd "$ROOT/deploy"
FILES=(-f docker-compose.yml)
[ -e /proc/net/if_inet6 ] || FILES+=(-f docker-compose.no-ipv6.yml)
PORT=${PRETIX_PORT:-8345}
HOST=${PRETIX_HOST:-${PRETIX_DOMAIN:-localhost}}
ORG=${PRETIX_ORGANIZER:-jsl}
EV=${PRETIX_EVENT:-investor-events}

echo "=== what pretix thinks its own URL is ==="
docker compose "${FILES[@]}" exec -T pretix sh -c \
  'for f in /etc/pretix/pretix.cfg /pretix.cfg ~/.pretix.cfg; do
     [ -f "$f" ] && echo "$f:" && grep -E "^(url|instance_name)" "$f"; done || true' 2>/dev/null \
  || echo "(config not found)"
echo "Host header this script will send: $HOST"

echo
echo "=== events that exist, per the database ==="
docker compose "${FILES[@]}" exec -T pretix python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','pretix.settings')
django.setup()
from django_scopes import scopes_disabled
from pretix.base.models import Event, SubEvent
with scopes_disabled():
    for e in Event.objects.all():
        print(' event:', e.organizer.slug + '/' + e.slug, '| live:', e.live,
              '| series:', e.has_subevents, '| testmode:', e.testmode)
        for s in SubEvent.objects.filter(event=e):
            print('    subevent', s.pk, '|', s.name, '| active:', s.active)
"

echo
echo "=== every active token, against every endpoint the portal uses ==="
TOKENS=$(docker compose "${FILES[@]}" exec -T pretix python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','pretix.settings')
django.setup()
from django_scopes import scopes_disabled
from pretix.base.models.organizer import TeamAPIToken
with scopes_disabled():
    for k in TeamAPIToken.objects.filter(active=True):
        print(k.team.name + '|' + k.name + '|' + k.token)
" 2>/dev/null)

while IFS='|' read -r team name token; do
  [ -n "${token:-}" ] || continue
  echo
  echo "--- team '$team', token '$name' ---"
  for path in "organizers/" "organizers/$ORG/events/" \
              "organizers/$ORG/events/$EV/" \
              "organizers/$ORG/events/$EV/subevents/" \
              "organizers/$ORG/events/$EV/items/" \
              "organizers/$ORG/events/$EV/checkinlists/"; do
    body=$(curl -s --max-time 10 -H "Authorization: Token $token" -H "Host: $HOST" \
           "http://127.0.0.1:$PORT/api/v1/$path" 2>/dev/null)
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
           -H "Authorization: Token $token" -H "Host: $HOST" \
           "http://127.0.0.1:$PORT/api/v1/$path" 2>/dev/null)
    short=$(echo "$body" | tr -d '\n' | cut -c1-110)
    printf '  %-3s /%-42s %s\n' "$code" "$path" "$short"
  done
done <<< "$TOKENS"

echo
echo "=== which token is in .env ==="
echo "PRETIX_API_TOKEN currently ends with: ...${PRETIX_API_TOKEN: -8}"
while IFS='|' read -r team name token; do
  [ -n "${token:-}" ] || continue
  [ "$token" = "${PRETIX_API_TOKEN:-}" ] && echo "  that is team '$team', token '$name'"
done <<< "$TOKENS"
