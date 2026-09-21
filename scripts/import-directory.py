#!/usr/bin/env python3
"""Load a broker/client CSV into the directory database.

The file JSL will send has not been seen yet, so column names are matched
loosely rather than fixed. Run with --dry-run first: it reports exactly what
it matched and what it would skip, without writing anything.

  python3 scripts/import-directory.py clients.csv --dry-run
  python3 scripts/import-directory.py clients.csv
"""
import argparse
import csv
import hashlib
import os
import re
import sys
import unicodedata

try:
    import psycopg2
except ImportError:
    sys.exit("psycopg2 missing: pip install psycopg2-binary")

# Header aliases, lowercased and stripped of non-letters before matching.
ALIASES = {
    "broker_code":  ["brokercode", "broker", "code", "rmcode", "arn", "arncode",
                     "empcode", "employeecode", "advisorcode", "partnercode"],
    "broker_name":  ["brokername", "rmname", "relationshipmanager", "advisorname",
                     "partnername", "rm"],
    "broker_email": ["brokeremail", "rmemail", "advisoremail"],
    "broker_phone": ["brokerphone", "brokermobile", "rmmobile", "rmphone"],
    "client_name":  ["clientname", "name", "investorname", "customername",
                     "clientfullname", "fullname"],
    "client_phone": ["clientphone", "phone", "mobile", "mobileno", "mobilenumber",
                     "contact", "contactno", "phoneno", "whatsapp", "whatsappnumber"],
    "client_email": ["clientemail", "email", "emailid", "emailaddress", "mail"],
}


def norm_header(h):
    return re.sub(r"[^a-z]", "", (h or "").strip().lower())


def map_columns(fieldnames):
    """Return {canonical: actual_header}. First match wins, in alias order."""
    seen = {norm_header(f): f for f in fieldnames if f}
    out = {}
    for canonical, aliases in ALIASES.items():
        for candidate in [norm_header(canonical)] + aliases:
            if candidate in seen and seen[candidate] not in out.values():
                out[canonical] = seen[candidate]
                break
    return out


def clean_text(v):
    if v is None:
        return None
    v = unicodedata.normalize("NFKC", str(v)).strip()
    v = re.sub(r"\s+", " ", v)
    return v or None


def clean_phone(v):
    """Indian mobile to bare 10 digits. Returns (value, reason_if_rejected)."""
    if not v:
        return None, None
    digits = re.sub(r"\D", "", str(v))
    for prefix in ("0091", "91", "0"):
        if len(digits) > 10 and digits.startswith(prefix):
            digits = digits[len(prefix):]
            break
    if not digits:
        return None, None
    if len(digits) != 10:
        return None, f"phone {v!r} is {len(digits)} digits, not 10"
    if digits[0] not in "6789":
        return None, f"phone {v!r} does not start 6-9"
    return digits, None


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def clean_email(v):
    v = clean_text(v)
    if not v:
        return None, None
    v = v.lower()
    if not EMAIL_RE.match(v):
        return None, f"email {v!r} is not a valid address"
    return v, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csvfile")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dsn", default=os.environ.get("DIRECTORY_DSN"))
    ap.add_argument("--encoding", default="utf-8-sig")
    args = ap.parse_args()

    if not args.dry_run and not args.dsn:
        sys.exit("set DIRECTORY_DSN or pass --dsn")

    raw = open(args.csvfile, "rb").read()
    sha = hashlib.sha256(raw).hexdigest()
    text = raw.decode(args.encoding, errors="replace")
    reader = csv.DictReader(text.splitlines())
    if not reader.fieldnames:
        sys.exit("no header row found")

    cols = map_columns(reader.fieldnames)
    print("Columns matched:")
    for k in ALIASES:
        print(f"  {k:<14} <- {cols.get(k) or '(none)'}")
    missing = [k for k in ("broker_code", "client_name") if k not in cols]
    if missing:
        sys.exit(f"\nCannot proceed: no column matched {missing}. "
                 f"Headers present: {reader.fieldnames}")

    brokers, clients, skipped = {}, [], []
    seen_keys = set()
    rows = 0
    for lineno, row in enumerate(reader, start=2):
        rows += 1
        bcode = clean_text(row.get(cols["broker_code"]))
        cname = clean_text(row.get(cols["client_name"]))
        if not bcode:
            skipped.append((lineno, "no broker code"))
            continue
        bcode = bcode.upper()
        if not cname:
            skipped.append((lineno, "no client name"))
            continue

        phone, perr = clean_phone(row.get(cols["client_phone"])) if "client_phone" in cols else (None, None)
        email, eerr = clean_email(row.get(cols["client_email"])) if "client_email" in cols else (None, None)
        for err in (perr, eerr):
            if err:
                skipped.append((lineno, err + " (row still loaded)"))

        b = brokers.setdefault(bcode, {"name": None, "email": None, "phone": None})
        if "broker_name" in cols and not b["name"]:
            b["name"] = clean_text(row.get(cols["broker_name"]))
        if "broker_email" in cols and not b["email"]:
            b["email"] = clean_email(row.get(cols["broker_email"]))[0]
        if "broker_phone" in cols and not b["phone"]:
            b["phone"] = clean_phone(row.get(cols["broker_phone"]))[0]

        key = (bcode, cname.lower(), phone or "")
        if key in seen_keys:
            skipped.append((lineno, f"duplicate of an earlier row: {cname}"))
            continue
        seen_keys.add(key)
        clients.append((bcode, cname, phone, email))

    print(f"\n{rows} data rows read")
    print(f"{len(brokers)} brokers, {len(clients)} clients to load")
    print(f"{len(skipped)} notes/skips")
    for lineno, why in skipped[:25]:
        print(f"  line {lineno}: {why}")
    if len(skipped) > 25:
        print(f"  ... and {len(skipped) - 25} more")

    with_phone = sum(1 for c in clients if c[2])
    with_email = sum(1 for c in clients if c[3])
    print(f"\n{with_phone}/{len(clients)} have a usable mobile (WhatsApp will reach these)")
    print(f"{with_email}/{len(clients)} have a usable email")

    if args.dry_run:
        print("\nDry run: nothing written.")
        return

    conn = psycopg2.connect(args.dsn)
    with conn, conn.cursor() as cur:
        for code, b in brokers.items():
            cur.execute(
                """INSERT INTO brokers (broker_code, name, email, phone)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (broker_code) DO UPDATE SET
                     name  = COALESCE(EXCLUDED.name,  brokers.name),
                     email = COALESCE(EXCLUDED.email, brokers.email),
                     phone = COALESCE(EXCLUDED.phone, brokers.phone)""",
                (code, b["name"] or code, b["email"], b["phone"]))
        for bcode, cname, phone, email in clients:
            cur.execute(
                """INSERT INTO clients (broker_code, name, phone, email)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (broker_code, lower(name), coalesce(phone, ''))
                   DO UPDATE SET email = COALESCE(EXCLUDED.email, clients.email)""",
                (bcode, cname, phone, email))
        cur.execute(
            """INSERT INTO import_batches
                 (filename, sha256, rows_seen, rows_loaded, rows_skipped, notes)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (os.path.basename(args.csvfile), sha, rows, len(clients), len(skipped),
             f"brokers={len(brokers)}"))
    conn.close()
    print(f"\nLoaded. sha256={sha[:16]}...")


if __name__ == "__main__":
    main()
