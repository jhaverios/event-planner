#!/usr/bin/env bash
# Reset the pretix administrator password, and record it where it belongs.
#
# Needed because .env is written on a machine's first deploy and preserved
# afterwards, so a machine deployed before PRETIX_ADMIN_PASSWORD existed has a
# superuser whose password was generated once, used once, and never written
# down. There is no way to read it back — a password hash is a hash — so the
# only honest repair is to set a new one.
#
# Matters for exactly one thing that cannot be done any other way: pairing the
# Android door scanners. Device pairing is organizer-level and the API token
# gets 403, so it needs a human signed into the pretix UI.
#
#   sudo scripts/reset-pretix-admin.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY="$ROOT/deploy"
ENVFILE="$DEPLOY/.env"
[ -f "$ENVFILE" ] || { echo "no $ENVFILE — run scripts/deploy-jprod.sh first" >&2; exit 1; }

cd "$DEPLOY"
OUT=$(docker compose exec -T pretix python - <<'PY'
import os
import secrets

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pretix.settings")
django.setup()
from pretix.base.models import User  # noqa: E402

for x in User.objects.all().order_by("id"):
    print(f"# existing user: {x.email}  staff={x.is_staff} active={x.is_active}")

pw = secrets.token_urlsafe(12)
# The staff user is the one that can reach /control/. Prefer it; fall back to
# any user; create one only if pretix has none at all.
u = (User.objects.filter(is_staff=True).order_by("id").first()
     or User.objects.order_by("id").first())
created = False
if u is None:
    u = User.objects.create_user(
        email=os.environ.get("PRETIX_ADMIN_EMAIL", "admin@localhost"), password=pw)
    created = True
u.is_staff = True
u.is_active = True
u.set_password(pw)
u.save()
print(f"# {'created' if created else 'reset'} {u.email}")
print(f"PRETIX_ADMIN_EMAIL={u.email}")
print(f"PRETIX_ADMIN_PASSWORD={pw}")
PY
)

echo "$OUT"
printf '%s\n' "$OUT" | grep -qE '^PRETIX_ADMIN_PASSWORD=.+' || {
  echo "the reset produced no password; nothing written to .env" >&2; exit 1; }

sed -i '/^PRETIX_ADMIN_EMAIL=/d;/^PRETIX_ADMIN_PASSWORD=/d' "$ENVFILE"
printf '%s\n' "$OUT" | grep -E '^PRETIX_ADMIN_' >> "$ENVFILE"
chmod 600 "$ENVFILE"

DOMAIN=$(grep -E '^PRETIX_DOMAIN=' "$ENVFILE" | cut -d= -f2- | tr -d '"'"'"'')
echo
echo "recorded in $ENVFILE. Sign in at https://${DOMAIN:-localhost}/control/login"
