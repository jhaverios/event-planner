#!/usr/bin/env python3
"""Create the event series, its questions and the first event date, over the API.

Runs on the host after scripts/bootstrap-pretix.py has produced a token.
Idempotent: everything is looked up before it is created, so a re-run after a
partial failure carries on rather than duplicating.

    PRETIX_API_TOKEN=... python3 scripts/bootstrap-event.py

Every payload here is one that has been accepted by a live pretix and recorded
in docs/runbooks/04-pretix-setup.md, including the two that bite:
  * an event cannot be created live, so it is created unpublished and set live
    once a quota exists
  * meta_data is required on a subevent even when it is empty
  * testmode must stay false, because pretixSCAN refuses tickets created in
    test mode and the failure looks like a broken scanner at the door
"""
import argparse
import os
import sys

import httpx

BASE = os.environ.get("PRETIX_API_BASE", "http://127.0.0.1:8345/api/v1").rstrip("/")
HOST = os.environ.get("PRETIX_HOST", "localhost")
TOKEN = os.environ.get("PRETIX_API_TOKEN", "")
ORG = os.environ.get("PRETIX_ORGANIZER", "jsl")
SERIES = os.environ.get("PRETIX_EVENT", "investor-events")

ap = argparse.ArgumentParser()
ap.add_argument("--series-name", default="JSL Investor Events")
ap.add_argument("--event-name", default="Look Beyond the Headlines — Contra Fund & SIF")
ap.add_argument("--starts", default="2026-09-24T18:30:00+05:30")
ap.add_argument("--venue",
                default="Hotel The Fern, Behind Dinesh Mills, Akota, Vadodara 390020")
ap.add_argument("--capacity", type=int, default=200)
# What the invitation says. Pretix has no field for a speaker, so these land in
# the directory database, which is where the WhatsApp event line and the pass
# email both read them from.
ap.add_argument("--speaker", default="Sanket Joshi")
ap.add_argument("--speaker-title", default="Cluster Head Baroda")
ap.add_argument("--speaker-org", default="ICICI Prudential AMC")
ap.add_argument("--note", default="Dinner to follow the session.")
ap.add_argument("--rsvp-name", default="Vimal Pandya")
ap.add_argument("--rsvp-phone", default="9712989074")
a = ap.parse_args()

if not TOKEN:
    sys.exit("PRETIX_API_TOKEN is not set; run scripts/bootstrap-pretix.py first")

c = httpx.Client(base_url=BASE, timeout=30.0,
                 headers={"Authorization": f"Token {TOKEN}", "Host": HOST,
                          "Content-Type": "application/json"})


def check(r, what):
    if r.status_code >= 400:
        sys.exit(f"{what} failed: {r.status_code} {r.text[:400]}")
    return r.json()


def find(path, match, params=None):
    d = check(c.get(path, params={"page_size": 200, **(params or {})}), f"listing {path}")
    for row in d["results"]:
        if match(row):
            return row
    return None


EV = f"/organizers/{ORG}/events/{SERIES}"

# --- the series ----------------------------------------------------------
series = find(f"/organizers/{ORG}/events/", lambda e: e["slug"] == SERIES)
if series:
    print(f"series          : {SERIES} (existing)")
else:
    series = check(c.post(f"/organizers/{ORG}/events/", json={
        "name": {"en": a.series_name}, "slug": SERIES, "live": False,
        "testmode": False, "currency": "INR", "date_from": a.starts,
        "is_public": False, "has_subevents": True}), "creating the series")
    print(f"series          : {SERIES} (created)")

# --- the product ---------------------------------------------------------
item = find(f"{EV}/items/", lambda i: True)
if item:
    print(f"item            : {item['id']} (existing)")
else:
    item = check(c.post(f"{EV}/items/", json={
        "name": {"en": "Registration"}, "default_price": "0.00",
        "admission": True, "active": True, "personalized": True}),
        "creating the item")
    print(f"item            : {item['id']} (created)")

