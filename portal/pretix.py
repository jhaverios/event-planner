"""The bits of the pretix API this portal uses.

Kept thin on purpose: pretix is the source of truth for registrations and
check-ins, and nothing here caches or second-guesses it.
"""
import httpx

import config

# Question identifiers, resolved once per process. The numeric ids differ
# between a fresh install and this one, so never hard-code them.
_questions = None


def _client():
    return httpx.Client(
        base_url=f"{config.PRETIX_BASE}/organizers/{config.PRETIX_ORGANIZER}"
                 f"/events/{config.PRETIX_EVENT}",
        headers={"Authorization": f"Token {config.PRETIX_TOKEN}",
                 "Host": config.PRETIX_HOST,
                 "Content-Type": "application/json"},
        timeout=20.0)


def questions():
    global _questions
    if _questions is None:
        with _client() as c:
            r = c.get("/questions/", params={"page_size": 100})
            r.raise_for_status()
            _questions = {q["identifier"]: q["id"] for q in r.json()["results"]}
    return _questions


def subevents():
    with _client() as c:
        r = c.get("/subevents/", params={"active": "true", "page_size": 100})
        r.raise_for_status()
        out = []
        for s in r.json()["results"]:
            out.append({
                "id": s["id"],
                "name": _localised(s["name"]),
                "date_from": s["date_from"],
                "location": _localised(s.get("location") or {}),
            })
        out.sort(key=lambda s: s["date_from"])
        return out


def subevent(sid):
    with _client() as c:
        r = c.get(f"/subevents/{sid}/")
        r.raise_for_status()
        s = r.json()
        return {"id": s["id"], "name": _localised(s["name"]),
                "date_from": s["date_from"],
                "location": _localised(s.get("location") or {})}


def _localised(v):
    if isinstance(v, dict):
        return v.get("en") or next(iter(v.values()), "")
    return v or ""


def create_order(*, name, phone, email, broker_code, subevent_id, item_id=1):
    q = questions()
    answers = [{"question": q["client_phone"], "answer": phone, "options": []},
               {"question": q["broker_code"], "answer": broker_code, "options": []},
               {"question": q["consent_given"], "answer": "true", "options": []},
               {"question": q["consent_version"], "answer": config.CONSENT_VERSION,
                "options": []}]
    body = {"status": "p", "payment_provider": "free", "locale": "en",
            "send_email": False,
            "positions": [{"positionid": 1, "item": item_id, "price": "0.00",
                           "attendee_name": name, "subevent": subevent_id,
                           "answers": answers}]}
    if email:
        body["email"] = email
    with _client() as c:
        r = c.post("/orders/", json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"pretix {r.status_code}: {r.text[:400]}")
        o = r.json()
        return {"code": o["code"],
                "secret": o["positions"][0]["secret"],
                "position_id": o["positions"][0]["id"]}


def cancel_order(code):
    """Cancel an order, so test registrations do not inflate the turnout.

    The endpoint is mark_canceled, American spelling and one L. /cancel/,
    /mark_cancelled/ and /delete/ all return 404, which made this look like a
    permissions problem rather than a wrong URL.
    """
    with _client() as c:
        r = c.post(f"/orders/{code}/mark_canceled/", json={})
        if r.status_code >= 400:
            return False
        # Read the order back rather than trusting the status code. A 200 that
        # did not actually cancel anything is exactly the sort of thing that
        # only shows up as a wrong headcount on the night.
        g = c.get(f"/orders/{code}/")
        return g.status_code < 400 and g.json().get("status") == "c"


def order_status():
    """{order_code: status} — n pending, p paid, e expired, c canceled.

    The position serializer's own cancellation flag is not the thing that
    changes when an order is cancelled: pretix marks the order and leaves the
    positions in place, so /orderpositions/ keeps returning a cancelled
    registration looking perfectly live. Filtering on the order's status is
    unambiguous, and at a couple of hundred orders it is one extra call.

    This must never raise. It refines a count; it does not produce the page.
    Letting it throw took the admin dashboard down with a 500 the day before
    the event, because two endpoints had been given a hard dependency on a new
    call with no fallback. Returning nothing degrades to the older behaviour —
    a cancelled order may be counted for one page load — which is a cosmetic
    wrong answer rather than no answer at all.
    """
    out, url, params = {}, "/orders/", {"page_size": 200}
    try:
        with _client() as c:
            while url:
                r = c.get(url, params=params)
                r.raise_for_status()
                d = r.json()
                for o in d["results"]:
                    out[o["code"]] = o.get("status")
                url, params = d.get("next"), None
    except Exception as e:
        print(f"pretix: could not read order statuses "
              f"({type(e).__name__}: {str(e)[:300]}) — "
              f"cancelled orders may be counted until this is fixed", flush=True)
        return {}
    return out


def positions(subevent_id=None):
    """Every registration with its answers and check-ins, paged out fully."""
    out, url, params = [], "/orderpositions/", {"page_size": 200}
    if subevent_id:
        params["subevent"] = subevent_id
    with _client() as c:
        while url:
            r = c.get(url, params=params)
            r.raise_for_status()
            d = r.json()
            out.extend(d["results"])
            url, params = d.get("next"), None
    return out


def checkin_lists():
    with _client() as c:
        r = c.get("/checkinlists/", params={"page_size": 100})
        r.raise_for_status()
        return r.json()["results"]


def redeem(list_id, secret):
    with _client() as c:
        r = c.post(f"/checkinlists/{list_id}/positions/{secret}/redeem/",
                   json={"source_type": "barcode", "type": "entry"})
        return r.json()


def find_by_code(code, subevent_id=None):
    """Door fallback: a reference typed in when a scan will not read."""
    params = {"order": code.strip().upper(), "page_size": 10}
    if subevent_id:
        params["subevent"] = subevent_id
    with _client() as c:
        r = c.get("/orderpositions/", params=params)
        r.raise_for_status()
        return r.json()["results"]
