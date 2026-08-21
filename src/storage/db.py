import sqlite3
from pathlib import Path
from typing import Union
from contextlib import contextmanager
from src.config import DEFAULT_DB_PATH

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;

-- 1. Clients (Tenants)
CREATE TABLE IF NOT EXISTS clients (
    client_id       TEXT PRIMARY KEY,
    client_name     TEXT NOT NULL,
    industry        TEXT,
    odoo_url        TEXT,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 2. Business Processes
CREATE TABLE IF NOT EXISTS business_processes (
    process_id      TEXT PRIMARY KEY,
    client_id       TEXT NOT NULL REFERENCES clients(client_id) ON DELETE CASCADE,
    process_name    TEXT NOT NULL,
    description     TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(client_id, process_name)
);

-- 3. Extraction Sessions (Provenance Anchor)
CREATE TABLE IF NOT EXISTS extraction_sessions (
    session_id           TEXT NOT NULL,
    client_id            TEXT NOT NULL REFERENCES clients(client_id) ON DELETE CASCADE,
    process_name         TEXT DEFAULT 'general',
    source_type          TEXT NOT NULL DEFAULT 'user_interaction',
    interaction_text     TEXT NOT NULL,
    model_id             TEXT NOT NULL,
    model_temperature    REAL DEFAULT 0.0,
    candidates_extracted INTEGER NOT NULL DEFAULT 0,
    extracted_at         TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (session_id),
    UNIQUE (session_id, client_id)
);

-- 4. Memory Candidates (Inferred rules, starts in pending_review)
CREATE TABLE IF NOT EXISTS memory_candidates (
    candidate_id        TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL,
    client_id           TEXT NOT NULL,
    process_name        TEXT DEFAULT 'general',
    rule_text           TEXT NOT NULL,
    rule_type           TEXT NOT NULL,
    severity            TEXT NOT NULL DEFAULT 'info',
    enforcement_mode    TEXT NOT NULL DEFAULT 'advisory',
    source_quote        TEXT NOT NULL,
    confidence          REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    status              TEXT NOT NULL DEFAULT 'pending_review'
                        CHECK (status IN ('pending_review', 'approved', 'rejected')),
    promoted_to_rule_id TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id, client_id) REFERENCES extraction_sessions(session_id, client_id) ON DELETE CASCADE,
    FOREIGN KEY (client_id) REFERENCES clients(client_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_candidates_client_status
    ON memory_candidates(client_id, status);

CREATE INDEX IF NOT EXISTS idx_candidates_process
    ON memory_candidates(client_id, process_name);

-- 5. Canonical Rules (Approved, Versioned, Enforceable)
CREATE TABLE IF NOT EXISTS canonical_rules (
    rule_id             TEXT PRIMARY KEY,
    client_id           TEXT NOT NULL REFERENCES clients(client_id) ON DELETE CASCADE,
    process_name        TEXT DEFAULT 'general',
    rule_text           TEXT NOT NULL,
    rule_type           TEXT NOT NULL,
    severity            TEXT NOT NULL DEFAULT 'info',
    enforcement_mode    TEXT NOT NULL DEFAULT 'advisory',
    version             INTEGER NOT NULL DEFAULT 1,
    status              TEXT NOT NULL DEFAULT 'approved'
                        CHECK (status IN ('approved', 'superseded', 'archived')),
    source_candidate_id TEXT UNIQUE,
    replaced_by_rule_id TEXT,
    approved_by         TEXT NOT NULL,
    approved_at         TEXT NOT NULL DEFAULT (datetime('now')),
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (source_candidate_id) REFERENCES memory_candidates(candidate_id)
);

CREATE INDEX IF NOT EXISTS idx_rules_client_active
    ON canonical_rules(client_id, status);

CREATE INDEX IF NOT EXISTS idx_rules_process
    ON canonical_rules(client_id, process_name, status);

-- 6. Review Events (Immutable, Append-Only Audit Trail)
CREATE TABLE IF NOT EXISTS review_events (
    event_id        TEXT PRIMARY KEY,
    client_id       TEXT NOT NULL REFERENCES clients(client_id),
    candidate_id    TEXT,
    rule_id         TEXT,
    event_type      TEXT NOT NULL DEFAULT 'candidate_review',
    reviewer        TEXT NOT NULL,
    decision        TEXT NOT NULL,
    edited_rule_text TEXT,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_review_candidate
    ON review_events(candidate_id);

CREATE INDEX IF NOT EXISTS idx_review_client
    ON review_events(client_id);

-- Append-Only Immutability Triggers for review_events
CREATE TRIGGER IF NOT EXISTS trg_prevent_review_events_update
BEFORE UPDATE ON review_events
BEGIN
    SELECT RAISE(FAIL, 'review_events audit trail is strictly append-only and cannot be modified.');
END;

CREATE TRIGGER IF NOT EXISTS trg_prevent_review_events_delete
BEFORE DELETE ON review_events
BEGIN
    SELECT RAISE(FAIL, 'review_events audit trail is strictly append-only and cannot be deleted.');
END;
"""

def get_connection(db_path: Union[str, Path] = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Returns a SQLite connection configured with row factory, foreign keys, WAL mode, and busy timeout."""
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn

@contextmanager
def db_session(db_path: Union[str, Path] = DEFAULT_DB_PATH):
    """Context manager for SQLite connections with automatic commit/rollback and guaranteed closure."""
    conn = get_connection(db_path)
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db(db_path: Union[str, Path] = DEFAULT_DB_PATH) -> None:
    """Initializes the database schema with constraints, indexes, and immutability triggers."""
    with db_session(db_path) as conn:
        with conn:
            conn.executescript(SCHEMA_SQL)
