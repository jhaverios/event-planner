-- Broker and client directory for the JSL event portal.
-- Lives beside pretix and n8n in the same cluster, in its own database.
-- Pretix stays the source of truth for registrations; this is only the
-- lookup that makes a broker's form fill itself in.

CREATE TABLE IF NOT EXISTS brokers (
    broker_code  TEXT PRIMARY KEY,
    name         TEXT        NOT NULL,
    email        TEXT,
    phone        TEXT,
    active       BOOLEAN     NOT NULL DEFAULT true,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS clients (
    id           BIGSERIAL PRIMARY KEY,
    broker_code  TEXT        NOT NULL REFERENCES brokers(broker_code) ON DELETE CASCADE,
    name         TEXT        NOT NULL,
    phone        TEXT,
    email        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A broker may legitimately hold two people of the same name, so the key
-- includes the phone. Rows without a phone are deduplicated on name alone.
CREATE UNIQUE INDEX IF NOT EXISTS clients_broker_name_phone_key
    ON clients (broker_code, lower(name), coalesce(phone, ''));

CREATE INDEX IF NOT EXISTS clients_broker_idx      ON clients (broker_code);
CREATE INDEX IF NOT EXISTS clients_name_lower_idx  ON clients (lower(name) text_pattern_ops);
CREATE INDEX IF NOT EXISTS clients_phone_idx       ON clients (phone);

-- Every CSV load is recorded so a bad import can be traced and reversed.
CREATE TABLE IF NOT EXISTS import_batches (
    id           BIGSERIAL PRIMARY KEY,
    filename     TEXT        NOT NULL,
    sha256       TEXT        NOT NULL,
    rows_seen    INTEGER     NOT NULL DEFAULT 0,
    rows_loaded  INTEGER     NOT NULL DEFAULT 0,
    rows_skipped INTEGER     NOT NULL DEFAULT 0,
    notes        TEXT,
    imported_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS brokers_touch ON brokers;
CREATE TRIGGER brokers_touch BEFORE UPDATE ON brokers
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

DROP TRIGGER IF EXISTS clients_touch ON clients;
CREATE TRIGGER clients_touch BEFORE UPDATE ON clients
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- What the invitation says, as opposed to what the ticket does.
--
-- Pretix owns the facts that affect ticketing and check-in: when it starts,
-- how many seats, which check-in list. It does not own who is speaking, and
-- the speaker is often the reason somebody comes. Keeping that here means a
-- structured name, title and firm rather than one blob of description text,
-- which is what the WhatsApp event line and the pass email both need.
--
-- Every column is optional on purpose. An event added in pretix with no row
-- here still registers and still checks people in; the admin screen says the
-- details are missing rather than sending a half-empty invitation.
CREATE TABLE IF NOT EXISTS event_details (
    subevent_id   BIGINT PRIMARY KEY,
    speaker       TEXT,
    speaker_title TEXT,
    speaker_org   TEXT,
    description   TEXT,
    note          TEXT,
    rsvp_name     TEXT,
    rsvp_phone    TEXT,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS event_details_touch ON event_details;
CREATE TRIGGER event_details_touch BEFORE UPDATE ON event_details
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- Was the invitation actually sent?
--
-- The broker's screen showed a tick per channel and then forgot it, so the
-- admin could not answer the one question that matters the morning after:
-- did this person's message go out. Keyed by the pretix order code, because
-- that is the reference a human quotes.
--
-- pretix still owns whether they are registered and whether they turned up.
-- This owns only what happened to the message.
CREATE TABLE IF NOT EXISTS deliveries (
    order_code      TEXT PRIMARY KEY,
    subevent_id     BIGINT,
    broker_code     TEXT,
    name            TEXT,
    phone           TEXT,
    email           TEXT,
    whatsapp_ok     BOOLEAN,
    whatsapp_detail TEXT,
    email_ok        BOOLEAN,
    email_detail    TEXT,
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS deliveries_subevent_idx ON deliveries (subevent_id);
CREATE INDEX IF NOT EXISTS deliveries_broker_idx   ON deliveries (broker_code);

-- Who has already been reminded, and for which template.
--
-- Kept apart from `deliveries` deliberately. That table only has rows for
-- registrations made after it existed — ZYXJK has none — and a reminder must
-- not depend on bookkeeping that predates it. The partial unique index is the
-- real guard against sending twice: the application checks first, but two runs
-- overlapping would both pass that check, and only the database can refuse the
-- second write.
CREATE TABLE IF NOT EXISTS reminders (
    id          BIGSERIAL PRIMARY KEY,
    order_code  TEXT NOT NULL,
    template    TEXT NOT NULL,
    ok          BOOLEAN NOT NULL,
    detail      TEXT,
    sent_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Only successful sends are unique: a failure must stay retryable.
CREATE UNIQUE INDEX IF NOT EXISTS reminders_once_idx
    ON reminders (order_code, template) WHERE ok;
CREATE INDEX IF NOT EXISTS reminders_order_idx ON reminders (order_code);
