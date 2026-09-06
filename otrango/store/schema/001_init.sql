-- Phase 0: calls and the idempotent event sink.
-- Mandates and outcomes arrive in Phase 2 (migration 002), which also adds the
-- foreign key on calls.mandate_id. It is nullable here because Phase 0 dials
-- directly, without an authorizing mandate.

CREATE TABLE calls (
    id               TEXT PRIMARY KEY,
    mandate_id       TEXT,
    provider         TEXT NOT NULL DEFAULT 'vapi',
    provider_call_id TEXT UNIQUE,
    to_number        TEXT NOT NULL,
    status           TEXT NOT NULL,   -- queued|dialing|in_progress|ended|failed
    end_reason       TEXT,
    recording_url    TEXT,
    transcript       TEXT,
    cost_cents       INTEGER,
    created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at       TIMESTAMP,
    ended_at         TIMESTAMP
);

CREATE INDEX calls_created_at ON calls (created_at DESC);
CREATE INDEX calls_status ON calls (status);

-- Append-only webhook sink. The UNIQUE constraint on provider_event_id is the
-- entire idempotency mechanism: a replayed payload derives the same key and
-- loses the INSERT race, so duplicate delivery costs one failed insert.
CREATE TABLE events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_event_id TEXT UNIQUE NOT NULL,
    call_id           TEXT,
    kind              TEXT NOT NULL,
    payload           TEXT NOT NULL,
    received_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX events_call_id ON events (call_id, received_at);
