"""Directory lookups.

This is only the convenience layer that fills a broker's form in. Pretix stays
the source of truth for who is registered; nothing here is consulted at the
door or in a report.
"""
import contextlib

import psycopg2
import psycopg2.errors
import psycopg2.extras
import psycopg2.pool

import config

_pool = None


def init():
    global _pool
    if config.DIRECTORY_DSN and _pool is None:
        _pool = psycopg2.pool.SimpleConnectionPool(1, 8, config.DIRECTORY_DSN)


def available():
    return _pool is not None


# Every table the portal writes to. Kept here rather than inferred, so a table
# added to the schema file but never created on a running machine is caught.
EXPECTED_TABLES = ("brokers", "clients", "import_batches", "event_details",
                   "deliveries", "reminders")


def missing_tables():
    """Which expected tables are not actually there.

    available() says only that a connection pool exists. A directory database
    created before a table was added answers every query against it with an
    error — and the callers here deliberately swallow errors, because losing a
    bookkeeping row must never fail a registration that already succeeded. The
    result is a registration that works, a message that goes out, and an admin
    page that says "no record" forever, with /healthz reporting the directory
    as fine throughout. Exactly the lie the pretix check used to tell by
    asking whether a token was SET rather than whether it WORKED.
    """
    if not available():
        return list(EXPECTED_TABLES)
    try:
        with _cur() as c:
            c.execute("SELECT table_name FROM information_schema.tables "
                      "WHERE table_schema = 'public'")
            have = {r["table_name"] for r in c.fetchall()}
        return [t for t in EXPECTED_TABLES if t not in have]
    except Exception as ex:
        return [f"unreadable ({type(ex).__name__})"]


@contextlib.contextmanager
def _cur():
    conn = _pool.getconn()
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            yield c
    finally:
        _pool.putconn(conn)


def broker(code):
    if not available():
        return None
    with _cur() as c:
        c.execute("SELECT broker_code, name, email, phone, active "
                  "FROM brokers WHERE broker_code = %s", (code,))
        return c.fetchone()


def search_clients(broker_code, q, limit=8):
    """Typeahead over one broker's own book.

    Prefix matches rank above substring ones, because someone typing "meh" is
    far more often starting a surname than recalling one from the middle.
    """
    if not available() or not q or len(q.strip()) < 2:
        return []
    q = q.strip().lower()
    with _cur() as c:
        c.execute(
            """SELECT id, name, phone, email,
                      CASE WHEN lower(name) LIKE %s THEN 0 ELSE 1 END AS rank
               FROM clients
               WHERE broker_code = %s AND lower(name) LIKE %s
               ORDER BY rank, name LIMIT %s""",
            (q + "%", broker_code, "%" + q + "%", limit))
        return [dict(r) for r in c.fetchall()]


def remember_client(broker_code, name, phone, email):
    """A client typed in freehand joins the book, so the next registration
    finds them. Never overwrites a known contact detail with a blank.

    Two things this must not do, both learned by breaking them:

    A broker code typed at the desk may be one the directory has never seen —
    on a fresh install the brokers table is empty until the CSV lands. The
    broker row is created rather than the insert failing on a foreign key.

    And it must never raise. By the time this runs the pretix order exists and
    the client already has their pass in hand; turning that into a 500 tells
    the broker the registration failed when it plainly did not. Bookkeeping
    losing a row is a smaller problem than a broker registering someone twice.
    """
    if not available() or not name or not broker_code:
        return
    try:
        with _cur() as c:
            c.execute(
                """INSERT INTO brokers (broker_code, name)
                   VALUES (%s, %s) ON CONFLICT (broker_code) DO NOTHING""",
                (broker_code, broker_code))
            c.execute(
                """INSERT INTO clients (broker_code, name, phone, email)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (broker_code, lower(name), coalesce(phone, ''))
                   DO UPDATE SET email = COALESCE(EXCLUDED.email, clients.email)""",
                (broker_code, name, phone or None, email or None))
    except Exception as e:
        # Deliberately swallowed. See above.
        print(f"directory: could not remember {name!r} for {broker_code}: "
              f"{type(e).__name__}: {e}", flush=True)


def broker_names(codes):
    """Display names for a set of broker codes, for the admin report."""
    if not available() or not codes:
        return {}
    with _cur() as c:
        c.execute("SELECT broker_code, name FROM brokers WHERE broker_code = ANY(%s)",
                  (list(codes),))
        return {r["broker_code"]: r["name"] for r in c.fetchall()}


# --- what the invitation says, as opposed to what the ticket does ----------

DETAIL_FIELDS = ("speaker", "speaker_title", "speaker_org",
                 "description", "note", "rsvp_name", "rsvp_phone")


def event_details(subevent_id):
    if not available():
        return {}
    with _cur() as c:
        c.execute("SELECT * FROM event_details WHERE subevent_id = %s", (subevent_id,))
        row = c.fetchone()
        return dict(row) if row else {}


def all_event_details():
    """One query for the whole event list, rather than one per event."""
    if not available():
        return {}
    with _cur() as c:
        c.execute("SELECT * FROM event_details")
        return {r["subevent_id"]: dict(r) for r in c.fetchall()}


