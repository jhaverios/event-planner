"""The JSL event portal API and screens.

Split of responsibilities, deliberate: anything a human is waiting on happens
here, synchronously, so a broker learns in the form that a number is
unreachable rather than finding out days later. Anything on a clock —
reminders, delivery-status polling, the post-event split — stays in n8n.
"""
import datetime as dt
import re
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi import Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import auth
import config
import db
import mailer
import pretix
import wati

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="JSL Event Portal", docs_url=None, redoc_url=None)

MOBILE = re.compile(r"^[6-9]\d{9}$")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


@app.on_event("startup")
def _startup():
    db.init()


# ---------------------------------------------------------------- helpers

# The registration desk is the website. A broker opens events.jslwealth.in,
# types their broker code, and that is the sign-in. No token in a URL, because
# a link that has to be found again is a link that gets lost.
PUBLIC_DESK = {"role": "desk", "subject": "public", "public": True}


def _identity(request: Request, authorization: str = None, allow_public: bool = False):
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:]
    token = (token or request.query_params.get("t")
             or request.cookies.get("jsladmin") or request.cookies.get("jslt"))
    who = auth.verify(token) if token else None
    if who:
        return who
    if allow_public:
        return dict(PUBLIC_DESK)
    raise HTTPException(401, "not signed in")


def _need(who, *roles):
    if who["role"] not in roles:
        raise HTTPException(403, "not permitted for this link")
    return who


def normalise_mobile(raw):
    """Indian mobile to bare ten digits, or None. Accepts the shapes people
    actually paste: +91, 0091, a leading zero, spaces and dashes."""
    if not raw:
        return None
    d = re.sub(r"\D", "", str(raw))
    for p in ("0091", "91", "0"):
        if len(d) > 10 and d.startswith(p):
            d = d[len(p):]
            break
    return d if MOBILE.match(d) else None


def when_text(iso):
    """'24 September 2026, 6:30 PM' — the form the template was approved with."""
    try:
        t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(IST)
    except Exception:
        return iso
    hour = t.hour % 12 or 12
    return f"{t.day} {t:%B} {t.year}, {hour}:{t:%M} {t:%p}".replace("AM", "AM")


def qr_url_for(secret):
    return config.QR_BASE + secret


WA_PARAM_MAX = 200


def event_line(name, details):
    """The event as the client reads it, with the speaker folded in.

    The approved template has six fixed slots and no speaker slot, but this one
    is free text, so the speaker reaches WhatsApp without another Meta review.
    Truncated rather than allowed to fail: WATI rejects a parameter over 200
    characters, and a future event with a long title must not silently break a
    send.
    """
    who = db.speaker_line(details)
    line = f"{name}, with {who}" if who else name
    if len(line) <= WA_PARAM_MAX:
        return line
    if len(name) <= WA_PARAM_MAX:
        return name
    return name[:WA_PARAM_MAX - 1].rstrip() + "\u2026"


# ---------------------------------------------------------------- screens

@app.get("/")
def root(request: Request):
    """The registration desk, for anyone who opens the site.

    A personal or admin link still lands where it belongs; everyone else gets
    the broker form, which is what the domain is for.
    """
    t = request.query_params.get("t", "")
    if t:
        who = auth.verify(t)
        if who and who["role"] in ("admin", "door"):
            return RedirectResponse(f"/{who['role']}?t={t}")
    return FileResponse(STATIC / "register.html")


for _route, _file in (("/r", "register.html"), ("/admin", "admin.html"),
                      ("/door", "door.html")):
    def _mk(f):
        def _page():
            return FileResponse(STATIC / f)
        return _page
    app.get(_route, include_in_schema=False)(_mk(_file))

app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")


# ---------------------------------------------------------------- api

