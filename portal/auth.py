"""Stateless signed links.

Brokers are not given passwords. They receive a personal link over WhatsApp and
that link is the sign-in, which is the lowest friction we can offer someone who
registers two clients a month from a phone. The same scheme carries admin and
door-staff links, separated by role.

A token is  role.subject.expiry.signature  — no server-side session, so a
restart never logs anybody out and there is nothing to leak from a store.

Two broker shapes, because the directory is not loaded yet:

  desk    one link shared by every broker, who types their own code
  broker  a personal link that already knows the code, for once the
          client list exists and the form can fill itself in
"""
import base64
import hashlib
import hmac
import os
import time

SECRET = os.environ.get("BROKER_LINK_SECRET", "")
ROLES = ("desk", "broker", "admin", "door")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _sign(payload: str) -> str:
    if not SECRET:
        raise RuntimeError("BROKER_LINK_SECRET is unset; refusing to sign")
    mac = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).digest()
    return _b64(mac)[:32]


def issue(role: str, subject: str, ttl_days: int = 180) -> str:
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    expiry = int(time.time()) + ttl_days * 86400
    payload = f"{role}.{subject}.{expiry}"
    return f"{payload}.{_sign(payload)}"


def verify(token: str):
    """Return {'role','subject'} or None. Never raises on malformed input."""
    if not token or token.count(".") != 3:
        return None
    role, subject, expiry, sig = token.split(".")
    if role not in ROLES:
        return None
    payload = f"{role}.{subject}.{expiry}"
    try:
        expected = _sign(payload)
    except RuntimeError:
        return None
    # Constant time: a timing oracle here would let someone grind out a
    # signature one byte at a time.
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        if int(expiry) < time.time():
            return None
    except ValueError:
        return None
    return {"role": role, "subject": subject, "expires": int(expiry)}