def set_event_details(subevent_id, **fields):
    """Upsert. A field left out keeps whatever is stored; passing an empty
    string clears it, which is how a wrong speaker gets removed rather than
    being stuck because blank was read as 'no change'."""
    if not available():
        raise RuntimeError("the directory database is not configured")
    given = {k: (v.strip() if isinstance(v, str) else v)
             for k, v in fields.items() if k in DETAIL_FIELDS and v is not None}
    cols = ", ".join(given)
    marks = ", ".join(["%s"] * len(given))
    sets = ", ".join(f"{k} = EXCLUDED.{k}" for k in given)
    with _cur() as c:
        if not given:
            c.execute("INSERT INTO event_details (subevent_id) VALUES (%s) "
                      "ON CONFLICT (subevent_id) DO NOTHING", (subevent_id,))
        else:
            c.execute(
                f"INSERT INTO event_details (subevent_id, {cols}) "
                f"VALUES (%s, {marks}) "
                f"ON CONFLICT (subevent_id) DO UPDATE SET {sets}",
                (subevent_id, *given.values()))


def speaker_line(details):
    """'Sanket Joshi, ICICI Prudential AMC' — the form that reads naturally
    after 'with'. Title is left out here: it matters on the page, not in a
    one-line credit."""
    if not details or not details.get("speaker"):
        return ""
    bits = [details["speaker"]]
    if details.get("speaker_org"):
        bits.append(details["speaker_org"])
    return ", ".join(bits)


# --- did the invitation actually go out? ----------------------------------

def record_delivery(order_code, *, subevent_id, broker_code, name, phone,
                    email, delivery):
    """Remember what happened to the messages.

    Never raises. By the time this runs the order exists and the client has
    their pass; losing a bookkeeping row is a smaller problem than telling a
    broker the registration failed when it plainly did not. The same rule that
    remember_client learned the hard way.
    """
    if not available() or not order_code:
        return
    w = (delivery or {}).get("whatsapp") or {}
    e = (delivery or {}).get("email") or {}
    try:
        with _cur() as c:
            c.execute(
                """INSERT INTO deliveries
                     (order_code, subevent_id, broker_code, name, phone, email,
                      whatsapp_ok, whatsapp_detail, email_ok, email_detail)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (order_code) DO UPDATE SET
                     whatsapp_ok = EXCLUDED.whatsapp_ok,
                     whatsapp_detail = EXCLUDED.whatsapp_detail,
                     email_ok = EXCLUDED.email_ok,
                     email_detail = EXCLUDED.email_detail""",
                (order_code, subevent_id, broker_code, name, phone or None,
                 email or None,
                 w.get("ok") if w else None, (w.get("detail") or "")[:300] or None,
                 e.get("ok") if e else None, (e.get("detail") or "")[:300] or None))
    except Exception as ex:
        print(f"deliveries: could not record {order_code}: "
              f"{type(ex).__name__}: {ex}", flush=True)


def forget_delivery(order_code):
    """Drop the bookkeeping row for a cancelled registration.

    The counters on the admin cards read "WhatsApp sent" straight off this
    table while "registered" is counted from pretix, which excludes cancelled
    orders. Leaving the row behind would mean a cancelled test invite kept
    being counted as a message sent to a guest who no longer exists.

    Never raises, for the same reason record_delivery does not: the order is
    already cancelled in pretix by the time this runs.
    """
    if not available() or not order_code:
        return
    try:
        with _cur() as c:
            c.execute("DELETE FROM deliveries WHERE order_code = %s", (order_code,))
    except Exception as ex:
        print(f"deliveries: could not forget {order_code}: "
              f"{type(ex).__name__}: {ex}", flush=True)


def reminded(template):
    """Order codes already successfully sent this template.

    One query for the whole run rather than one per person: two hundred guests
    is two hundred round trips otherwise, and the reminder goes out in a
    single pass.
    """
    if not available():
        return set()
    try:
        with _cur() as c:
            c.execute("SELECT order_code FROM reminders "
                      "WHERE template = %s AND ok", (template,))
            return {r["order_code"] for r in c.fetchall()}
    except Exception as ex:
        print(f"reminders: could not read who was already sent: "
              f"{type(ex).__name__}: {ex}", flush=True)
        # Empty would mean "nobody has been reminded", and the caller would
        # message everyone a second time. Refuse instead.
        raise


def record_reminder(order_code, template, ok, detail=""):
    """Write down that a reminder was attempted.

    Unlike record_delivery this MUST be able to report failure: if the row does
    not land, the next run has no way to know this person was already messaged,
    and a duplicate reminder is exactly what the table exists to prevent. The
    unique index refuses a second successful row, which is not an error here.
    """
    if not available():
        return False
    try:
        with _cur() as c:
            c.execute("INSERT INTO reminders (order_code, template, ok, detail) "
                      "VALUES (%s, %s, %s, %s)",
                      (order_code, template, bool(ok), (detail or "")[:300] or None))
        return True
    except psycopg2.errors.UniqueViolation:
        return True  # already recorded as sent; nothing to do
    except Exception as ex:
        print(f"reminders: could not record {order_code}: "
              f"{type(ex).__name__}: {ex}", flush=True)
        return False


def deliveries_for(subevent_id):
    """{order_code: row} for one event, so the admin can join it onto pretix."""
    if not available():
        return {}
    try:
        with _cur() as c:
            c.execute("SELECT * FROM deliveries WHERE subevent_id = %s", (subevent_id,))
            return {r["order_code"]: dict(r) for r in c.fetchall()}
    except Exception:
        return {}
