"""The pass by email, through ZeptoMail's HTTPS API.

The API rather than SMTP because outbound 587 and 465 are blocked from the
build environment, and because an HTTP status is easier to act on than an SMTP
conversation. The India data centre is not a guess: the same token validates on
api.zeptomail.in and returns 401 on the .com host.
"""
import html

import httpx

import config

API = "https://api.zeptomail.in/v1.1/email"


def configured():
    return bool(config.ZEPTO_KEY and config.MAIL_FROM)


def send_pass(*, to_email, to_name, event, when, venue, reference, qr_url,
              details=None):
    if not configured():
        return False, "ZeptoMail is not configured"
    if not to_email:
        return False, "no email address"
    body = {
        "from": {"address": config.MAIL_FROM, "name": config.MAIL_FROM_NAME},
        "to": [{"email_address": {"address": to_email, "name": to_name}}],
        "subject": f"Your pass — {event}",
        "htmlbody": _html(to_name, event, when, venue, reference, qr_url,
                          details or {}),
    }
    try:
        r = httpx.post(API, json=body, timeout=25.0,
                       headers={"Authorization": f"Zoho-enczapikey {config.ZEPTO_KEY}",
                                "Content-Type": "application/json"})
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    if r.status_code >= 400:
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    return True, "accepted"


def _html(name, event, when, venue, reference, qr_url, details):
    e = html.escape

    def row(label, value):
        return (f'<tr><td style="color:#5b6b78;padding:4px 14px 4px 0;'
                f'white-space:nowrap;vertical-align:top;">{label}</td>'
                f'<td>{e(value)}</td></tr>') if value else ""

    # The speaker is usually the reason somebody comes, so it sits with the
    # event rather than below the fold. Title and firm on one line under it.
    speaker = details.get("speaker") or ""
    credit = ", ".join(x for x in (details.get("speaker_title"),
                                   details.get("speaker_org")) if x)
    speaker_row = ""
    if speaker:
        sub = (f'<div style="color:#5b6b78;font-size:12.5px;">{e(credit)}</div>'
               if credit else "")
        speaker_row = (f'<tr><td style="color:#5b6b78;padding:4px 14px 4px 0;'
                       f'white-space:nowrap;vertical-align:top;">Speaker</td>'
                       f'<td>{e(speaker)}{sub}</td></tr>')
    note = details.get("note") or ""
    # Inline styles only: every mail client strips a stylesheet. Navy and the
    # serif face are the house tokens, so the mail reads as the portal does.
    return f"""<!doctype html><html><body style="margin:0;background:#f7f8f9;
 font-family:'Iowan Old Style',Palatino,Georgia,serif;color:#16212b;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table role="presentation" width="100%" style="max-width:520px;margin:24px auto;
 background:#ffffff;border:1px solid #e3e7ea;border-radius:10px;" cellpadding="0" cellspacing="0">
<tr><td style="background:#16294d;color:#ffffff;padding:16px 22px;border-radius:9px 9px 0 0;
 font-size:17px;font-weight:600;">Jhaveri Securities</td></tr>
<tr><td style="padding:22px;">
<p style="margin:0 0 16px;font-size:16px;">Hello {e(name)}, your registration is confirmed.</p>
<table role="presentation" cellpadding="0" cellspacing="0" style="font-size:14px;width:100%;">
{row("Event", event)}
{speaker_row}
{row("Date and time", when)}
{row("Venue", venue)}
<tr><td style="color:#5b6b78;padding:4px 14px 4px 0;white-space:nowrap;">Reference</td>
    <td style="font-variant-numeric:tabular-nums;font-weight:600;">{e(reference)}</td></tr>
</table>
{f'<p style="margin:14px 0 0;font-size:13px;color:#5b6b78;">{e(note)}</p>' if note else ''}
<div style="text-align:center;margin:22px 0 6px;">
  <img src="{e(qr_url)}" alt="Entry QR code" width="240" height="240"
       style="border:1px solid #e3e7ea;border-radius:8px;"></div>
<p style="margin:6px 0 0;font-size:13px;color:#5b6b78;text-align:center;">
  Show this code at the entrance, or quote reference {e(reference)}.</p>
</td></tr>
<tr><td style="border-top:1px solid #e3e7ea;padding:13px 22px;font-size:11.5px;color:#5b6b78;">
  You are receiving this because a Jhaveri Securities representative registered you for this event.
</td></tr></table></td></tr></table></body></html>"""