@app.get("/healthz", include_in_schema=False)
def healthz():
    """What is wired up, said plainly.

    A fresh deploy has no pretix API token yet, because the token cannot be
    created until pretix itself has been set up. Reporting that as "degraded"
    with the reason beats refusing to start with a stack trace.
    """
    # Asking whether the token is SET is not the same as asking whether it
    # WORKS. A stale token from an earlier install passes the first test and
    # fails every real request, and a deploy script that waits on this would
    # declare success over a broken portal. So call pretix.
    pretix_ok, pretix_why = False, "no token configured"
    if config.PRETIX_TOKEN:
        try:
            pretix.subevents()
            pretix_ok, pretix_why = True, "ok"
        except Exception as e:
            pretix_why = f"{type(e).__name__}: {str(e)[:120]}"

    # Connecting is not the same as being usable, for a database either.
    gaps = db.missing_tables()
    checks = {
        "pretix": pretix_ok,
        "directory": db.available() and not gaps,
        "whatsapp": wati.configured(),
        "email": mailer.configured(),
        "link_secret": bool(auth.SECRET),
    }
    missing = [k for k, v in checks.items() if not v]
    out = {"status": "ok" if not missing else "degraded",
           "checks": checks, "missing": missing}
    if not pretix_ok:
        out["pretix_error"] = pretix_why
    if gaps and db.available():
        out["directory_error"] = ("tables missing: " + ", ".join(gaps)
                                  + " — re-apply deploy/postgres/directory-schema.sql")
    return out


@app.get("/api/session")
def session(request: Request, authorization: str = Header(None)):
    who = _identity(request, authorization, allow_public=True)
    out = dict(who)
    if who["role"] == "desk":
        # The desk link belongs to no one broker, so the form asks who is using it.
        out["display_name"] = "Registration desk"
        out["ask_broker_code"] = True
        out["directory"] = db.available()
    elif who["role"] == "broker":
        b = db.broker(who["subject"])
        out["display_name"] = (b or {}).get("name") or who["subject"]
        out["ask_broker_code"] = False
        out["directory"] = db.available()
    else:
        # Admin and door. They are not a broker, so they must still say which
        # broker an invitation is for — exactly like the public desk. Setting
        # this False hid the broker code field from an administrator who opened
        # the registration page, leaving a form that could not be submitted.
        # Only a per-broker link knows the code without being told.
        out["display_name"] = who["subject"]
        out["ask_broker_code"] = True
        out["directory"] = db.available()
    out["admin_password_set"] = auth.admin_password_set()
    out["channels"] = {"whatsapp": wati.configured(), "email": mailer.configured()}
    return out


class AdminLogin(BaseModel):
    password: str


@app.post("/api/admin/login")
def admin_login(body: AdminLogin, request: Request, response: Response):
    if not auth.admin_password_set():
        raise HTTPException(503, "No admin password is configured on the server")
    if not auth.check_admin_password(body.password):
        raise HTTPException(401, "That password is not right")
    token = auth.issue("admin", "Administrator", 30)
    # httponly so a script on the page cannot read it; samesite=lax so it
    # survives a normal click-through but not a cross-site form post.
    #
    # secure follows the actual scheme rather than being hardcoded. In
    # production that is always https, so the flag is set; over plain http a
    # hardcoded Secure means the browser accepts the cookie and never sends it
    # back, and the sign-in appears to work while nothing is signed in.
    # uvicorn runs with --proxy-headers, so this is the scheme the client used,
    # not the one nginx used to reach us.
    https = request.url.scheme == "https" or \
        request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"
    response.set_cookie("jsladmin", token, httponly=True, samesite="lax",
                        secure=https, max_age=30 * 86400, path="/")
    return {"ok": True}


@app.post("/api/admin/logout")
def admin_logout(response: Response):
    response.delete_cookie("jsladmin", path="/")
    return {"ok": True}


@app.get("/api/events")
def events(request: Request, authorization: str = Header(None)):
    _identity(request, authorization, allow_public=True)
    now = dt.datetime.now(dt.timezone.utc)
    details = db.all_event_details()
    out = []
    for s in pretix.subevents():
        try:
            past = dt.datetime.fromisoformat(s["date_from"].replace("Z", "+00:00")) < now
        except Exception:
            past = False
        d = details.get(s["id"], {})
        out.append({**s, "when": when_text(s["date_from"]), "past": past,
                    "speaker": d.get("speaker") or "",
                    "speaker_title": d.get("speaker_title") or "",
                    "speaker_org": d.get("speaker_org") or "",
                    "note": d.get("note") or "",
                    "description": d.get("description") or ""})
    return out


@app.get("/api/clients")
def clients(request: Request, q: str = Query("", max_length=80),
            broker: str = Query("", max_length=32),
            authorization: str = Header(None)):
    """Suggestions are always scoped to one broker's own book.

    A personal link carries the code; the shared desk link passes the code the
    broker typed. Either way a broker never sees another broker's clients.
    """
    who = _need(_identity(request, authorization, allow_public=True), "desk", "broker")
    code = who["subject"] if who["role"] == "broker" and not who.get("public") \
        else broker.strip().upper()
    if not code:
        return []
    return db.search_clients(code, q)


