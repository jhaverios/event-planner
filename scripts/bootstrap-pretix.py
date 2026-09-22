#!/usr/bin/env python3
"""Create the pretix identity, organizer, teams and API token, without the UI.

Normally this is four trips through the pretix admin: superuser, organizer,
team, token. That makes a deploy wait on a person, and the person is the slow
part. This does the same work through pretix's own models.

Runs INSIDE the pretix container, where Django and the database are configured:

    docker compose exec -T pretix python - < scripts/bootstrap-pretix.py

Reads PRETIX_ADMIN_EMAIL, PRETIX_ADMIN_PASSWORD and PRETIX_ORGANIZER from the
environment. Idempotent: re-running reuses what exists and reprints the token.

The permission sets below are copied from a team that was built by hand through
the UI and proven to work, not guessed from the field names.
"""
import os
import secrets
import sys

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pretix.settings")
django.setup()

from django.db import transaction  # noqa: E402
from pretix.base.models import Organizer, Team, User  # noqa: E402
from pretix.base.models.organizer import TeamAPIToken  # noqa: E402

EMAIL = os.environ.get("PRETIX_ADMIN_EMAIL", "admin@jslwealth.in")
PASSWORD = os.environ.get("PRETIX_ADMIN_PASSWORD") or ""
ORG_SLUG = os.environ.get("PRETIX_ORGANIZER", "jsl")
ORG_NAME = os.environ.get("PRETIX_ORGANIZER_NAME", "Jhaveri Securities")

# What n8n and the portal need: read and write orders, check people in, and
# create the event series, its items and its subevents.
AUTOMATION_EVENT = {
    "event.items:write": True,
    "event.orders:read": True,
    "event.orders:write": True,
    "event.orders:checkin": True,
    "event.vouchers:read": True,
    "event.vouchers:write": True,
    "event.subevents:write": True,
    "event.settings.general:write": True,
}
AUTOMATION_ORG = {
    "organizer.events:create": True,
    "organizer.devices:write": True,
}
# Door staff can admit people and nothing else. A scanner at an entrance must
# not be able to change an order or read the client list.
DOOR_EVENT = {
    "event.orders:read": True,
    "event.orders:checkin": True,
}

out = {}

with transaction.atomic():
    user = User.objects.filter(email=EMAIL).first()
    if user is None:
        pw = PASSWORD or secrets.token_urlsafe(18)
        user = User.objects.create_user(email=EMAIL, password=pw)
        user.is_staff = True
        user.is_active = True
        user.save()
        out["admin_password"] = pw if not PASSWORD else "(as supplied)"
        created_user = True
    else:
        if PASSWORD:
            user.set_password(PASSWORD)
        user.is_staff = True
        user.is_active = True
        user.save()
        out["admin_password"] = "(unchanged)" if not PASSWORD else "(reset as supplied)"
        created_user = False

    org, org_new = Organizer.objects.get_or_create(
        slug=ORG_SLUG, defaults={"name": ORG_NAME})

    # Case-insensitive, because get_or_create(name="Automation") happily made
    # a SECOND team next to an existing "automation" on jprod, and the token
    # in .env stayed on the old one with the narrower permissions. A duplicate
    # team is worse than no team: everything looks configured and nothing works.
    def team(name):
        t = Team.objects.filter(organizer=org, name__iexact=name).first()
        return t or Team.objects.create(organizer=org, name=name)

    admins = team("Admins")
    admins.all_events = True
    admins.all_event_permissions = True
    admins.all_organizer_permissions = True
    admins.save()
    admins.members.add(user)

    automation = team("Automation")
    automation.all_events = True
    automation.all_event_permissions = False
    automation.limit_event_permissions = AUTOMATION_EVENT
    automation.all_organizer_permissions = False
    automation.limit_organizer_permissions = AUTOMATION_ORG
    automation.save()

    door = team("Door staff")
    door.all_events = True
    door.all_event_permissions = False
    door.limit_event_permissions = DOOR_EVENT
    door.all_organizer_permissions = False
    door.limit_organizer_permissions = {}
    door.save()

    token = TeamAPIToken.objects.filter(team=automation, active=True).first()
    # Report the team's real name so a mismatch with .env is visible.
    if token is None:
        token = TeamAPIToken.objects.create(
            team=automation, name="portal", active=True,
            token=secrets.token_hex(32))

# Machine-readable last line so the deploy script can pick the token up with a
# single grep rather than parsing prose.
print(f"admin email     : {EMAIL}")
print(f"admin password  : {out['admin_password']}")
print(f"admin is new    : {created_user}")
print(f"organizer       : {org.slug} ({'created' if org_new else 'existing'})")
print(f"teams           : {admins.name}, {automation.name}, {door.name}")
print(f"token is on     : team {automation.name!r}, token {token.name!r}")
print(f"PRETIX_API_TOKEN={token.token}", file=sys.stdout)
