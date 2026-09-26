#!/usr/bin/env python3
"""Set what an event's invitation says.

Pretix holds when it starts and where. This holds who is speaking, which is
usually the reason somebody comes, and which pretix has no field for.

  python3 scripts/set-event-details.py 2 --speaker "Sanket Joshi" \
      --speaker-title "Cluster Head Baroda" --speaker-org "ICICI Prudential AMC" \
      --note "Dinner to follow." --rsvp-name "Vimal Pandya" --rsvp-phone 9712989074

  python3 scripts/set-event-details.py 2 --show

A field left out keeps whatever is stored. Passing an empty string clears it,
so a wrong speaker can be removed rather than being stuck because blank was
read as "no change".
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "portal"))
import db  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("subevent_id", type=int)
ap.add_argument("--speaker")
ap.add_argument("--speaker-title")
ap.add_argument("--speaker-org")
ap.add_argument("--description")
ap.add_argument("--note")
ap.add_argument("--rsvp-name")
ap.add_argument("--rsvp-phone")
ap.add_argument("--show", action="store_true", help="print what is stored and exit")
a = ap.parse_args()

db.init()
if not db.available():
    sys.exit("DIRECTORY_DSN is not set, so there is nowhere to store this")

if a.show:
    d = db.event_details(a.subevent_id)
    if not d:
        sys.exit(f"no invitation details stored for subevent {a.subevent_id}")
    for k in db.DETAIL_FIELDS:
        print(f"{k:<14} {d.get(k) or '-'}")
    print(f"{'speaker line':<14} {db.speaker_line(d) or '-'}")
    raise SystemExit(0)

fields = {k: v for k, v in vars(a).items()
          if k in db.DETAIL_FIELDS and v is not None}
if not fields:
    sys.exit("nothing to set; pass at least one field, or --show")

db.set_event_details(a.subevent_id, **fields)
print(f"subevent {a.subevent_id} updated:")
for k, v in fields.items():
    print(f"  {k:<14} {v!r}")
print(f"\nWhatsApp event line will read:\n  "
      f"<event name>, with {db.speaker_line(db.event_details(a.subevent_id))}")
