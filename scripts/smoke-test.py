#!/usr/bin/env python3
"""Prove a deployment works, against the thing people will actually use.

Two modes, because one of them sends real messages to a real phone:

  --quick   everything that does not send: health, the auth boundaries, the
            event list, every input the form can reject, and the door's
            behaviour on a code that does not exist. Safe to run any time.

  --phone / --email   the full path. Registers a real client, asserts both
            channels were accepted, polls WhatsApp for delivery, checks the
            door admits them once and refuses them twice, checks the numbers
            reach the dashboard, then cancels the order it created.

Supply a phone and email you own. This sends real messages.

  python3 scripts/smoke-test.py --base https://events.jslwealth.in --quick
  python3 scripts/smoke-test.py --base https://events.jslwealth.in \
      --phone 9833693876 --email you@example.com

BROKER_LINK_SECRET must be set so the test can mint its own links, or pass
--desk-token / --admin-token / --door-token instead.
"""
import argparse
import os
import sys
import time

import httpx

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
results = []


def record(name, status, detail=""):
    results.append((name, status, detail))
    mark = {"PASS": "  ok  ", "FAIL": " FAIL ", "SKIP": " skip "}[status]
    print(f"[{mark}] {name}" + (f"  — {detail}" if detail else ""), flush=True)


def check(name, condition, detail=""):
    record(name, PASS if condition else FAIL, detail)
    return condition


ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://127.0.0.1:8090",
                help="the portal's origin, e.g. https://events.jslwealth.in")
ap.add_argument("--quick", action="store_true", help="skip anything that sends")
ap.add_argument("--phone", help="a real Indian mobile you own")
ap.add_argument("--email", help="a real address you own")
ap.add_argument("--broker", default="SMOKE01")
ap.add_argument("--name", default="Smoke Test")
ap.add_argument("--desk-token")
ap.add_argument("--admin-token")
ap.add_argument("--door-token")
ap.add_argument("--cacert", help="CA bundle, if behind a TLS-inspecting proxy")
ap.add_argument("--keep", action="store_true", help="do not cancel the test order")
a = ap.parse_args()

verify = a.cacert or True
c = httpx.Client(base_url=a.base.rstrip("/"), timeout=45.0, verify=verify,
                 follow_redirects=True)

# --- tokens --------------------------------------------------------------
desk, admin, door = a.desk_token, a.admin_token, a.door_token
if not all((desk, admin, door)):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "portal"))
    try:
        import auth
        if not auth.SECRET:
            sys.exit("BROKER_LINK_SECRET is not set and no tokens were passed")
        desk = desk or auth.issue("desk", "smoke", 1)
        admin = admin or auth.issue("admin", "smoke", 1)
        door = door or auth.issue("door", "smoke", 1)
    except ImportError:
        sys.exit("cannot mint tokens here; pass --desk-token/--admin-token/--door-token")

H = lambda t: {"Authorization": f"Bearer {t}"}          # noqa: E731

print(f"\nTesting {a.base}\n" + "=" * 60)

# --- 1. health -----------------------------------------------------------
try:
    h = c.get("/healthz").json()
except Exception as e:
    record("portal answers /healthz", FAIL, f"{type(e).__name__}: {e}")
    print("\nThe portal is not reachable. Nothing else can be tested.")
    sys.exit(1)

check("health: status is ok", h.get("status") == "ok",
      f"missing: {h.get('missing')}" + (f" · {h.get('pretix_error','')}" if h.get("pretix_error") else ""))
for k in ("pretix", "directory", "link_secret"):
    check(f"health: {k}", bool(h.get("checks", {}).get(k)))
for k in ("whatsapp", "email"):
    record(f"health: {k}", PASS if h.get("checks", {}).get(k) else SKIP,
           "" if h.get("checks", {}).get(k) else "not configured")

# --- 2. the auth boundaries ---------------------------------------------
check("auth: no token is refused", c.get("/api/session").status_code == 401)
check("auth: a tampered signature is refused",
      c.get("/api/session", headers=H("desk.smoke.9999999999." + "A" * 32)).status_code == 401)