def _already_registered(subevent_id, name, phone):
    """The reference this person already holds for this event, or None.

    A duplicate is the same NAME on the same NUMBER — never the number alone.
    Of the six repeated numbers in the real guest list, three were couples
    sharing a handset: Dhaval and Sheetal Shah, Kaushik and Artiben Shah,
    Naresh and Vaishaliben Patel. Blocking on the number would have turned
    away six real guests in order to catch two duplicates.

    Names are compared loosely, because the same broker typing the same client
    in twice is precisely the case this exists for.

    Only phone is matched. Email is not a pretix question — it lives on the
    order rather than the answers — so a registration made with an email and
    no number is not checked. Saying so beats a branch that looks like it
    works and never fires.
    """
    key = " ".join((name or "").lower().split())
    if not key or not phone:
        return None
    try:
        ident = {v: k for k, v in pretix.questions().items()}
        dead = pretix.order_status()
        for p in pretix.positions(subevent_id=subevent_id):
            if p.get("canceled") or dead.get(p.get("order")) in ("c", "e"):
                continue
            if " ".join((p.get("attendee_name") or "").lower().split()) != key:
                continue
            a = {ident.get(x["question"]): x["answer"] for x in p.get("answers", [])}
            if re.sub(r"\D", "", a.get("client_phone") or "")[-10:] == phone:
                return p.get("order")
    except Exception as e:
        print(f"duplicate check skipped ({type(e).__name__}: {str(e)[:150]})",
              flush=True)
    return None


class Registration(BaseModel):
    subevent: int
    name: str
    phone: str = ""
    email: str = ""
    broker_code: str = ""


@app.post("/api/register")
def register(body: Registration, request: Request, authorization: str = Header(None)):
    who = _need(_identity(request, authorization, allow_public=True),
                "desk", "broker", "admin")

    # A personal link already knows whose registration this is. The shared desk
    # link does not, so the broker states it and it is recorded as given.
    if who["role"] == "broker" and not who.get("public"):
        broker_code = who["subject"]
    else:
        broker_code = (body.broker_code or "").strip().upper()
        if not broker_code:
            raise HTTPException(400, "Enter your broker code")
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9/_-]{1,15}", broker_code):
            raise HTTPException(400, "That broker code does not look right")

    name = " ".join((body.name or "").split())
    if len(name) < 2:
        raise HTTPException(400, "Enter the client's name")

    phone = normalise_mobile(body.phone)
    email = (body.email or "").strip().lower() or None
    # Order matters: someone who mistyped a digit must be told the number is
    # wrong, not asked for the number they just typed.
    if body.phone.strip() and not phone:
        raise HTTPException(
            400, "That is not a valid Indian mobile number — 10 digits "
                 "starting 6, 7, 8 or 9")
    if not phone and not email:
        raise HTTPException(
            400, "Give a mobile number or an email address — without one of "
                 "them the client cannot be sent their pass")

    # Already registered? Refuse rather than send a second pass.
    #
    # Matched on phone AND name, never phone alone. Of the six repeated numbers
    # in the real list, three were couples sharing a handset — Dhaval and
    # Sheetal Shah, Kaushik and Artiben Shah, Naresh and Vaishaliben Patel.
    # Blocking on the number would have turned away six real guests to catch
    # two duplicates.
    #
    # Fails open. This prevents a wasted message; it is not worth refusing a
    # registration over. If the lookup breaks, the broker gets today's
    # behaviour rather than an error.
    existing = _already_registered(body.subevent, name, phone)
    if existing:
        raise HTTPException(409, f"{name} is already registered for this event "
                                 f"— reference {existing}. The pass was sent "
                                 f"when they were first registered.")

    ev = pretix.subevent(body.subevent)
    details = db.event_details(body.subevent)
    # What the client will actually read, speaker included. Returned as well as
    # the bare name so the reply can be checked against what went out.
    sent_as = event_line(ev["name"], details)
    order = pretix.create_order(name=name, phone=f"+91{phone}" if phone else "",
                                email=email, broker_code=broker_code,
                                subevent_id=body.subevent)

    qr = qr_url_for(order["secret"])
    delivery = {}
    if phone:
        ok, detail = wati.send_pass(
            phone=f"91{phone}", name=name,
            event=sent_as,
            when=when_text(ev["date_from"]), venue=ev["location"],
            reference=order["code"], qr_url=qr)
        delivery["whatsapp"] = {"ok": ok, "detail": detail}
    if email:
        ok, detail = mailer.send_pass(
            to_email=email, to_name=name, event=ev["name"],
            when=when_text(ev["date_from"]), venue=ev["location"],
            reference=order["code"], qr_url=qr, details=details)
        delivery["email"] = {"ok": ok, "detail": detail}

    if who["role"] in ("desk", "broker"):
        db.remember_client(broker_code, name, phone, email)

    # So the admin can answer "did their message go out" tomorrow, not only in
    # the second after the broker pressed submit.
    db.record_delivery(order["code"], subevent_id=body.subevent,
                       broker_code=broker_code, name=name, phone=phone,
                       email=email, delivery=delivery)

    return {"reference": order["code"], "name": name, "event": ev["name"],
            "event_line": sent_as,
            "when": when_text(ev["date_from"]), "broker_code": broker_code,
            "phone": phone or "", "email": email or "", "delivery": delivery}


