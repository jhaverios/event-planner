#!/usr/bin/env bash
# Re-apply the Automation team's permissions and refresh the portal's token.
#
#   sudo bash scripts/fix-pretix-token.sh
#
# Use this when /healthz reports pretix false with a 403. A 403 means the token
# authenticates but its team cannot see the event — which happens when pretix
# was set up before scripts/bootstrap-pretix.py existed, or by hand through the
# UI with a narrower permission set.
#
# Needs root because deploy/.env is 0600 and owned by root, which is correct:
# it holds the WhatsApp and mail credentials.

set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)
ENVFILE="$ROOT/deploy/.env"

[ -r "$ENVFILE" ] || { echo "cannot read $ENVFILE — run with sudo" >&2; exit 1; }
set -a; . "$ENVFILE"; set +a

cd "$ROOT/deploy"
FILES=(-f docker-compose.yml)
[ -e /proc/net/if_inet6 ] || FILES+=(-f docker-compose.no-ipv6.yml)

echo "==> Re-applying the Automation team's permissions"
OUT=$(docker compose "${FILES[@]}" exec -T \
        -e PRETIX_ADMIN_EMAIL="${PRETIX_ADMIN_EMAIL:-admin@jslwealth.in}" \
        -e PRETIX_ADMIN_PASSWORD="${PRETIX_ADMIN_PASSWORD:-}" \
        -e PRETIX_ORGANIZER="${PRETIX_ORGANIZER:-jsl}" \
        pretix python - < "$ROOT/scripts/bootstrap-pretix.py")
echo "$OUT" | grep -v '^PRETIX_API_TOKEN='

TOKEN_LINE=$(echo "$OUT" | grep -E '^PRETIX_API_TOKEN=' | tail -1)
[ -n "$TOKEN_LINE" ] || { echo "no token came back" >&2; exit 1; }

# Prove the token works before writing it, rather than swapping one broken
# value for another.
TOKEN=${TOKEN_LINE#PRETIX_API_TOKEN=}
CODE=$(curl -s -o /dev/null -w '%{http_code}' \
       -H "Authorization: Token $TOKEN" -H "Host: ${PRETIX_HOST:-$PRETIX_DOMAIN}" \
       "http://127.0.0.1:${PRETIX_PORT:-8345}/api/v1/organizers/${PRETIX_ORGANIZER:-jsl}/events/" || true)
# Listing an organizer's events proves the token and nothing else. Asking for a
# SPECIFIC event conflates "bad token" with "event not created yet" — pretix
# answers 403 for an event that does not exist, and that cost an hour.
echo "==> That token listing events: HTTP $CODE"
if [ "$CODE" != 200 ]; then
  echo "Still not 200. Not writing it; the old value is untouched." >&2
  echo "Teams and their permissions:" >&2
  docker compose "${FILES[@]}" exec -T pretix python -c "
import django, os, json
os.environ.setdefault('DJANGO_SETTINGS_MODULE','pretix.settings')
django.setup()
from pretix.base.models import Team
from pretix.base.models.organizer import TeamAPIToken
for t in Team.objects.all():
    print(t.organizer.slug, '|', t.name, '| all_events:', t.all_events,
          '| all_perms:', t.all_event_permissions,
          '|', json.dumps(t.limit_event_permissions),
          '| tokens:', [k.name for k in TeamAPIToken.objects.filter(team=t, active=True)])
" >&2
  exit 1
fi

sed -i '/^PRETIX_API_TOKEN=/d' "$ENVFILE"
printf '%s\n' "$TOKEN_LINE" >> "$ENVFILE"
echo "==> Token recorded"

docker compose "${FILES[@]}" up -d portal
sleep 8
echo "==> Portal health"
curl -s "http://127.0.0.1:${PORTAL_PORT:-8090}/healthz"; echo
