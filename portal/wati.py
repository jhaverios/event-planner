"""Sending the event pass over WhatsApp.

The template `jsl_event_v3` is approved with a media header whose entire URL is
the variable `qr_url`, so each recipient gets their own QR from one call. The
parameter names below were read back from getMessageTemplates, not assumed —
the body text stores positional tokens while the API addresses them by name.
"""
import re

import httpx

import config

REQUIRED_PARAMS = ("name", "event", "datetime", "venue", "reference", "qr_url")


def configured():
    return bool(config.WATI_BASE and config.WATI_TOKEN)


BODY_PARAMS = ("name", "event", "datetime", "venue", "reference")
_param_cache = {}
_all_cache = {}
_header_cache = {}


def template_params(template):
    """Every parameter name WATI stores for a template, in order.

    Read back, never assumed — jsl_event_v3 names its five body slots
    name/event/datetime/venue/reference while jsl_event_reminder_v1, built in
    the same UI a day later, names them "1".."5". Assuming the words would have
    sent nothing to fifty-one guests.

    Returns [] when the template cannot be read, which callers treat as "use
    what you were told".
    """
    if template in _all_cache:
        return _all_cache[template]
    names = []
    try:
        r = httpx.get(f"{config.WATI_BASE}/api/v1/getMessageTemplates",
                      params={"pageSize": 80},
                      headers={"Authorization": f"Bearer {config.WATI_TOKEN}"},
                      timeout=25.0)
        r.raise_for_status()
        for t in r.json().get("messageTemplates", []):
            if t.get("elementName") == template:
                names = [p.get("paramName") or p.get("name")
                         for p in (t.get("customParams") or [])]
                names = [n for n in names if n]
                break
    except Exception as e:
        print(f"wati: could not read parameters for {template} "
              f"({type(e).__name__})", flush=True)
    _all_cache[template] = names
    return names


def header_param(template):
    """The name of the template's header variable, or None if it has none.

    Taken from the stored header link: "{{qr_url}}" means the header is filled
    by a parameter called qr_url. Assuming the name is always qr_url was wrong
    the first time it mattered — jsl_investment_event_followup stores
    "{{doc_url}}", and zipping three values against four slot names silently
    dropped the document, which WATI rejects outright.
    """
    if template in _header_cache:
        return _header_cache[template]
    name = None
    try:
        r = httpx.get(f"{config.WATI_BASE}/api/v1/getMessageTemplates",
                      params={"pageSize": 80},
                      headers={"Authorization": f"Bearer {config.WATI_TOKEN}"},
                      timeout=25.0)
        r.raise_for_status()
        for t in r.json().get("messageTemplates", []):
            if t.get("elementName") == template:
                link = ((t.get("header") or {}).get("link") or "").strip()
                m = re.fullmatch(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}", link)
                name = m.group(1) if m else None
                break
    except Exception as e:
        print(f"wati: could not read the header of {template} "
              f"({type(e).__name__})", flush=True)
    _header_cache[template] = name
    return name


def body_params(template):
    """The template's body slots, in order, with the header's own slot removed."""
    head = header_param(template)
    return [n for n in template_params(template) if n != head]


def send_template(*, phone, template, values, header_value=None,
                  broadcast_prefix="msg", reference=""):
    """Send any approved template. Returns (ok, detail); never raises.

    Slot names come from WATI, values from the caller, zipped in order. The
    header's own parameter is supplied separately because it is not part of
    the body and its name differs per template.
    """
    if not configured():
        return False, "WATI is not configured"
    names = body_params(template)
    if not names:                      # unreadable; fall back to the pass shape
        names = list(BODY_PARAMS)[:len(values)]
    params = [{"name": k, "value": v} for k, v in zip(names, values)]
    head = header_param(template)
    if head and header_value:
        params.append({"name": head, "value": header_value})
    body = {"template_name": template,
            "broadcast_name": f"{broadcast_prefix}_{reference or 'bulk'}",
            "parameters": params}
    try:
        r = httpx.post(f"{config.WATI_BASE}/api/v1/sendTemplateMessage",
                       params={"whatsappNumber": phone},
                       headers={"Authorization": f"Bearer {config.WATI_TOKEN}",
                                "Content-Type": "application/json"},
                       json=body, timeout=25.0)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    if r.status_code >= 400:
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    d = r.json()
    if d.get("result") is True:
        return True, "accepted"
    return False, str(d.get("info") or d)[:200]