@app.get("/api/overview")
def overview(request: Request, authorization: str = Header(None)):
    """One card per event: what it is, and how it is going.

    Counted from pretix rather than from our own rows, because pretix is what
    the door actually scans against.
    """
    who = _need(_identity(request, authorization), "admin")
    ident = {v: k for k, v in pretix.questions().items()}
    details = db.all_event_details()
    dead = pretix.order_status()
    keen = db.interest_counts()
    cards = []
    for ev in pretix.subevents():
        rows = [p for p in pretix.positions(subevent_id=ev["id"])
                if not p.get("canceled") and dead.get(p.get("order")) not in ("c", "e")]
        sent = db.deliveries_for(ev["id"])
        attended = sum(1 for p in rows if p.get("checkins"))
        brokers = set()
        for p in rows:
            a = {ident.get(x["question"]): x["answer"] for x in p.get("answers", [])}
            if a.get("broker_code"):
                brokers.add(a["broker_code"])
        d = details.get(ev["id"], {})
        cards.append({
            **ev,
            "when": when_text(ev["date_from"]),
            "speaker": d.get("speaker") or "",
            "speaker_title": d.get("speaker_title") or "",
            "speaker_org": d.get("speaker_org") or "",
            "registered": len(rows),
            "attended": attended,
            "brokers": len(brokers),
            "whatsapp_sent": sum(1 for r in sent.values() if r.get("whatsapp_ok")),
            "whatsapp_failed": sum(1 for r in sent.values()
                                   if r.get("whatsapp_ok") is False),
            "email_sent": sum(1 for r in sent.values() if r.get("email_ok")),
            "email_failed": sum(1 for r in sent.values() if r.get("email_ok") is False),
            "interested": keen.get(ev["id"], 0),
            "rate": round(100 * attended / len(rows)) if rows else 0,
        })
    cards.sort(key=lambda c: c["date_from"])
    return {"admin": who["subject"], "events": cards}


