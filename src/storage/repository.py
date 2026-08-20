import uuid
from datetime import datetime, timezone
from typing import List, Optional, Union
from pathlib import Path
import sqlite3

from src.storage.db import get_connection, init_db, DEFAULT_DB_PATH
from src.models.schemas import (
    Client,
    BusinessProcess,
    ExtractionSession,
    CandidateRule,
    CanonicalRule,
    ReviewEvent
)
from src.models.enums import RuleStatus, DecisionType, RuleType, Severity, EnforcementMode

class MemoryRepository:
    def __init__(self, db_path: Union[str, Path] = DEFAULT_DB_PATH):
        self.db_path = db_path
        init_db(self.db_path)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # --- CLIENTS & PROCESSES ---
    def upsert_client(self, client: Client) -> Client:
        conn = get_connection(self.db_path)
        now = self._now()
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
        conn.close()
        return client

    def get_client(self, client_id: str) -> Optional[Client]:
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        row = cursor.execute("SELECT * FROM clients WHERE client_id = ?", (client_id,)).fetchone()
        conn.close()
        if row:
            return Client(**dict(row))
        return None

    def add_process(self, process: BusinessProcess) -> BusinessProcess:
        conn = get_connection(self.db_path)
        now = self._now()
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
        conn.close()
        return process

    # --- EXTRACTION SESSIONS & CANDIDATES ---
    def create_session(self, session: ExtractionSession) -> ExtractionSession:
        conn = get_connection(self.db_path)
        now = self._now()
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
        conn.close()
        return session

    def save_candidates(self, candidates: List[CandidateRule]) -> List[CandidateRule]:
        conn = get_connection(self.db_path)
        now = self._now()
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
        conn.close()
        return candidates

    def get_candidate(self, candidate_id: str) -> Optional[CandidateRule]:
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        row = cursor.execute("SELECT * FROM memory_candidates WHERE candidate_id = ?", (candidate_id,)).fetchone()
        conn.close()
        if row:
            return CandidateRule(**dict(row))
        return None

    def list_candidates(
        self,
        client_id: str,
        status: Optional[RuleStatus] = RuleStatus.PENDING_REVIEW,
        process_name: Optional[str] = None
    ) -> List[CandidateRule]:
        conn = get_connection(self.db_path)
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
        conn.close()
        return [CandidateRule(**dict(r)) for r in rows]

    # --- HUMAN REVIEW & CANONICAL RULE PROMOTION ---
    def review_candidate(
        self,
        candidate_id: str,
        decision: DecisionType,
        reviewer: str,
        edited_rule_text: Optional[str] = None,
        notes: Optional[str] = None
    ) -> Optional[CanonicalRule]:
        """
        Processes a review decision on a candidate rule.
        - Approving creates a CanonicalRule and updates candidate status to 'approved'.
        - Rejecting updates candidate status to 'rejected'.
        - Records an immutable ReviewEvent audit log.
        """
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        candidate_row = cursor.execute("SELECT * FROM memory_candidates WHERE candidate_id = ?", (candidate_id,)).fetchone()
        if not candidate_row:
            conn.close()
            raise ValueError(f"Candidate with ID '{candidate_id}' not found.")

        candidate = CandidateRule(**dict(candidate_row))
        now = self._now()
        event_id = f"evt_{uuid.uuid4().hex[:10]}"

        canonical_rule = None

        with conn:
            # 1. Record immutable review event
            conn.execute(
                """
                INSERT INTO review_events (event_id, candidate_id, reviewer, decision, edited_rule_text, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, candidate_id, reviewer, decision.value, edited_rule_text, notes, now)
            )

            # 2. Handle Decision
            if decision == DecisionType.APPROVE or decision == DecisionType.EDIT:
                final_text = edited_rule_text if (decision == DecisionType.EDIT and edited_rule_text) else candidate.rule_text
                rule_id = f"rule_{uuid.uuid4().hex[:10]}"

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

                # Insert into canonical_rules
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

                # Update candidate status
                conn.execute(
                    """
                    UPDATE memory_candidates
                    SET status = 'approved', promoted_to_rule_id = ?, updated_at = ?
                    WHERE candidate_id = ?
                    """,
                    (rule_id, now, candidate_id)
                )

            elif decision == DecisionType.REJECT:
                conn.execute(
                    """
                    UPDATE memory_candidates
                    SET status = 'rejected', updated_at = ?
                    WHERE candidate_id = ?
                    """,
                    (now, candidate_id)
                )

        conn.close()
        return canonical_rule

    def supersede_rule(
        self,
        old_rule_id: str,
        new_rule_text: str,
        reviewer: str
    ) -> CanonicalRule:
        """
        Creates a new version of an existing canonical rule, marking the old one as superseded.
        """
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        old_row = cursor.execute("SELECT * FROM canonical_rules WHERE rule_id = ?", (old_rule_id,)).fetchone()
        if not old_row:
            conn.close()
            raise ValueError(f"Rule with ID '{old_rule_id}' not found.")

        old_rule = CanonicalRule(**dict(old_row))
        now = self._now()
        new_rule_id = f"rule_{uuid.uuid4().hex[:10]}"

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
            source_candidate_id=old_rule.source_candidate_id,
            approved_by=reviewer,
            approved_at=now,
            created_at=now,
            updated_at=now
        )

        with conn:
            # 1. Mark old rule as superseded
            conn.execute(
                """
                UPDATE canonical_rules
                SET status = 'superseded', replaced_by_rule_id = ?, updated_at = ?
                WHERE rule_id = ?
                """,
                (new_rule_id, now, old_rule_id)
            )

            # 2. Insert new rule version
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
        conn.close()
        return new_rule

    def get_active_rules(self, client_id: str, process_name: Optional[str] = None) -> List[CanonicalRule]:
        """
        Returns only approved, active canonical rules.
        Guarantees that pending_review candidates are strictly excluded.
        """
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        query = "SELECT * FROM canonical_rules WHERE client_id = ? AND status = 'approved'"
        params = [client_id]

        if process_name:
            query += " AND (process_name = ? OR process_name = 'general')"
            params.append(process_name)

        query += " ORDER BY version DESC, created_at DESC"
        rows = cursor.execute(query, params).fetchall()
        conn.close()
        return [CanonicalRule(**dict(r)) for r in rows]

    def get_review_events(self, candidate_id: str) -> List[ReviewEvent]:
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        rows = cursor.execute(
            "SELECT * FROM review_events WHERE candidate_id = ? ORDER BY created_at ASC",
            (candidate_id,)
        ).fetchall()
        conn.close()
        return [ReviewEvent(**dict(r)) for r in rows]
