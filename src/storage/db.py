import sqlite3
from pathlib import Path
from typing import Union
from contextlib import contextmanager
from src.config import DEFAULT_DB_PATH

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;

-- 1. Clients / Companies (Tenants)
CREATE TABLE IF NOT EXISTS clients (
    client_id       TEXT PRIMARY KEY,
    client_name     TEXT NOT NULL,
    industry        TEXT,
    odoo_url        TEXT,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS companies (
    company_id      TEXT PRIMARY KEY,
    company_slug    TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 2. Users & Memberships
CREATE TABLE IF NOT EXISTS users (
    user_id         TEXT PRIMARY KEY,
    email           TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    cognito_sub     TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS memberships (
    membership_id   TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL,
    user_id         TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    role            TEXT NOT NULL DEFAULT 'member',
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(company_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_memberships_company_user ON memberships(company_id, user_id);

-- 3. Odoo Connection Configurations
CREATE TABLE IF NOT EXISTS odoo_connections (
    connection_id       TEXT PRIMARY KEY,
    company_id          TEXT NOT NULL UNIQUE,
    secret_arn          TEXT,
    odoo_url            TEXT NOT NULL DEFAULT 'https://community.odooconcept.com',
    odoo_db             TEXT NOT NULL DEFAULT 'community',
    default_project_id  INTEGER NOT NULL DEFAULT 142,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 4. Business Processes
CREATE TABLE IF NOT EXISTS business_processes (
    process_id      TEXT PRIMARY KEY,
    client_id       TEXT NOT NULL REFERENCES clients(client_id) ON DELETE CASCADE,
    process_name    TEXT NOT NULL,
    description     TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(client_id, process_name)
);

-- 5. Extraction Sessions (Provenance Anchor)
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

-- 6. Memory Candidates (Inferred rules, starts in pending_review)
CREATE TABLE IF NOT EXISTS memory_candidates (
    candidate_id             TEXT PRIMARY KEY,
    session_id               TEXT NOT NULL,
    client_id                TEXT NOT NULL,
    process_name             TEXT DEFAULT 'general',
    rule_text                TEXT NOT NULL,
    rule_type                TEXT NOT NULL,
    severity                 TEXT NOT NULL DEFAULT 'info',
    enforcement_mode         TEXT NOT NULL DEFAULT 'advisory',
    source_quote             TEXT NOT NULL,
    confidence               REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    status                   TEXT NOT NULL DEFAULT 'pending_review'
                             CHECK (status IN ('pending_review', 'approved', 'rejected')),
    structured_scope_json    TEXT,
    structured_constraint_json TEXT,
    promoted_to_rule_id      TEXT,
    created_at               TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at               TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id, client_id) REFERENCES extraction_sessions(session_id, client_id) ON DELETE CASCADE,
    FOREIGN KEY (client_id) REFERENCES clients(client_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_candidates_client_status
    ON memory_candidates(client_id, status);

CREATE INDEX IF NOT EXISTS idx_candidates_process
    ON memory_candidates(client_id, process_name);

-- 7. Canonical Rules (Approved, Versioned, Enforceable)
CREATE TABLE IF NOT EXISTS canonical_rules (
    rule_id                  TEXT PRIMARY KEY,
    client_id                TEXT NOT NULL REFERENCES clients(client_id) ON DELETE CASCADE,
    process_name             TEXT DEFAULT 'general',
    rule_text                TEXT NOT NULL,
    rule_type                TEXT NOT NULL,
    severity                 TEXT NOT NULL DEFAULT 'info',
    enforcement_mode         TEXT NOT NULL DEFAULT 'advisory',
    version                  INTEGER NOT NULL DEFAULT 1,
    status                   TEXT NOT NULL DEFAULT 'approved'
                             CHECK (status IN ('approved', 'superseded', 'archived')),
    source_candidate_id      TEXT UNIQUE,
    replaced_by_rule_id      TEXT,
    structured_scope_json    TEXT,
    structured_constraint_json TEXT,
    approved_by              TEXT NOT NULL,
    approved_at              TEXT NOT NULL DEFAULT (datetime('now')),
    created_at               TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at               TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (source_candidate_id) REFERENCES memory_candidates(candidate_id)
);

CREATE INDEX IF NOT EXISTS idx_rules_client_active
    ON canonical_rules(client_id, status);

CREATE INDEX IF NOT EXISTS idx_rules_process
    ON canonical_rules(client_id, process_name, status);

-- 8. Review Events (Immutable, Append-Only Audit Trail)
CREATE TABLE IF NOT EXISTS review_events (
    event_id            TEXT PRIMARY KEY,
    client_id           TEXT NOT NULL REFERENCES clients(client_id),
    candidate_id        TEXT,
    rule_id             TEXT,
    event_type          TEXT NOT NULL DEFAULT 'candidate_review',
    reviewer            TEXT NOT NULL,
    decision            TEXT NOT NULL,
    edited_rule_text    TEXT,
    edited_scope_json   TEXT,
    edited_constraint_json TEXT,
    notes               TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_review_candidate ON review_events(candidate_id);
CREATE INDEX IF NOT EXISTS idx_review_client ON review_events(client_id);

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

-- 9. Execution Runs (Managed Execution Records with Idempotency)
CREATE TABLE IF NOT EXISTS execution_runs (
    run_id                      TEXT PRIMARY KEY,
    company_id                  TEXT NOT NULL,
    user_id                     TEXT NOT NULL,
    correlation_id              TEXT NOT NULL,
    action_scope_json           TEXT NOT NULL,
    adapter_kind                TEXT NOT NULL DEFAULT 'odoo17_xmlrpc',
    status                      TEXT NOT NULL DEFAULT 'created',
    redacted_input_hash         TEXT,
    applied_rules_snapshot_json TEXT,
    odoo_task_id                INTEGER,
    odoo_task_url               TEXT,
    result_payload_json         TEXT,
    error_detail                TEXT,
    created_at                  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(company_id, correlation_id)
);

CREATE INDEX IF NOT EXISTS idx_execution_runs_company ON execution_runs(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_execution_runs_correlation ON execution_runs(company_id, correlation_id);

-- 10. Execution Events (Append-Only Execution Audit Trail)
CREATE TABLE IF NOT EXISTS execution_events (
    event_id        TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES execution_runs(run_id) ON DELETE CASCADE,
    event_type      TEXT NOT NULL,
    details_json    TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_execution_events_run ON execution_events(run_id);

CREATE TRIGGER IF NOT EXISTS trg_prevent_execution_events_update
BEFORE UPDATE ON execution_events
BEGIN
    SELECT RAISE(FAIL, 'execution_events audit trail is strictly append-only and cannot be modified.');
END;

CREATE TRIGGER IF NOT EXISTS trg_prevent_execution_events_delete
BEFORE DELETE ON execution_events
BEGIN
    SELECT RAISE(FAIL, 'execution_events audit trail is strictly append-only and cannot be deleted.');
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

def _migrate_columns_if_needed(conn: sqlite3.Connection) -> None:
    """Safely adds newly introduced JSON columns if existing SQLite DB was created previously."""
    cursor = conn.cursor()
    
    # Check memory_candidates columns
    cand_cols = [r["name"] for r in cursor.execute("PRAGMA table_info(memory_candidates);").fetchall()]
    if "structured_scope_json" not in cand_cols and len(cand_cols) > 0:
        cursor.execute("ALTER TABLE memory_candidates ADD COLUMN structured_scope_json TEXT;")
    if "structured_constraint_json" not in cand_cols and len(cand_cols) > 0:
        cursor.execute("ALTER TABLE memory_candidates ADD COLUMN structured_constraint_json TEXT;")

    # Check canonical_rules columns
    rule_cols = [r["name"] for r in cursor.execute("PRAGMA table_info(canonical_rules);").fetchall()]
    if "structured_scope_json" not in rule_cols and len(rule_cols) > 0:
        cursor.execute("ALTER TABLE canonical_rules ADD COLUMN structured_scope_json TEXT;")
    if "structured_constraint_json" not in rule_cols and len(rule_cols) > 0:
        cursor.execute("ALTER TABLE canonical_rules ADD COLUMN structured_constraint_json TEXT;")

    # Check review_events columns
    evt_cols = [r["name"] for r in cursor.execute("PRAGMA table_info(review_events);").fetchall()]
    if "edited_scope_json" not in evt_cols and len(evt_cols) > 0:
        cursor.execute("ALTER TABLE review_events ADD COLUMN edited_scope_json TEXT;")
    if "edited_constraint_json" not in evt_cols and len(evt_cols) > 0:
        cursor.execute("ALTER TABLE review_events ADD COLUMN edited_constraint_json TEXT;")

def init_db(db_path: Union[str, Path] = DEFAULT_DB_PATH) -> None:
    """Initializes the database schema with constraints, indexes, and immutability triggers."""
    with db_session(db_path) as conn:
        with conn:
            conn.executescript(SCHEMA_SQL)
            _migrate_columns_if_needed(conn)
