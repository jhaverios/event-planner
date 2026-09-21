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


def send_pass(*, phone, name, event, when, venue, reference, qr_url):
    """Returns (ok, detail). Never raises — a messaging failure must not undo
    a registration that pretix has already accepted."""
    if not configured():
        return False, "WATI is not configured"
    body = {
        "template_name": config.WATI_TEMPLATE,
        "broadcast_name": f"pass_{reference}",
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
