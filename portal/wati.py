"""Sending the event pass over WhatsApp.

The template `jsl_event_v3` is approved with a media header whose entire URL is
the variable `qr_url`, so each recipient gets their own QR from one call. The
parameter names below were read back from getMessageTemplates, not assumed —
the body text stores positional tokens while the API addresses them by name.
"""
import httpx

import config

REQUIRED_PARAMS = ("name", "event", "datetime", "venue", "reference", "qr_url")


def configured():
    return bool(config.WATI_BASE and config.WATI_TOKEN)


def send_pass(*, phone, name, event, when, venue, reference, qr_url,
              template=None, broadcast_prefix="pass"):
    """Returns (ok, detail). Never raises — a messaging failure must not undo
    a registration that pretix has already accepted.

    The reminder rides the same six parameters as the confirmation, so it is
    the same call with a different template name rather than a second sender.
    """
    if not configured():
        return False, "WATI is not configured"
    body = {
        "template_name": template or config.WATI_TEMPLATE,
        "broadcast_name": f"{broadcast_prefix}_{reference}",
        "parameters": [
            {"name": "name", "value": name},
            {"name": "event", "value": event},
            {"name": "datetime", "value": when},
            {"name": "venue", "value": venue},
            {"name": "reference", "value": reference},
            {"name": "qr_url", "value": qr_url},
        ],
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
