#!/usr/bin/env python3
"""Who actually turned up, as a CSV a WATI campaign can be fed.

Runs INSIDE the portal container, where the credentials are:

    docker compose exec -T portal python - --subevent 1 < scripts/export-attendees.py
    docker compose exec -T portal python - --subevent 1 --no-shows < scripts/export-attendees.py

Why a CSV rather than mapping the campaign's {{1}} to WATI's contact name:
WATI stores a contact's name as THE PHONE NUMBER until something tells it
otherwise — verified on this account, where contact 917990040687 has
fullName "917990040687". Mapping the greeting to that attribute would open a
follow-up with "Dear 919898575180" in a message whose whole purpose is reading
as though a person wrote it. Pretix holds the name the broker actually typed,
so the list comes from there.

Reads only. Sends nothing, writes nothing.
"""
import argparse
import csv
import os
import re
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "portal"))

import db  # noqa: E402
import pretix  # noqa: E402
from app import event_line, when_text  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--subevent", type=int,
                default=int(os.environ.get("EXPORT_SUBEVENT") or 0))
ap.add_argument("--no-shows", action="store_true",
                help="the people who registered and did not come, instead")
ap.add_argument("--out", help="write here instead of stdout")
a = ap.parse_args()

if not a.subevent:
    sys.exit("which event? pass --subevent <id>")

db.init()
ev = next((e for e in pretix.subevents() if e["id"] == a.subevent), None)
if not ev:
    sys.exit(f"no active event with id {a.subevent}")

ident = {v: k for k, v in pretix.questions().items()}
dead = pretix.order_status()
details = db.event_details(a.subevent)

rows, other = [], 0
for p in pretix.positions(subevent_id=a.subevent):
    if p.get("canceled") or dead.get(p.get("order")) in ("c", "e"):
        continue
    came = bool(p.get("checkins"))
    if came == a.no_shows:          # wanted attendees, got a no-show, or vice versa
        other += 1
        continue
    ans = {ident.get(x["question"]): x["answer"] for x in p.get("answers", [])}
    phone = re.sub(r"\D", "", ans.get("client_phone") or "")
    if not phone:
        continue
    rows.append({"phone": phone[-12:] if phone.startswith("91") else f"91{phone[-10:]}",
                 "name": (p.get("attendee_name") or "").strip(),
                 "reference": p.get("order"),
                 "broker": ans.get("broker_code") or ""})

rows.sort(key=lambda r: r["name"].lower())
out = open(a.out, "w", newline="", encoding="utf-8") if a.out else sys.stdout
w = csv.DictWriter(out, fieldnames=["phone", "name", "reference", "broker"])
w.writeheader()
w.writerows(rows)
if a.out:
    out.close()

who = "no-shows" if a.no_shows else "attendees"
# To stderr, so piping the CSV somewhere keeps it clean.
print(f"\n{len(rows)} {who} · {other} on the other side of the door\n",
      file=sys.stderr)
print("jsl_investment_event_followup greets \"Dear Investor\", so all three\n"
      "variables are the same for everyone. Type these once in the campaign;\n"
      "the upload only has to carry the numbers.\n", file=sys.stderr)
print(f"  {{{{1}}}} event : {event_line(ev['name'], details)}", file=sys.stderr)
print(f"  {{{{2}}}} date  : {when_text(ev['date_from']).split(',')[0]}", file=sys.stderr)
print(f"  {{{{3}}}} topic : <what was discussed — your words>\n", file=sys.stderr)
print("The name column is there for you, not for WATI: read it before fifty\n"
      "messages go out.\n", file=sys.stderr)
