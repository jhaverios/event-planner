#!/usr/bin/env python3
"""Who replied to the follow-up — button taps and typed replies alike.

    docker compose exec -T portal python - --subevent 1 --since 2026-09-26 \
        < scripts/read-interest.py

WATI's Growth plan has no webhooks, so replies are polled rather than pushed.
getMessages/{phone} returns every event on a conversation; an outbound template
is eventType "broadcastMessage".

What an inbound quick-reply is stamped as has NOT been observed on this
account — nobody had replied when this was written — so this does not filter
for a value it has guessed. It reports everything on the conversation after
the send that is not outbound, and prints the eventType it found. The first
real reply names the thing, in the output, instead of being silently dropped
by a filter that was wrong.

Reads only. Sends nothing.
"""
import argparse
import os
import re
import sys
import time

sys.path.insert(0, "/app")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "portal"))

import httpx  # noqa: E402
import config  # noqa: E402
import db  # noqa: E402
import pretix  # noqa: E402
import wati  # noqa: E402

OUTBOUND = {"broadcastMessage", "ticket", "sessionMessage"}

ap = argparse.ArgumentParser()
ap.add_argument("--subevent", type=int, default=int(os.environ.get("INTEREST_SUBEVENT") or 0))
ap.add_argument("--template", default="jsl_investment_event_followup",
                help="the template whose replies these are. Each person's own "
                     "send time becomes the cut-off for what counts.")
ap.add_argument("--since", default="",
                help="an extra floor, ISO date. The per-person send time is "
                     "used regardless; this only tightens it further.")
ap.add_argument("--reset", action="store_true",
                help="clear what was recorded for this event first, so a "
                     "corrected run does not leave wrong rows behind")
ap.add_argument("--attended", action="store_true", default=True,
                help="only people who came (the follow-up's audience)")
ap.add_argument("--everyone", action="store_true", help="every registration instead")
ap.add_argument("--pause", type=float, default=0.25)
ap.add_argument("--no-record", action="store_true",
                help="print only; do not write to the dashboard")
ap.add_argument("--recheck-known", action="store_true",
                help="also poll people already recorded as interested. Off by "
                     "default: once someone has answered, asking again every "
                     "run spends calls to learn nothing.")
ap.add_argument("--quiet", action="store_true",
                help="print only what changed — for running on a schedule")
a = ap.parse_args()

if not a.subevent:
    sys.exit("which event? pass --subevent <id>")
if not wati.configured():
    sys.exit("WATI is not configured in this container")

db.init()
interest_missing = "interest" in db.missing_tables()
ident = {v: k for k, v in pretix.questions().items()}
dead = pretix.order_status()

people = []
for p in pretix.positions(subevent_id=a.subevent):
    if p.get("canceled") or dead.get(p.get("order")) in ("c", "e"):
        continue
    if not a.everyone and not p.get("checkins"):
        continue
    ans = {ident.get(x["question"]): x["answer"] for x in p.get("answers", [])}
    phone = re.sub(r"\D", "", ans.get("client_phone") or "")
    if phone:
        people.append((p.get("order"), (p.get("attendee_name") or "").strip(),
                       phone[-12:] if phone.startswith("91") else f"91{phone[-10:]}",
                       ans.get("broker_code") or ""))

if a.reset and not a.no_record:
    print(f"cleared {db.clear_interest(a.subevent)} previously recorded row(s)\n",
          file=sys.stderr)

# A reply only means something relative to the message it answers. Counting
# any inbound message a contact ever sent turned years of ordinary client
# conversation into "interested in the Contra Fund".
try:
    sent_at = db.followup_sent_at(a.template)
except Exception as e:
    sys.exit(f"cannot read when {a.template} was sent ({type(e).__name__}: {e}).\n"
             "Refusing to guess: without it every old message counts as interest.")
if not sent_at:
    sys.exit(f"no successful send of {a.template} is recorded, so there is "
             f"nothing for a reply to be a reply to. Send it first, or pass "
             f"--template with the name that was actually used.")

known = {} if (a.recheck_known or a.reset) else db.interest_for(a.subevent)
if known:
    people = [t for t in people if t[0] not in known]

print(f"\nreading {len(people)} conversation(s)"
      + (f", {len(known)} already answered" if known else "")
      + (f" since {a.since}" if a.since else " (all history)") + "\n")

interested, quiet, unreadable, no_send = [], 0, 0, 0
for ref, name, phone, broker in people:
    try:
        r = httpx.get(f"{config.WATI_BASE}/api/v1/getMessages/{phone}",
                      params={"pageSize": 20},
                      headers={"Authorization": f"Bearer {config.WATI_TOKEN}"},
                      timeout=25.0)
        r.raise_for_status()
        items = (r.json().get("messages") or {}).get("items") or []
    except Exception as e:
        unreadable += 1
        print(f"  ?  {name[:26]:26} {phone}  could not read ({type(e).__name__})")
        time.sleep(a.pause)
        continue

    # After we messaged THEM, not after some global date.
    floor = sent_at.get(ref)
    if floor is None:
        no_send += 1
        time.sleep(a.pause)
        continue
    cut = floor.isoformat() if hasattr(floor, "isoformat") else str(floor)
    if a.since and a.since > cut:
        cut = a.since
    replies = [m for m in items
               if m.get("eventType") not in OUTBOUND
               and (m.get("created") or "") > cut]
    if replies:
        newest = max(replies, key=lambda m: m.get("created") or "")
        for m in replies:
            txt = (m.get("finalText") or "").replace("\n", " ")[:60]
            print(f"  >  {name[:26]:26} {phone}  [{ref}] broker={broker}")
            print(f"     {m.get('created','')[:19]}  {m.get('eventType')}  {txt}")
        interested.append((ref, name, phone, broker))
        if not a.no_record:
            # Onto the admin dashboard, so nobody has to open WATI to find out
            # who asked to be called. The page reads this table; it never calls
            # WATI itself.
            db.record_interest(
                ref, subevent_id=a.subevent, phone=phone,
                event_type=newest.get("eventType") or "",
                body=(newest.get("finalText") or "").strip(),
                replied_at=(newest.get("created") or None))
    else:
        quiet += 1
    time.sleep(a.pause)

if not a.no_record and interest_missing:
    print("\nthe interest table does not exist yet — re-apply "
          "deploy/postgres/directory-schema.sql, or nothing reaches the "
          "dashboard", file=sys.stderr)

if a.quiet and not interested and not unreadable and not a.reset:
    # Nothing new. A scheduled run that prints a report every half hour is a
    # log nobody reads, which means the run that mattered goes unnoticed.
    raise SystemExit(0)

print(f"\n{len(interested)} replied · {quiet} silent"
      + (f" · {no_send} never sent the follow-up" if no_send else "")
      + (f" · {unreadable} unreadable" if unreadable else ""))
if interested:
    print("\nphone,name,reference,broker")
    for ref, name, phone, broker in interested:
        print(f"{phone},{name},{ref},{broker}")
