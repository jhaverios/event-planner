"""Directory lookups.

This is only the convenience layer that fills a broker's form in. Pretix stays
the source of truth for who is registered; nothing here is consulted at the
door or in a report.
"""
import contextlib

import psycopg2
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
