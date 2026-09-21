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

def _identity(request: Request, authorization: str = None):
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:]
    token = token or request.query_params.get("t") or request.cookies.get("jslt")
    who = auth.verify(token) if token else None
    if not who:
        raise HTTPException(401, "link expired or not valid")
    return who


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


# ---------------------------------------------------------------- screens

@app.get("/")
def root(request: Request):
    who = _identity(request)
    page = {"desk": "/r", "broker": "/r", "admin": "/admin",
            "door": "/door"}[who["role"]]
    t = request.query_params.get("t", "")
    return RedirectResponse(f"{page}?t={t}" if t else page)


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
    checks = {
        "pretix_token": bool(config.PRETIX_TOKEN),
        "directory": db.available(),
        "whatsapp": wati.configured(),
        "email": mailer.configured(),
        "link_secret": bool(auth.SECRET),
    }
    missing = [k for k, v in checks.items() if not v]
    return {"status": "ok" if not missing else "degraded",
            "checks": checks, "missing": missing}


@app.get("/api/session")
def session(request: Request, authorization: str = Header(None)):
    who = _identity(request, authorization)
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
        out["display_name"] = who["subject"]
        out["ask_broker_code"] = False
    out["channels"] = {"whatsapp": wati.configured(), "email": mailer.configured()}
    return out


@app.get("/api/events")
def events(request: Request, authorization: str = Header(None)):
    _identity(request, authorization)
    now = dt.datetime.now(dt.timezone.utc)
    out = []
    for s in pretix.subevents():
        try:
            past = dt.datetime.fromisoformat(s["date_from"].replace("Z", "+00:00")) < now
        except Exception:
            past = False
        out.append({**s, "when": when_text(s["date_from"]), "past": past})
    return out


@app.get("/api/clients")
def clients(request: Request, q: str = Query("", max_length=80),
            broker: str = Query("", max_length=32),
            authorization: str = Header(None)):
    """Suggestions are always scoped to one broker's own book.

    A personal link carries the code; the shared desk link passes the code the
    broker typed. Either way a broker never sees another broker's clients.
    """
    who = _need(_identity(request, authorization), "desk", "broker")
    code = who["subject"] if who["role"] == "broker" else broker.strip().upper()
    if not code:
        return []
    return db.search_clients(code, q)


class Registration(BaseModel):
    subevent: int
    name: str
    phone: str = ""
    email: str = ""
    broker_code: str = ""


@app.post("/api/register")
def register(body: Registration, request: Request, authorization: str = Header(None)):
    who = _need(_identity(request, authorization), "desk", "broker", "admin")

    # A personal link already knows whose registration this is. The shared desk
    # link does not, so the broker states it and it is recorded as given.
    if who["role"] == "broker":
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

    ev = pretix.subevent(body.subevent)
    order = pretix.create_order(name=name, phone=f"+91{phone}" if phone else "",
                                email=email, broker_code=broker_code,
                                subevent_id=body.subevent)

    qr = qr_url_for(order["secret"])
    delivery = {}
    if phone:
        ok, detail = wati.send_pass(
            phone=f"91{phone}", name=name, event=ev["name"],
            when=when_text(ev["date_from"]), venue=ev["location"],
            reference=order["code"], qr_url=qr)
        delivery["whatsapp"] = {"ok": ok, "detail": detail}
    if email:
        ok, detail = mailer.send_pass(
            to_email=email, to_name=name, event=ev["name"],
            when=when_text(ev["date_from"]), venue=ev["location"],
            reference=order["code"], qr_url=qr)
        delivery["email"] = {"ok": ok, "detail": detail}

    if who["role"] in ("desk", "broker"):
        db.remember_client(broker_code, name, phone, email)

    return {"reference": order["code"], "name": name, "event": ev["name"],
            "when": when_text(ev["date_from"]), "broker_code": broker_code,
            "phone": phone or "", "email": email or "", "delivery": delivery}


@app.get("/api/stats")
def stats(request: Request, subevent: int = Query(...),
          authorization: str = Header(None)):
    who = _identity(request, authorization)
    ident = {v: k for k, v in pretix.questions().items()}
    rows = pretix.positions(subevent_id=subevent)

    scope = who["subject"] if who["role"] == "broker" else None
    per, total, attended, rsvp = {}, 0, 0, {"yes": 0, "no": 0, "none": 0}
    people = []

    for p in rows:
        if p.get("canceled"):
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
        people.append({"name": p.get("attendee_name") or "—",
                       "reference": p.get("order"),
                       "broker": code,
                       "phone": answers.get("client_phone") or "",
                       "rsvp": state,
                       "attended": came})

    names = db.broker_names([c for c in per if c != "—"])
    for code, b in per.items():
        b["name"] = names.get(code, code)
        b["no_show"] = b["registered"] - b["attended"]
        b["rate"] = round(100 * b["attended"] / b["registered"]) if b["registered"] else 0

    people.sort(key=lambda r: (not r["attended"], r["name"]))
    return {"scope": who["role"], "subevent": pretix.subevent(subevent),
            "total": total, "attended": attended, "no_show": total - attended,
            "rate": round(100 * attended / total) if total else 0,
            "rsvp": rsvp,
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


@app.exception_handler(HTTPException)
def _http_error(request: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
