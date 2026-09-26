#!/usr/bin/env python3
"""Send the day-before reminder to everyone registered for one event.

Runs INSIDE the portal container, where the dependencies and the credentials
already are — the same reason bootstrap-pretix.py runs inside pretix:

    docker compose exec -T portal python - --subevent 2 < scripts/send-reminders.py
    docker compose exec -T portal python - --subevent 2 --send < scripts/send-reminders.py

Dry run by default. The flag that actually messages two hundred people is the
one you have to type.

Why a script rather than an n8n workflow, which is where clock-driven work is
supposed to live: this fires once, the evening before the event, and n8n on
jprod has never been credentialed or exercised. Building a workflow for a
single send two days out trades something proven for something unproven. If
the portal outlives this event, W3 belongs in n8n and this is what it should
be ported from.

Safe to run twice. Anyone already sent this template successfully is skipped,
and the database refuses a second successful row even if two runs overlap.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, "/app")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "portal"))

import config  # noqa: E402
import db  # noqa: E402
import pretix  # noqa: E402
import wati  # noqa: E402
from app import event_line, qr_url_for, when_text  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--subevent", type=int, default=int(os.environ.get("REMINDER_SUBEVENT") or 0),
                help="the event date to remind about")
ap.add_argument("--send", action="store_true",
                default=bool(os.environ.get("REMINDER_SEND")),
                help="actually send. Without it, nothing leaves the building.")
ap.add_argument("--template", default=config.WATI_REMINDER_TEMPLATE)
ap.add_argument("--attended", action="store_true",
                help="only people whose pass was scanned at the door")
ap.add_argument("--no-shows", action="store_true",
                help="only people who registered and did not come")
ap.add_argument("--values",
                help="pipe-separated values for a template whose slots are the "
                     "same for everyone, e.g. the post-event follow-up: "
                     "--values 'Event name|24 September 2026|what was discussed'. "
                     "Without it the five values of the entry pass are sent.")
ap.add_argument("--only", default=os.environ.get("REMINDER_ONLY", ""),
                help="comma-separated references, e.g. --only ZYXJK. Use it to "
                     "prove the message renders on a handset you hold before "
                     "sending to everybody.")
ap.add_argument("--limit", type=int, default=0,
                help="stop after N sends — for proving it works on one person first")
ap.add_argument("--pause", type=float, default=0.4,
                help="seconds between sends, so a burst does not look like a flood")
a = ap.parse_args()

if not a.subevent:
    sys.exit("which event? pass --subevent <id> (the admin dashboard shows it)")

db.init()
gaps = db.missing_tables()
if "reminders" in gaps:
    sys.exit("the reminders table does not exist — re-apply "
             "deploy/postgres/directory-schema.sql before sending anything")

ev = next((e for e in pretix.subevents() if e["id"] == a.subevent), None)
if not ev:
    sys.exit(f"no active event with id {a.subevent}")

details = db.event_details(a.subevent)
line = event_line(ev["name"], details)
when = when_text(ev["date_from"])
venue = ev["location"] or ""

ident = {v: k for k, v in pretix.questions().items()}
status = pretix.order_status()
try:
    # Raises rather than returning an empty set: "nobody has been reminded" is
    # indistinguishable from "the read failed", and acting on the wrong one
    # messages everybody a second time.
    already = db.reminded(a.template)
except Exception as e:
    sys.exit(f"cannot read who has already been reminded ({type(e).__name__}: {e}).\n"
             "Refusing to send, because a re-run would message everyone twice.")

only = {x.strip().upper() for x in a.only.split(",") if x.strip()}
if a.attended and a.no_shows:
    sys.exit("--attended and --no-shows are opposites; pick one")
fixed = [v.strip() for v in a.values.split("|")] if a.values else None
if fixed:
    got = [n for n in wati.template_params(a.template) if n != "qr_url"]
    if got and len(got) != len(fixed):
        sys.exit(f"{a.template} takes {len(got)} values ({', '.join(got)}) "
                 f"but {len(fixed)} were given. WATI rejects the send outright "
                 f"when they do not match, so this stops here rather than "
                 f"failing once per person.")

print(f"\n{ev['name']}\n  {when} · {venue}"
      f"\n  template: {a.template}"
      f"\n  mode: {'SENDING' if a.send else 'dry run — nothing will be sent'}"
      + f"\n  audience: {'attendees only' if a.attended else 'no-shows only' if a.no_shows else 'everyone registered'}"
      + (f"\n  values: {' | '.join(fixed)}" if fixed else "")
      + (f"\n  limited to: {', '.join(sorted(only))}" if only else "") + "\n")

sent = failed = 0
skipped = {}
for p in pretix.positions(subevent_id=a.subevent):
    order = p.get("order")
    name = p.get("attendee_name") or ""
    answers = {ident.get(x["question"]): x["answer"] for x in p.get("answers", [])}
    phone = answers.get("client_phone") or ""

    def skip(why):
        skipped.setdefault(why, []).append(f"{order} {name}".strip())

    if only and order not in only:
        continue
    if p.get("canceled") or status.get(order) in ("c", "e"):
        skip("cancelled or expired")
        continue
    came = bool(p.get("checkins"))
    if a.attended and not came:
        skip("did not come")
        continue
    if a.no_shows and came:
        skip("attended")
        continue
    if not phone:
        skip("no mobile number on the registration")
        continue
    if order in already:
        skip("already reminded")
        continue
    if a.limit and sent >= a.limit:
        skip(f"--limit {a.limit} reached")
        continue
    allowed, why = wati.broadcast_allowed(phone)
    if not allowed:
        skip(why)
        continue

    if not a.send:
        print(f"  would send → {name or '(no name)':24} {phone}  [{order}]")
        sent += 1
        continue

    if fixed is not None:
        # Every slot identical for everyone — a follow-up addressed "Dear
        # Investor" needs no per-person data, and the document in its header is
        # a fixed URL rather than a parameter.
        ok, detail = wati.send_template(
            phone=phone, template=a.template, values=fixed,
            broadcast_prefix="followup", reference=order)
    else:
        ok, detail = wati.send_pass(
            phone=phone, name=name or "there", event=line, when=when, venue=venue,
            reference=order, qr_url=qr_url_for(p["secret"]),
            template=a.template, broadcast_prefix="reminder")
    recorded = db.record_reminder(order, a.template, ok, detail)
    if not recorded and ok:
        # The message went out but the row did not land. Saying so is the only
        # way anyone knows a re-run might message this person twice.
        print(f"  !! {order} was SENT but not recorded — re-running may repeat it")
    print(f"  {'sent  ' if ok else 'FAILED'} {name or '(no name)':24} {phone}  "
          f"[{order}]  {detail if not ok else ''}")
    sent += ok
    failed += (not ok)
    time.sleep(a.pause)

print(f"\n{'sent' if a.send else 'would send'}: {sent}"
      + (f" · failed: {failed}" if failed else ""))
for why, who in sorted(skipped.items()):
    print(f"skipped ({why}): {len(who)}")
    for w in who[:10]:
        print(f"    {w}")
    if len(who) > 10:
        print(f"    … and {len(who) - 10} more")
if not a.send and sent:
    print("\nNothing was sent. Add --send to do it for real.")
sys.exit(1 if failed else 0)