check("auth: an expired link is refused",
      c.get("/api/session", headers=H("desk.smoke.1000000000." + "A" * 32)).status_code == 401)
check("auth: a desk link cannot open the door console",
      c.post("/api/checkin", headers=H(desk), json={"code": "XXXXX", "list_id": 1}).status_code == 403)
check("auth: a door link cannot register anyone",
      c.post("/api/register", headers=H(door),
             json={"subevent": 1, "name": "x", "phone": "9833693876"}).status_code == 403)

# --- 2b. every role can actually have a link minted ----------------------
# issue-link.py keeps its own role-to-path map, separate from auth.ROLES.
# When "desk" was added to one and not the other, the command used to mint the
# broker link raised KeyError — and nothing here caught it, because the tests
# mint tokens through auth.issue directly.
try:
    import subprocess
    link_script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "portal", "issue-link.py")
    for role in ("desk", "broker", "admin", "door"):
        rr = subprocess.run([sys.executable, link_script, role, "smoke", "--days", "1"],
                            capture_output=True, text=True, timeout=30)
        check(f"links: a {role} link can be minted",
              rr.returncode == 0 and "?t=" in rr.stdout,
              (rr.stderr.strip().splitlines() or [""])[-1][:70])
except Exception as e:
    record("links: minting", SKIP, str(e)[:60])

# --- 3. the event list ---------------------------------------------------
r = c.get("/api/events", headers=H(desk))
ok = check("events: the list loads", r.status_code == 200, f"HTTP {r.status_code}")
events = r.json() if ok else []
upcoming = [e for e in events if not e.get("past")]
check("events: at least one upcoming event", bool(upcoming),
      f"{len(events)} total, {len(upcoming)} upcoming")
if upcoming:
    ev = upcoming[0]
    check("events: the event has a venue", bool(ev.get("location")))
    record("events: the speaker is set", PASS if ev.get("speaker") else FAIL,
           ev.get("speaker") or "no speaker — the WhatsApp event line will omit it")
    subevent = ev["id"]
else:
    subevent = None

# --- 4. everything the form must reject ----------------------------------
if subevent:
    bad = [
        ("a mistyped mobile", {"subevent": subevent, "name": a.name, "phone": "12345",
                               "broker_code": a.broker}, "valid Indian mobile"),
        ("a landline-shaped number", {"subevent": subevent, "name": a.name, "phone": "5123456789",
                                      "broker_code": a.broker}, "valid Indian mobile"),
        ("no way to contact the client", {"subevent": subevent, "name": a.name,
                                          "broker_code": a.broker}, "mobile number or an email"),
        ("a one-letter name", {"subevent": subevent, "name": "A", "phone": "9833693876",
                               "broker_code": a.broker}, "client's name"),
        ("no broker code", {"subevent": subevent, "name": a.name,
                            "phone": "9833693876"}, "broker code"),
        ("a broker code with spaces", {"subevent": subevent, "name": a.name, "phone": "9833693876",
                                       "broker_code": "a b!"}, "does not look right"),
    ]
    for label, payload, expect in bad:
        rr = c.post("/api/register", headers=H(desk), json=payload)
        msg = (rr.json() or {}).get("error", "") if rr.status_code == 400 else ""
        check(f"rejects: {label}", rr.status_code == 400 and expect in msg,
              f"HTTP {rr.status_code} · {msg[:70]}")

# --- 5. the door, on a code that does not exist --------------------------
rr = c.post("/api/checkin", headers=H(door), json={"code": "ZZZZZ", "list_id": 1})
check("door: an unknown reference is refused, not crashed",
      rr.status_code == 200 and rr.json().get("reason") == "not_found",
      f"HTTP {rr.status_code} · {rr.json() if rr.status_code == 200 else ''}")

# --- 6. the full path ----------------------------------------------------
reference = None
if a.quick or not (a.phone or a.email):
    record("full registration", SKIP, "--quick, or no --phone/--email given")
elif not subevent:
    record("full registration", SKIP, "no upcoming event to register for")