# --- the questions -------------------------------------------------------
QUESTIONS = [
    ("client_phone", "TEL", True, False, "Client mobile number"),
    ("broker_code", "S", False, True, "Broker code"),
    ("consent_given", "B", False, True, "Client consented to WhatsApp updates"),
    ("consent_version", "S", False, True, "Consent notice version"),
    ("rsvp_status", "S", False, True, "RSVP status"),
    ("rsvp_at", "S", False, True, "RSVP recorded at"),
]
have = {q["identifier"] for q in
        check(c.get(f"{EV}/questions/", params={"page_size": 200}), "listing questions")["results"]}
made = []
for pos, (ident, qtype, required, hidden, label) in enumerate(QUESTIONS, start=1):
    if ident in have:
        continue
    check(c.post(f"{EV}/questions/", json={
        "question": {"en": label}, "type": qtype, "required": required,
        "position": pos, "identifier": ident, "items": [item["id"]],
        "ask_during_checkin": False, "hidden": hidden}), f"creating question {ident}")
    made.append(ident)
print(f"questions       : {len(have)} existing, {len(made)} created"
      + (f" ({', '.join(made)})" if made else ""))

# --- the event date ------------------------------------------------------
sub = find(f"{EV}/subevents/", lambda s: s["date_from"] == a.starts)
if sub:
    print(f"event date      : subevent {sub['id']} (existing)")
else:
    sub = check(c.post(f"{EV}/subevents/", json={
        "name": {"en": a.event_name}, "date_from": a.starts, "active": True,
        "is_public": False, "location": {"en": a.venue},
        # Required even when empty; omitting it returns 400.
        "meta_data": {},
        "item_price_overrides": [], "variation_price_overrides": []}),
        "creating the subevent")
    print(f"event date      : subevent {sub['id']} (created)")

# --- quota, then live ----------------------------------------------------
quota = find(f"{EV}/quotas/", lambda q: q.get("subevent") == sub["id"])
if quota:
    print(f"quota           : {quota['id']} (existing)")
else:
    quota = check(c.post(f"{EV}/quotas/", json={
        "name": "Seats", "size": a.capacity, "items": [item["id"]],
        "variations": [], "subevent": sub["id"]}), "creating the quota")
    print(f"quota           : {quota['id']} (created, {a.capacity} seats)")

cl = find(f"{EV}/checkinlists/", lambda l: l.get("subevent") == sub["id"])
if cl:
    print(f"check-in list   : {cl['id']} (existing)")
else:
    cl = check(c.post(f"{EV}/checkinlists/", json={
        "name": f"Main entrance — {a.event_name[:40]}", "all_products": True,
        "limit_products": [], "subevent": sub["id"]}), "creating the check-in list")
    print(f"check-in list   : {cl['id']} (created)")

# A series can only go live once a quota exists, so this is the last step.
if not series.get("live"):
    check(c.patch(f"/organizers/{ORG}/events/{SERIES}/", json={"live": True}),
          "setting the series live")
    print("series          : set live")
else:
    print("series          : already live")

# --- what the invitation says -------------------------------------------
# A deploy that creates the event but not the speaker leaves someone to
# remember a second command, and nobody does.
if a.speaker:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "portal"))
    try:
        import db  # noqa: E402
        db.init()
        if db.available():
            db.set_event_details(
                sub["id"], speaker=a.speaker, speaker_title=a.speaker_title,
                speaker_org=a.speaker_org, note=a.note,
                rsvp_name=a.rsvp_name, rsvp_phone=a.rsvp_phone)
            print(f"invitation      : {db.speaker_line(db.event_details(sub['id']))}")
        else:
            print("invitation      : skipped, DIRECTORY_DSN is not set")
    except Exception as e:
        # Never fail a working deploy over the speaker line.
        print(f"invitation      : could not be stored ({type(e).__name__}: {e})")

print(f"\nSUBEVENT_ID={sub['id']}")
print(f"CHECKIN_LIST_ID={cl['id']}")
