#!/usr/bin/env python3
"""Mint a signed link.

  python3 issue-link.py broker BRK042
  python3 issue-link.py admin nimish --days 30
  python3 issue-link.py door entrance-1 --days 7

A broker link is the broker's sign-in, so it is sent to them once and lasts
until it expires. Door links should be short-lived: they are read out to staff
and end up in group chats.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth  # noqa: E402
import config  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("role", choices=auth.ROLES)
ap.add_argument("subject", help="broker code, admin username, or entrance name")
ap.add_argument("--days", type=int, default=180)
ap.add_argument("--base", default=config.PORTAL_BASE)
a = ap.parse_args()

if not auth.SECRET:
    sys.exit("BROKER_LINK_SECRET is not set")

# Must cover every role in auth.ROLES, and match the map in app.py's root().
# "desk" is the shared registration-desk link and lands on the same page as a
# broker link; the form then asks which broker code is using it.
path = {"desk": "/r", "broker": "/r", "admin": "/admin", "door": "/door"}[a.role]
print(f"{a.base}{path}?t={auth.issue(a.role, a.subject, a.days)}")