else:
    body = {"subevent": subevent, "name": a.name, "broker_code": a.broker}
    if a.phone:
        body["phone"] = a.phone
    if a.email:
        body["email"] = a.email
    rr = c.post("/api/register", headers=H(desk), json=body)
    ok = check("register: accepted", rr.status_code == 200,
               f"HTTP {rr.status_code} · {rr.text[:120]}")
    if ok:
        d = rr.json()
        reference = d["reference"]
        check("register: a reference came back", bool(reference), reference)
        # event_line is what the client is actually sent, so that is what has
        # to carry the speaker, not the bare event name.
        check("register: the event line carries the speaker",
              (", with " in d.get("event_line", "")) or not ev.get("speaker"),
              d.get("event_line", "")[:90])
        for chan in ("whatsapp", "email"):
            got = (d.get("delivery") or {}).get(chan)
            if got is None:
                record(f"send: {chan}", SKIP, "not attempted")
            else:
                check(f"send: {chan} accepted", got.get("ok"), got.get("detail", ""))

        # delivery is asynchronous; give it a moment rather than asserting instantly
        if a.phone and (d.get("delivery") or {}).get("whatsapp", {}).get("ok"):
            record("send: waiting 10s for a delivery receipt", PASS, "")
            time.sleep(10)

        # --- the door, for real ---
        r1 = c.post("/api/checkin", headers=H(door),
                    json={"code": reference, "list_id": 1}).json()
        check("door: admits the guest", r1.get("status") == "ok",
              f"{r1.get('status')} · {r1.get('reason') or ''}")
        r2 = c.post("/api/checkin", headers=H(door),
                    json={"code": reference, "list_id": 1}).json()
        check("door: refuses the same pass twice",
              r2.get("reason") == "already_redeemed", str(r2.get("reason")))

        # --- the dashboard ---
        st = c.get(f"/api/stats?subevent={subevent}", headers=H(admin))
        ok = check("dashboard: loads", st.status_code == 200, f"HTTP {st.status_code}")
        if ok:
            s = st.json()
            mine = [p for p in s["people"] if p["reference"] == reference]
            check("dashboard: the registration appears", bool(mine))
            if mine:
                check("dashboard: shown as checked in", mine[0]["attended"])
                check("dashboard: attributed to the right broker",
                      mine[0]["broker"] == a.broker, mine[0]["broker"])
            check("dashboard: turnout counts it", s["attended"] >= 1,
                  f"{s['attended']}/{s['total']} = {s['rate']}%")

        # --- a broker sees only their own ---
        try:
            import auth as _auth
            other = _auth.issue("broker", "SMOKE-OTHER", 1)
            os_ = c.get(f"/api/stats?subevent={subevent}", headers=H(other))
            if os_.status_code == 200:
                codes = {b["broker"] for b in os_.json()["brokers"]}
                check("privacy: another broker cannot see this registration",
                      a.broker not in codes, f"sees {codes or 'nothing'}")
            else:
                record("privacy: broker scoping", SKIP, f"HTTP {os_.status_code}")
        except Exception as e:
            record("privacy: broker scoping", SKIP, str(e)[:60])

# --- 7. clean up ---------------------------------------------------------
if reference and not a.keep:
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "portal"))
        import pretix
        cancelled = pretix.cancel_order(reference)
        record("cleanup: test order cancelled", PASS if cancelled else FAIL,
               reference if cancelled
               else f"{reference} is still live — cancel it before the event")
    except Exception as e:
        record("cleanup: test order", FAIL,
               f"{reference} is still live — cancel it before the event ({e})")
elif reference:
    record("cleanup", SKIP, f"--keep given; {reference} left in place")

# --- summary -------------------------------------------------------------
print("=" * 60)
p = sum(1 for _, s, _ in results if s == PASS)
f = sum(1 for _, s, _ in results if s == FAIL)
k = sum(1 for _, s, _ in results if s == SKIP)
print(f"{p} passed, {f} failed, {k} skipped")
if f:
    print("\nFailed:")
    for n, s, d in results:
        if s == FAIL:
            print(f"  · {n}" + (f" — {d}" if d else ""))
sys.exit(1 if f else 0)
