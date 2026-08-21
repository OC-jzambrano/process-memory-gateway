import uuid
from datetime import datetime, timezone
from typing import List, Optional, Union
from pathlib import Path

from src.storage.db import db_session, init_db, DEFAULT_DB_PATH
from src.models.schemas import (
    Client,
    BusinessProcess,
    ExtractionSession,
    CandidateRule,
    CanonicalRule,
    ReviewEvent
)
from src.models.enums import RuleStatus, DecisionType, EventType

class MemoryRepository:
    def __init__(self, db_path: Union[str, Path] = DEFAULT_DB_PATH):
        self.db_path = db_path
        init_db(self.db_path)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # --- CLIENTS & BUSINESS PROCESSES ---
    def upsert_client(self, client: Client) -> Client:
        now = self._now()
        with db_session(self.db_path) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO clients (client_id, client_name, industry, odoo_url, notes, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(client_id) DO UPDATE SET
                        client_name=excluded.client_name,
                        industry=excluded.industry,
                        odoo_url=excluded.odoo_url,
                        notes=excluded.notes,
                        updated_at=?
                    """,
                    (
                        client.client_id, client.client_name, client.industry,
                        client.odoo_url, client.notes, now, now, now
                    )
                )
        return client

    def get_client(self, client_id: str) -> Optional[Client]:
        with db_session(self.db_path) as conn:
            cursor = conn.cursor()
            row = cursor.execute("SELECT * FROM clients WHERE client_id = ?", (client_id,)).fetchone()
            if row:
                return Client(**dict(row))
        return None

    def add_process(self, process: BusinessProcess) -> BusinessProcess:
        now = self._now()
        with db_session(self.db_path) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO business_processes (process_id, client_id, process_name, description, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(client_id, process_name) DO UPDATE SET
                        description=excluded.description
                    """,
                    (process.process_id, process.client_id, process.process_name, process.description, now)
                )
        return process

    # --- EXTRACTION SESSIONS & CANDIDATES ---
    def create_session(self, session: ExtractionSession) -> ExtractionSession:
        now = self._now()
        with db_session(self.db_path) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO extraction_sessions 
                    (session_id, client_id, process_name, source_type, interaction_text, model_id, model_temperature, candidates_extracted, extracted_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session.session_id, session.client_id, session.process_name,
                        session.source_type.value, session.interaction_text, session.model_id,
                        session.model_temperature, session.candidates_extracted, now
                    )
                )
        return session

    def save_candidates(self, candidates: List[CandidateRule]) -> List[CandidateRule]:
        if not candidates:
            return []
        now = self._now()
        with db_session(self.db_path) as conn:
            with conn:
                for c in candidates:
                    conn.execute(
                        """
                        INSERT INTO memory_candidates
                        (candidate_id, session_id, client_id, process_name, rule_text, rule_type, severity, enforcement_mode, source_quote, confidence, status, promoted_to_rule_id, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            c.candidate_id, c.session_id, c.client_id, c.process_name,
                            c.rule_text, c.rule_type.value, c.severity.value, c.enforcement_mode.value,
                            c.source_quote, c.confidence, c.status.value, c.promoted_to_rule_id,
                            c.created_at or now, c.updated_at or now
                        )
                    )
        return candidates

    def get_candidate(self, candidate_id: str, client_id: Optional[str] = None) -> Optional[CandidateRule]:
        with db_session(self.db_path) as conn:
            cursor = conn.cursor()
            if client_id:
                row = cursor.execute(
                    "SELECT * FROM memory_candidates WHERE candidate_id = ? AND client_id = ?",
                    (candidate_id, client_id)
                ).fetchone()
            else:
                row = cursor.execute(
                    "SELECT * FROM memory_candidates WHERE candidate_id = ?",
                    (candidate_id,)
                ).fetchone()
            if row:
                return CandidateRule(**dict(row))
        return None

    def list_candidates(
        self,
        client_id: str,
        status: Optional[RuleStatus] = RuleStatus.PENDING_REVIEW,
        process_name: Optional[str] = None
    ) -> List[CandidateRule]:
        with db_session(self.db_path) as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM memory_candidates WHERE client_id = ?"
            params = [client_id]

            if status:
                query += " AND status = ?"
                params.append(status.value)
            if process_name:
                query += " AND process_name = ?"
                params.append(process_name)

            query += " ORDER BY created_at DESC"
            rows = cursor.execute(query, params).fetchall()
            return [CandidateRule(**dict(r)) for r in rows]

    # --- HUMAN REVIEW & CANONICAL RULE PROMOTION (ATOMIC) ---
    def review_candidate(
        self,
        candidate_id: str,
        decision: DecisionType,
        reviewer: str,
        client_id: Optional[str] = None,
        edited_rule_text: Optional[str] = None,
        notes: Optional[str] = None
    ) -> Optional[CanonicalRule]:
        """
        Atomically processes human sign-off on a candidate rule with tenant authorization:
        - Strictly verifies candidate exists and is in 'pending_review' state.
        - Prevents replay attacks: cannot approve or reject an already reviewed candidate.
        - Records an immutable append-only audit event in review_events.
        """
        now = self._now()
        event_id = f"evt_{uuid.uuid4().hex}"
        canonical_rule = None

        with db_session(self.db_path) as conn:
            cursor = conn.cursor()
            
            # 1. Fetch candidate with tenant validation
            if client_id:
                row = cursor.execute(
                    "SELECT * FROM memory_candidates WHERE candidate_id = ? AND client_id = ?",
                    (candidate_id, client_id)
                ).fetchone()
            else:
                row = cursor.execute(
                    "SELECT * FROM memory_candidates WHERE candidate_id = ?",
                    (candidate_id,)
                ).fetchone()

            if not row:
                raise ValueError(f"Candidate with ID '{candidate_id}' not found for tenant '{client_id or 'any'}'.")

            candidate = CandidateRule(**dict(row))

            # 2. State Transition Guard
            if candidate.status != RuleStatus.PENDING_REVIEW:
                raise ValueError(
                    f"Candidate '{candidate_id}' cannot be reviewed: already in '{candidate.status.value}' state."
                )

            with conn:
                if decision in (DecisionType.APPROVE, DecisionType.EDIT):
                    final_text = edited_rule_text if (decision == DecisionType.EDIT and edited_rule_text) else candidate.rule_text
                    rule_id = f"rule_{uuid.uuid4().hex}"

                    canonical_rule = CanonicalRule(
                        rule_id=rule_id,
                        client_id=candidate.client_id,
                        process_name=candidate.process_name,
                        rule_text=final_text,
                        rule_type=candidate.rule_type,
                        severity=candidate.severity,
                        enforcement_mode=candidate.enforcement_mode,
                        version=1,
                        status=RuleStatus.APPROVED,
                        source_candidate_id=candidate.candidate_id,
                        approved_by=reviewer,
                        approved_at=now,
                        created_at=now,
                        updated_at=now
                    )

                    # Atomic Conditional Update: Only transition if STILL pending_review
                    cur = conn.execute(
                        """
                        UPDATE memory_candidates
                        SET status = 'approved', promoted_to_rule_id = ?, updated_at = ?
                        WHERE candidate_id = ? AND status = 'pending_review'
                        """,
                        (rule_id, now, candidate_id)
                    )
                    if cur.rowcount == 0:
                        raise ValueError(f"Candidate '{candidate_id}' review conflict: state changed concurrently.")

                    # Insert Canonical Rule
                    conn.execute(
                        """
                        INSERT INTO canonical_rules
                        (rule_id, client_id, process_name, rule_text, rule_type, severity, enforcement_mode, version, status, source_candidate_id, replaced_by_rule_id, approved_by, approved_at, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            canonical_rule.rule_id, canonical_rule.client_id, canonical_rule.process_name,
                            canonical_rule.rule_text, canonical_rule.rule_type.value, canonical_rule.severity.value,
                            canonical_rule.enforcement_mode.value, canonical_rule.version, canonical_rule.status.value,
                            canonical_rule.source_candidate_id, canonical_rule.replaced_by_rule_id, canonical_rule.approved_by,
                            canonical_rule.approved_at, canonical_rule.created_at, canonical_rule.updated_at
                        )
                    )

                    # Record immutable ReviewEvent
                    conn.execute(
                        """
                        INSERT INTO review_events 
                        (event_id, client_id, candidate_id, rule_id, event_type, reviewer, decision, edited_rule_text, notes, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event_id, candidate.client_id, candidate_id, rule_id,
                            EventType.CANDIDATE_REVIEW.value, reviewer, decision.value,
                            edited_rule_text, notes, now
                        )
                    )

                elif decision == DecisionType.REJECT:
                    cur = conn.execute(
                        """
                        UPDATE memory_candidates
                        SET status = 'rejected', updated_at = ?
                        WHERE candidate_id = ? AND status = 'pending_review'
                        """,
                        (now, candidate_id)
                    )
                    if cur.rowcount == 0:
                        raise ValueError(f"Candidate '{candidate_id}' review conflict: state changed concurrently.")

                    conn.execute(
                        """
                        INSERT INTO review_events 
                        (event_id, client_id, candidate_id, rule_id, event_type, reviewer, decision, edited_rule_text, notes, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event_id, candidate.client_id, candidate_id, None,
                            EventType.CANDIDATE_REVIEW.value, reviewer, decision.value,
                            None, notes, now
                        )
                    )

        return canonical_rule

    # --- RULE SUPERSEDING & VERSIONING (ATOMIC) ---
    def supersede_rule(
        self,
        old_rule_id: str,
        new_rule_text: str,
        reviewer: str,
        client_id: Optional[str] = None,
        notes: Optional[str] = None
    ) -> CanonicalRule:
        """
        Atomically creates a new version of an existing canonical rule:
        - Strictly verifies rule is currently in 'approved' status.
        - Marks old version as 'superseded' with pointer to new version.
        - Records an immutable audit log event in review_events.
        """
        now = self._now()
        new_rule_id = f"rule_{uuid.uuid4().hex}"
        event_id = f"evt_{uuid.uuid4().hex}"

        with db_session(self.db_path) as conn:
            cursor = conn.cursor()
            if client_id:
                old_row = cursor.execute(
                    "SELECT * FROM canonical_rules WHERE rule_id = ? AND client_id = ?",
                    (old_rule_id, client_id)
                ).fetchone()
            else:
                old_row = cursor.execute(
                    "SELECT * FROM canonical_rules WHERE rule_id = ?",
                    (old_rule_id,)
                ).fetchone()

            if not old_row:
                raise ValueError(f"Canonical Rule with ID '{old_rule_id}' not found for tenant '{client_id or 'any'}'.")

            old_rule = CanonicalRule(**dict(old_row))

            # Guard: Only currently active rules can be superseded
            if old_rule.status != RuleStatus.APPROVED:
                raise ValueError(
                    f"Rule '{old_rule_id}' cannot be superseded: current status is '{old_rule.status.value}', expected 'approved'."
                )

            new_rule = CanonicalRule(
                rule_id=new_rule_id,
                client_id=old_rule.client_id,
                process_name=old_rule.process_name,
                rule_text=new_rule_text,
                rule_type=old_rule.rule_type,
                severity=old_rule.severity,
                enforcement_mode=old_rule.enforcement_mode,
                version=old_rule.version + 1,
                status=RuleStatus.APPROVED,
                source_candidate_id=None,  # New version is an evolution, not a direct candidate promotion
                approved_by=reviewer,
                approved_at=now,
                created_at=now,
                updated_at=now
            )

            with conn:
                # 1. Atomic Conditional Update: Old rule must be 'approved'
                cur = conn.execute(
                    """
                    UPDATE canonical_rules
                    SET status = 'superseded', replaced_by_rule_id = ?, updated_at = ?
                    WHERE rule_id = ? AND status = 'approved'
                    """,
                    (new_rule_id, now, old_rule_id)
                )
                if cur.rowcount == 0:
                    raise ValueError(f"Rule '{old_rule_id}' supersede conflict: state changed concurrently.")

                # 2. Insert new version
                conn.execute(
                    """
                    INSERT INTO canonical_rules
                    (rule_id, client_id, process_name, rule_text, rule_type, severity, enforcement_mode, version, status, source_candidate_id, replaced_by_rule_id, approved_by, approved_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_rule.rule_id, new_rule.client_id, new_rule.process_name,
                        new_rule.rule_text, new_rule.rule_type.value, new_rule.severity.value,
                        new_rule.enforcement_mode.value, new_rule.version, new_rule.status.value,
                        new_rule.source_candidate_id, new_rule.replaced_by_rule_id, new_rule.approved_by,
                        new_rule.approved_at, new_rule.created_at, new_rule.updated_at
                    )
                )

                # 3. Record supersede audit event
                conn.execute(
                    """
                    INSERT INTO review_events 
                    (event_id, client_id, candidate_id, rule_id, event_type, reviewer, decision, edited_rule_text, notes, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id, old_rule.client_id, old_rule.source_candidate_id, new_rule_id,
                        EventType.RULE_SUPERSEDED.value, reviewer, DecisionType.SUPERSEDE.value,
                        new_rule_text, notes, now
                    )
                )

        return new_rule

    def get_active_rules(self, client_id: str, process_name: Optional[str] = None) -> List[CanonicalRule]:
        with db_session(self.db_path) as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM canonical_rules WHERE client_id = ? AND status = 'approved'"
            params = [client_id]

            if process_name:
                query += " AND (process_name = ? OR process_name = 'general')"
                params.append(process_name)

            query += " ORDER BY version DESC, created_at DESC"
            rows = cursor.execute(query, params).fetchall()
            return [CanonicalRule(**dict(r)) for r in rows]

    def get_review_events(self, candidate_id: Optional[str] = None, client_id: Optional[str] = None) -> List[ReviewEvent]:
        with db_session(self.db_path) as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM review_events WHERE 1=1"
            params = []

            if client_id:
                query += " AND client_id = ?"
                params.append(client_id)
            if candidate_id:
                query += " AND candidate_id = ?"
                params.append(candidate_id)

            query += " ORDER BY created_at ASC"
            rows = cursor.execute(query, params).fetchall()
            return [ReviewEvent(**dict(r)) for r in rows]

    # --- DATA RETENTION & PRIVACY DELETION ---
    def delete_session(self, session_id: str, client_id: str) -> bool:
        """Deletes an extraction session and associated candidates for privacy compliance."""
        with db_session(self.db_path) as conn:
            with conn:
                cur = conn.execute(
                    "DELETE FROM extraction_sessions WHERE session_id = ? AND client_id = ?",
                    (session_id, client_id)
                )
                return cur.rowcount > 0

    def purge_expired_sessions(self, client_id: str, retention_days: int = 90) -> int:
        """Purges old interaction sessions older than retention_days."""
        with db_session(self.db_path) as conn:
            with conn:
                cur = conn.execute(
                    """
                    DELETE FROM extraction_sessions 
                    WHERE client_id = ? 
                    AND datetime(extracted_at) < datetime('now', '-' || ? || ' days')
                    """,
                    (client_id, retention_days)
                )
                return cur.rowcount