@app.get("/api/stats")
def stats(request: Request, subevent: int = Query(...),
          authorization: str = Header(None)):
    who = _identity(request, authorization)
    ident = {v: k for k, v in pretix.questions().items()}
    rows = pretix.positions(subevent_id=subevent)
    sent = db.deliveries_for(subevent)
    dead = pretix.order_status()
    # Filled by scripts/read-interest.py, never by calling WATI here. Drawing
    # a page must not depend on someone else's API being up.
    keen = db.interest_for(subevent)

    scope = who["subject"] if who["role"] == "broker" else None
    per, total, attended, rsvp = {}, 0, 0, {"yes": 0, "no": 0, "none": 0}
    people = []

    for p in rows:
        # Cancelled and expired orders are gone: their pass will not scan, so
        # counting them would report a turnout nobody can reconcile.
        if p.get("canceled") or dead.get(p.get("order")) in ("c", "e"):
            continue
        answers = {ident.get(a["question"]): a["answer"] for a in p.get("answers", [])}
        code = answers.get("broker_code") or "—"
        if scope and code != scope:
            continue
        came = bool(p.get("checkins"))
        total += 1
        attended += came
        state = (answers.get("rsvp_status") or "none").lower()
        rsvp[state if state in rsvp else "none"] += 1
        b = per.setdefault(code, {"broker": code, "registered": 0, "attended": 0})
        b["registered"] += 1
        b["attended"] += came
        d = sent.get(p.get("order")) or {}
        k = keen.get(p.get("order")) or {}
        people.append({"name": p.get("attendee_name") or "—",
                       "reference": p.get("order"),
                       "broker": code,
                       "phone": answers.get("client_phone") or "",
                       "email": d.get("email") or "",
                       "rsvp": state,
                       "attended": came,
                       # None means we have no record either way — an order
                       # created before this table existed, or by hand.
                       "whatsapp_ok": d.get("whatsapp_ok"),
                       "whatsapp_detail": d.get("whatsapp_detail") or "",
                       "email_ok": d.get("email_ok"),
                       "email_detail": d.get("email_detail") or "",
                       "interested": bool(k),
                       "interest_at": (k.get("replied_at").isoformat()
                                       if k.get("replied_at") else ""),
                       "interest_text": k.get("body") or ""})

    interested = sum(1 for pp in people if pp["interested"])
    names = db.broker_names([c for c in per if c != "—"])
    for code, b in per.items():
        b["name"] = names.get(code, code)
        b["no_show"] = b["registered"] - b["attended"]
        b["failed"] = sum(1 for pp in people if pp["broker"] == code
                          and (pp["whatsapp_ok"] is False or pp["email_ok"] is False))
        b["rate"] = round(100 * b["attended"] / b["registered"]) if b["registered"] else 0

    people.sort(key=lambda r: (not r["attended"], r["name"]))
    ev = pretix.subevent(subevent)
    ev.update({k: v or "" for k, v in db.event_details(subevent).items()
               if k in db.DETAIL_FIELDS})
    return {"scope": who["role"], "subevent": ev,
            "total": total, "attended": attended, "no_show": total - attended,
            "interested": interested,
            "rate": round(100 * attended / total) if total else 0,
            "rsvp": rsvp,
            "sent_ok": sum(1 for p in people
                           if p["whatsapp_ok"] or p["email_ok"]),
            "sent_failed": sum(1 for p in people
                               if p["whatsapp_ok"] is False or p["email_ok"] is False),
            "brokers": sorted(per.values(), key=lambda b: -b["registered"]),
            "people": people}


class Scan(BaseModel):
    code: str
    list_id: int


@app.post("/api/checkin")
def checkin(body: Scan, request: Request, authorization: str = Header(None)):
    _need(_identity(request, authorization), "door", "admin")
    raw = (body.code or "").strip()
    if not raw:
        raise HTTPException(400, "Nothing scanned")

    # A 32-character secret is a scan; anything shorter is a reference someone
    # typed because the scan would not read.
    if len(raw) >= 24:
        return pretix.redeem(body.list_id, raw)

    found = pretix.find_by_code(raw)
    if not found:
        return {"status": "error", "reason": "not_found"}
    if len(found) > 1:
        return {"status": "error", "reason": "ambiguous",
                "matches": [{"name": p.get("attendee_name"), "order": p.get("order")}
                            for p in found]}
    return pretix.redeem(body.list_id, found[0]["secret"])


@app.get("/api/checkinlists")
def lists(request: Request, authorization: str = Header(None)):
    _need(_identity(request, authorization), "door", "admin")
    return [{"id": l["id"], "name": l["name"], "subevent": l.get("subevent")}
            for l in pretix.checkin_lists()]


class Cancel(BaseModel):
    code: str = ""


@app.post("/api/admin/cancel")
def admin_cancel(body: Cancel, request: Request, authorization: str = Header(None)):
    """Undo a registration — a test one, or a broker's mistake.

    The only write the dashboard can make, and admin-only. Without it the fix
    for a wrong registration is a trip through pretix's own admin, which is not
    a thing anyone at JSL should have to learn on the day.
    """
    _need(_identity(request, authorization), "admin")
    code = (body.code or "").strip().upper()
    if not code:
        raise HTTPException(400, "Give the reference to cancel")
    if not pretix.cancel_order(code):
        raise HTTPException(400, f"{code} could not be cancelled — look it up in pretix")
    db.forget_delivery(code)
    return {"cancelled": code}


@app.exception_handler(HTTPException)
def _http_error(request: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
