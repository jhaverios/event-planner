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
    finds them. Never overwrites a known contact detail with a blank."""
    if not available() or not name:
        return
    with _cur() as c:
        c.execute(
            """INSERT INTO clients (broker_code, name, phone, email)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (broker_code, lower(name), coalesce(phone, ''))
               DO UPDATE SET email = COALESCE(EXCLUDED.email, clients.email)""",
            (broker_code, name, phone or None, email or None))


def broker_names(codes):
    """Display names for a set of broker codes, for the admin report."""
    if not available() or not codes:
        return {}
    with _cur() as c:
        c.execute("SELECT broker_code, name FROM brokers WHERE broker_code = ANY(%s)",
                  (list(codes),))
        return {r["broker_code"]: r["name"] for r in c.fetchall()}