def body_param_names(template):
    """The names WATI actually stores for a template's five body variables.

    Read back, never assumed. jsl_event_v3 stores them as name/event/datetime/
    venue/reference. jsl_event_reminder_v1, created through the same UI one day
    later, stores them as "1".."5" — WATI names them from the positional token
    when they are typed rather than inserted. Sending v3's names to the
    reminder would have delivered five blank lines to fifty-one guests.

    Falls back to the documented names if the lookup fails: a template we
    cannot read is more likely to be the usual shape than not, and refusing to
    send at all is the worse failure.
    """
    if template in _param_cache:
        return _param_cache[template]
    names = list(BODY_PARAMS)
    try:
        r = httpx.get(f"{config.WATI_BASE}/api/v1/getMessageTemplates",
                      params={"pageSize": 80},
                      headers={"Authorization": f"Bearer {config.WATI_TOKEN}"},
                      timeout=25.0)
        r.raise_for_status()
        for t in r.json().get("messageTemplates", []):
            if t.get("elementName") != template:
                continue
            got = [p.get("paramName") or p.get("name")
                   for p in (t.get("customParams") or [])]
            # qr_url is the header; whatever is left, in order, is the body.
            body = [g for g in got if g and g != "qr_url"]
            if len(body) >= len(BODY_PARAMS):
                names = body[:len(BODY_PARAMS)]
            break
    except Exception as e:
        print(f"wati: could not read parameter names for {template} "
              f"({type(e).__name__}) — assuming {names}", flush=True)
    _param_cache[template] = names
    return names


def send_pass(*, phone, name, event, when, venue, reference, qr_url,
              template=None, broadcast_prefix="pass"):
    """Returns (ok, detail). Never raises — a messaging failure must not undo
    a registration that pretix has already accepted.

    The reminder rides the same six parameters as the confirmation, so it is
    the same call with a different template name rather than a second sender.
    """
    if not configured():
        return False, "WATI is not configured"
    tpl = template or config.WATI_TEMPLATE
    keys = body_param_names(tpl)
    values = (name, event, when, venue, reference)
    body = {
        "template_name": tpl,
        "broadcast_name": f"{broadcast_prefix}_{reference}",
        "parameters": [{"name": k, "value": v} for k, v in zip(keys, values)]
                      + [{"name": "qr_url", "value": qr_url}],
    }
    try:
        r = httpx.post(
            f"{config.WATI_BASE}/api/v1/sendTemplateMessage",
            params={"whatsappNumber": phone},
            headers={"Authorization": f"Bearer {config.WATI_TOKEN}",
                     "Content-Type": "application/json"},
            json=body, timeout=25.0)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    if r.status_code >= 400:
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    d = r.json()
    # WATI answers 200 with result:false for a rejected send, so the status
    # code alone is not the answer.
    if d.get("result") is True:
        return True, "accepted"
    return False, str(d.get("info") or d)[:200]


def broadcast_allowed(phone):
    """Whether WATI will let a template go to this number.

    Returns (allowed, why). A contact with allowBroadcast off has asked not to
    be messaged in bulk, and sending anyway is both rude and a good way to lose
    a template. getContacts takes `name`, which matches the phone as well as
    the saved name — verified against a contact saved as "Jeet Jhaveri" and
    found by number alone. There are sixteen thousand contacts on this account,
    so this must be a lookup, never a scan.

    Unknown is treated as allowed: a lookup that fails must not silently stop a
    reminder anyone was expecting. The send itself is the real gate — WATI
    answers 200 with result:false when it refuses.
    """
    if not configured():
        return True, "WATI is not configured"
    try:
        r = httpx.get(f"{config.WATI_BASE}/api/v1/getContacts",
                      params={"pageSize": 1, "name": phone},
                      headers={"Authorization": f"Bearer {config.WATI_TOKEN}"},
                      timeout=20.0)
        r.raise_for_status()
        rows = r.json().get("contact_list") or []
    except Exception as e:
        return True, f"could not check ({type(e).__name__})"
    if not rows:
        return True, "no WATI contact yet"
    c = rows[0]
    if c.get("isDeleted"):
        return False, "contact deleted in WATI"
    if c.get("allowBroadcast") is False:
        return False, "broadcast turned off for this contact"
    return True, ""


def message_status(phone, limit=5):
    """Growth has no webhooks, so delivery is polled rather than pushed."""
    if not configured():
        return []
    try:
        r = httpx.get(f"{config.WATI_BASE}/api/v1/getMessages/{phone}",
                      params={"pageSize": limit},
                      headers={"Authorization": f"Bearer {config.WATI_TOKEN}"},
                      timeout=20.0)
        r.raise_for_status()
        d = r.json()
    except Exception:
        return []
    return (d.get("messages") or {}).get("items") or d.get("items") or []
