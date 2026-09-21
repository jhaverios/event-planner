"""Environment, read once at import so a missing value fails at boot."""
import os


def _req(name):
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"{name} is required")
    return v


PRETIX_BASE = os.environ.get("PRETIX_API_BASE", "http://pretix:80/api/v1").rstrip("/")
# pretix answers 400 "Unknown host" when the Host header does not match the url
# it was configured with, so every request has to carry it explicitly.
PRETIX_HOST = os.environ.get("PRETIX_HOST", "localhost")
PRETIX_TOKEN = os.environ.get("PRETIX_API_TOKEN", "")
PRETIX_ORGANIZER = os.environ.get("PRETIX_ORGANIZER", "jsl")
PRETIX_EVENT = os.environ.get("PRETIX_EVENT", "investor-events")

WATI_BASE = os.environ.get("WATI_API_BASE", "").rstrip("/")
WATI_TOKEN = os.environ.get("WATI_TOKEN", "")
WATI_TEMPLATE = os.environ.get("WATI_TEMPLATE", "jsl_event_v3")

DIRECTORY_DSN = os.environ.get("DIRECTORY_DSN", "")

# Where a client's QR is fetched from. Until events.jslwealth.in exists this
# points at a public QR renderer; the template stores a bare variable, so
# swapping it needs no new Meta approval.
QR_BASE = os.environ.get(
    "QR_BASE",
    "https://api.qrserver.com/v1/create-qr-code/?size=600x600&margin=20&data=")

PORTAL_BASE = os.environ.get("PORTAL_BASE", "http://localhost:8090").rstrip("/")
CONSENT_VERSION = os.environ.get("CONSENT_VERSION", "notice-v1-2026-09-21")

# ZeptoMail. The send key is the same value as the SMTP password; the API
# takes it as "Zoho-enczapikey <key>".
ZEPTO_KEY = os.environ.get("ZEPTO_API_KEY") or os.environ.get("ZEPTO_SMTP_PASS", "")
MAIL_FROM = os.environ.get("MAIL_FROM", "")
MAIL_FROM_NAME = os.environ.get("MAIL_FROM_NAME", "Jhaveri Securities")
