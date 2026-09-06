-- Phase 2: bounded authority, terminal outcomes, and turn-level metrics.

CREATE TABLE mandates (
    id             TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL,
    objective_type TEXT NOT NULL,
    target_phone   TEXT NOT NULL,
    spec           TEXT NOT NULL,   -- JSON, vertical-specific
    constraints    TEXT NOT NULL,   -- JSON, evaluated server-side
    status         TEXT NOT NULL,   -- draft|authorized|consumed|expired|superseded
    source         TEXT NOT NULL,   -- telegram|console
    created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    authorized_at  TIMESTAMP,
    expires_at     TIMESTAMP NOT NULL
);

CREATE INDEX mandates_user ON mandates (user_id, created_at DESC);

-- At most one live draft per user: creating a draft supersedes prior ones, so a
-- bare "Y" in the chat is never ambiguous. Enforced by the schema rather than by
-- application code, so a concurrent second "coffee" cannot race past it.
CREATE UNIQUE INDEX one_draft_per_user ON mandates (user_id) WHERE status = 'draft';

CREATE TABLE outcomes (
    call_id          TEXT PRIMARY KEY REFERENCES calls (id),
    result           TEXT NOT NULL,
    confidence       TEXT NOT NULL,
    failure_class    TEXT,
    items            TEXT,
    total_cents      INTEGER,
    ready_at         TIMESTAMP,
    confirmation_ref TEXT,
    notes            TEXT,
    needs_review     BOOLEAN NOT NULL DEFAULT 1,
    user_verified_at TIMESTAMP,
    user_verdict     TEXT,
    created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX outcomes_review ON outcomes (needs_review, created_at DESC);

-- PLAN.md §3.3. The most portfolio-valuable output of the project: nobody has
-- good public numbers on what happens when two commercial voice agents talk to
-- each other over PSTN.
CREATE TABLE turn_metrics (
    call_id             TEXT PRIMARY KEY REFERENCES calls (id),
    turn_count          INTEGER NOT NULL,
    gap_p50_ms          INTEGER NOT NULL,
    gap_p95_ms          INTEGER NOT NULL,
    gap_max_ms          INTEGER NOT NULL,
    overlap_ms          INTEGER NOT NULL,
    barge_in_count      INTEGER NOT NULL,
    dead_air_events     INTEGER NOT NULL,
    time_to_outcome_ms  INTEGER NOT NULL,
    computed_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
