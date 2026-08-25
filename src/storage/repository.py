import json
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Union, Dict, Any
from pathlib import Path

from src.storage.db import db_session, init_db, DEFAULT_DB_PATH
from src.storage.base_repository import BaseRepository
from src.models.schemas import (
    Company,
    User,
    Membership,
    OdooConnectionConfig,
    Client,
    BusinessProcess,
    ExtractionSession,
    CandidateRule,
    CanonicalRule,
    ReviewEvent,
    ExecutionRunRecord,
    ExecutionEventRecord,
    ActionContext,
    DeterministicConstraint
)
from src.models.enums import RuleStatus, DecisionType, EventType, RoleType, RunStatus, MembershipStatus, CompanyStatus

class MemoryRepository(BaseRepository):
    def __init__(self, db_path: Union[str, Path] = DEFAULT_DB_PATH):
        self.db_path = db_path
        init_db(self.db_path)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _serialize_json(self, obj: Any) -> Optional[str]:
        if obj is None:
            return None
        if hasattr(obj, "model_dump"):
            return json.dumps(obj.model_dump())
        return json.dumps(obj)

    def _deserialize_scope(self, data: Optional[str]) -> Optional[ActionContext]:
        if not data:
            return None
        try:
            d = json.loads(data)
            return ActionContext(**d)
        except Exception:
            return None

    def _deserialize_constraint(self, data: Optional[str]) -> Optional[DeterministicConstraint]:
        if not data:
            return None
        try:
            d = json.loads(data)
            return DeterministicConstraint(**d)
        except Exception:
            return None

    # --- 1. COMPANIES / TENANTS ---
    def upsert_company(self, company: Company) -> Company:
        now = self._now()
        with db_session(self.db_path) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO companies (company_id, company_slug, name, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(company_id) DO UPDATE SET
                        company_slug=excluded.company_slug,
                        name=excluded.name,
                        status=excluded.status,
                        updated_at=?
                    """,
                    (
                        company.company_id, company.company_slug, company.name,
                        company.status.value if isinstance(company.status, CompanyStatus) else company.status,
                        company.created_at or now, company.updated_at or now, now
                    )
                )
                # Keep clients table in sync for backward compatibility
                conn.execute(
                    """
                    INSERT INTO clients (client_id, client_name, industry, notes, created_at, updated_at)
                    VALUES (?, ?, 'default', 'synced from company', ?, ?)
                    ON CONFLICT(client_id) DO UPDATE SET
                        client_name=excluded.client_name,
                        updated_at=?
                    """,
                    (company.company_id, company.name, company.created_at or now, now, now)
                )
        return company

    def get_company(self, company_id: str) -> Optional[Company]:
        with db_session(self.db_path) as conn:
            cursor = conn.cursor()
            row = cursor.execute("SELECT * FROM companies WHERE company_id = ?", (company_id,)).fetchone()
            if row:
                d = dict(row)
                return Company(
                    company_id=d["company_id"],
                    company_slug=d["company_slug"],
                    name=d["name"],
                    status=CompanyStatus(d["status"]) if d["status"] in ("active", "suspended") else CompanyStatus.ACTIVE,
                    created_at=d.get("created_at"),
                    updated_at=d.get("updated_at")
                )
        return None

    def get_company_by_slug(self, company_slug: str) -> Optional[Company]:
        with db_session(self.db_path) as conn:
            cursor = conn.cursor()
            row = cursor.execute("SELECT * FROM companies WHERE company_slug = ?", (company_slug,)).fetchone()
            if row:
                d = dict(row)
                return Company(
                    company_id=d["company_id"],
                    company_slug=d["company_slug"],
                    name=d["name"],
                    status=CompanyStatus(d["status"]) if d["status"] in ("active", "suspended") else CompanyStatus.ACTIVE,
                    created_at=d.get("created_at"),
                    updated_at=d.get("updated_at")
                )
        return None

    # Backward compatibility for clients
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
                # Keep companies table in sync
                conn.execute(
                    """
                    INSERT INTO companies (company_id, company_slug, name, status, created_at, updated_at)
                    VALUES (?, ?, ?, 'active', ?, ?)
                    ON CONFLICT(company_id) DO NOTHING
                    """,
                    (client.client_id, client.client_id, client.client_name, now, now)
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

    # --- 2. USERS & MEMBERSHIPS ---
    def upsert_user(self, user: User) -> User:
        now = self._now()
        with db_session(self.db_path) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO users (user_id, email, name, cognito_sub, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        email=excluded.email,
                        name=excluded.name,
                        cognito_sub=excluded.cognito_sub,
                        status=excluded.status
                    """,
                    (user.user_id, user.email, user.name, user.cognito_sub, user.status, user.created_at or now)
                )
        return user

    def get_user(self, user_id: str) -> Optional[User]:
        with db_session(self.db_path) as conn:
            cursor = conn.cursor()
            row = cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if row:
                return User(**dict(row))
        return None

    def upsert_membership(self, membership: Membership) -> Membership:
        now = self._now()
        with db_session(self.db_path) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO memberships (membership_id, company_id, user_id, role, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(company_id, user_id) DO UPDATE SET
                        role=excluded.role,
                        status=excluded.status
                    """,
                    (
                        membership.membership_id, membership.company_id, membership.user_id,
                        membership.role.value if isinstance(membership.role, RoleType) else membership.role,
                        membership.status.value if isinstance(membership.status, MembershipStatus) else membership.status,
                        membership.created_at or now
                    )
                )
        return membership

    def get_membership(self, company_id: str, user_id: str) -> Optional[Membership]:
        with db_session(self.db_path) as conn:
            cursor = conn.cursor()
            row = cursor.execute(
                "SELECT * FROM memberships WHERE company_id = ? AND user_id = ?",
                (company_id, user_id)
            ).fetchone()
            if row:
                d = dict(row)
                return Membership(
                    membership_id=d["membership_id"],
                    company_id=d["company_id"],
                    user_id=d["user_id"],
                    role=RoleType(d["role"]) if d["role"] in [r.value for r in RoleType] else RoleType.MEMBER,
                    status=MembershipStatus(d["status"]) if d["status"] in [s.value for s in MembershipStatus] else MembershipStatus.ACTIVE,
                    created_at=d.get("created_at")
                )
        return None

    # --- 3. ODOO CONNECTIONS ---
    def upsert_odoo_connection(self, config: OdooConnectionConfig) -> OdooConnectionConfig:
        now = self._now()
        with db_session(self.db_path) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO odoo_connections (connection_id, company_id, secret_arn, odoo_url, odoo_db, default_project_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(company_id) DO UPDATE SET
                        secret_arn=excluded.secret_arn,
                        odoo_url=excluded.odoo_url,
                        odoo_db=excluded.odoo_db,
                        default_project_id=excluded.default_project_id
                    """,
                    (
                        config.connection_id, config.company_id, config.secret_arn,
                        config.odoo_url, config.odoo_db, config.default_project_id,
                        config.created_at or now
                    )
                )
        return config

    def get_odoo_connection(self, company_id: str) -> Optional[OdooConnectionConfig]:
        with db_session(self.db_path) as conn:
            cursor = conn.cursor()
            row = cursor.execute("SELECT * FROM odoo_connections WHERE company_id = ?", (company_id,)).fetchone()
            if row:
                return OdooConnectionConfig(**dict(row))
        return None

    # --- 4. EXTRACTION SESSIONS & CANDIDATES ---
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
                    scope_json = self._serialize_json(c.structured_scope)
                    constraint_json = self._serialize_json(c.structured_constraint)
                    conn.execute(
                        """
                        INSERT INTO memory_candidates
                        (candidate_id, session_id, client_id, process_name, rule_text, rule_type, severity, enforcement_mode, source_quote, confidence, status, structured_scope_json, structured_constraint_json, promoted_to_rule_id, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            c.candidate_id, c.session_id, c.client_id, c.process_name,
                            c.rule_text, c.rule_type.value, c.severity.value, c.enforcement_mode.value,
                            c.source_quote, c.confidence, c.status.value, scope_json, constraint_json,
                            c.promoted_to_rule_id, c.created_at or now, c.updated_at or now
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
                d = dict(row)
                return CandidateRule(
                    candidate_id=d["candidate_id"],
                    session_id=d["session_id"],
                    client_id=d["client_id"],
                    process_name=d.get("process_name") or "general",
                    rule_text=d["rule_text"],
                    rule_type=d["rule_type"],
                    severity=d["severity"],
                    enforcement_mode=d["enforcement_mode"],
                    source_quote=d["source_quote"],
                    confidence=d["confidence"],
                    status=d["status"],
                    structured_scope=self._deserialize_scope(d.get("structured_scope_json")),
                    structured_constraint=self._deserialize_constraint(d.get("structured_constraint_json")),
                    promoted_to_rule_id=d.get("promoted_to_rule_id"),
                    created_at=d.get("created_at"),
                    updated_at=d.get("updated_at")
                )
        return None

    def list_candidates(
        self,
        client_id: str,
        status: Optional[Union[RuleStatus, str]] = RuleStatus.PENDING_REVIEW,
        process_name: Optional[str] = None
    ) -> List[CandidateRule]:
        with db_session(self.db_path) as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM memory_candidates WHERE client_id = ?"
            params = [client_id]

            if status:
                query += " AND status = ?"
                status_val = status.value if hasattr(status, "value") else str(status)
                params.append(status_val)
            if process_name:
                query += " AND process_name = ?"
                params.append(process_name)

            query += " ORDER BY created_at DESC"
            rows = cursor.execute(query, params).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                results.append(
                    CandidateRule(
                        candidate_id=d["candidate_id"],
                        session_id=d["session_id"],
                        client_id=d["client_id"],
                        process_name=d.get("process_name") or "general",
                        rule_text=d["rule_text"],
                        rule_type=d["rule_type"],
                        severity=d["severity"],
                        enforcement_mode=d["enforcement_mode"],
                        source_quote=d["source_quote"],
                        confidence=d["confidence"],
                        status=d["status"],
                        structured_scope=self._deserialize_scope(d.get("structured_scope_json")),
                        structured_constraint=self._deserialize_constraint(d.get("structured_constraint_json")),
                        promoted_to_rule_id=d.get("promoted_to_rule_id"),
                        created_at=d.get("created_at"),
                        updated_at=d.get("updated_at")
                    )
                )
            return results

    # --- 5. HUMAN REVIEW & CANONICAL RULE PROMOTION ---
    def review_candidate(
        self,
        candidate_id: str,
        decision: Union[DecisionType, str],
        reviewer: str,
        client_id: Optional[str] = None,
        edited_rule_text: Optional[str] = None,
        edited_scope: Optional[ActionContext] = None,
        edited_constraint: Optional[DeterministicConstraint] = None,
        notes: Optional[str] = None
    ) -> Optional[CanonicalRule]:
        if isinstance(decision, str):
            decision = DecisionType(decision)

        now = self._now()
        event_id = f"evt_{uuid.uuid4().hex}"
        canonical_rule = None

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

            if not row:
                raise ValueError(f"Candidate with ID '{candidate_id}' not found for tenant '{client_id or 'any'}'.")

            d = dict(row)
            candidate = CandidateRule(
                candidate_id=d["candidate_id"],
                session_id=d["session_id"],
                client_id=d["client_id"],
                process_name=d.get("process_name") or "general",
                rule_text=d["rule_text"],
                rule_type=d["rule_type"],
                severity=d["severity"],
                enforcement_mode=d["enforcement_mode"],
                source_quote=d["source_quote"],
                confidence=d["confidence"],
                status=d["status"],
                structured_scope=self._deserialize_scope(d.get("structured_scope_json")),
                structured_constraint=self._deserialize_constraint(d.get("structured_constraint_json")),
                promoted_to_rule_id=d.get("promoted_to_rule_id")
            )

            if candidate.status != RuleStatus.PENDING_REVIEW:
                raise ValueError(
                    f"Candidate '{candidate_id}' cannot be reviewed: already in '{candidate.status.value}' state."
                )

            with conn:
                if decision in (DecisionType.APPROVE, DecisionType.EDIT):
                    final_text = edited_rule_text if (decision == DecisionType.EDIT and edited_rule_text) else candidate.rule_text
                    final_scope = edited_scope or candidate.structured_scope
                    final_constraint = edited_constraint or candidate.structured_constraint
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
                        structured_scope=final_scope,
                        structured_constraint=final_constraint,
                        approved_by=reviewer,
                        approved_at=now,
                        created_at=now,
                        updated_at=now
                    )

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

                    scope_json = self._serialize_json(final_scope)
                    constraint_json = self._serialize_json(final_constraint)

                    conn.execute(
                        """
                        INSERT INTO canonical_rules
                        (rule_id, client_id, process_name, rule_text, rule_type, severity, enforcement_mode, version, status, source_candidate_id, replaced_by_rule_id, structured_scope_json, structured_constraint_json, approved_by, approved_at, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            canonical_rule.rule_id, canonical_rule.client_id, canonical_rule.process_name,
                            canonical_rule.rule_text, canonical_rule.rule_type.value, canonical_rule.severity.value,
                            canonical_rule.enforcement_mode.value, canonical_rule.version, canonical_rule.status.value,
                            canonical_rule.source_candidate_id, canonical_rule.replaced_by_rule_id,
                            scope_json, constraint_json, canonical_rule.approved_by,
                            canonical_rule.approved_at, canonical_rule.created_at, canonical_rule.updated_at
                        )
                    )

                    conn.execute(
                        """
                        INSERT INTO review_events 
                        (event_id, client_id, candidate_id, rule_id, event_type, reviewer, decision, edited_rule_text, edited_scope_json, edited_constraint_json, notes, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event_id, candidate.client_id, candidate_id, rule_id,
                            EventType.CANDIDATE_REVIEW.value, reviewer, decision.value,
                            edited_rule_text, scope_json, constraint_json, notes, now
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

    def supersede_rule(
        self,
        old_rule_id: str,
        new_rule_text: str,
        reviewer: str,
        client_id: Optional[str] = None,
        notes: Optional[str] = None
    ) -> CanonicalRule:
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

            d = dict(old_row)
            old_rule = CanonicalRule(
                rule_id=d["rule_id"],
                client_id=d["client_id"],
                process_name=d.get("process_name") or "general",
                rule_text=d["rule_text"],
                rule_type=d["rule_type"],
                severity=d["severity"],
                enforcement_mode=d["enforcement_mode"],
                version=d["version"],
                status=d["status"],
                structured_scope=self._deserialize_scope(d.get("structured_scope_json")),
                structured_constraint=self._deserialize_constraint(d.get("structured_constraint_json")),
                approved_by=d["approved_by"]
            )

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
                source_candidate_id=None,
                structured_scope=old_rule.structured_scope,
                structured_constraint=old_rule.structured_constraint,
                approved_by=reviewer,
                approved_at=now,
                created_at=now,
                updated_at=now
            )

            with conn:
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

                scope_json = self._serialize_json(new_rule.structured_scope)
                constraint_json = self._serialize_json(new_rule.structured_constraint)

                conn.execute(
                    """
                    INSERT INTO canonical_rules
                    (rule_id, client_id, process_name, rule_text, rule_type, severity, enforcement_mode, version, status, source_candidate_id, replaced_by_rule_id, structured_scope_json, structured_constraint_json, approved_by, approved_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_rule.rule_id, new_rule.client_id, new_rule.process_name,
                        new_rule.rule_text, new_rule.rule_type.value, new_rule.severity.value,
                        new_rule.enforcement_mode.value, new_rule.version, new_rule.status.value,
                        new_rule.source_candidate_id, new_rule.replaced_by_rule_id,
                        scope_json, constraint_json, new_rule.approved_by,
                        new_rule.approved_at, new_rule.created_at, new_rule.updated_at
                    )
                )

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

    def get_rule(self, rule_id: str, client_id: Optional[str] = None) -> Optional[CanonicalRule]:
        with db_session(self.db_path) as conn:
            cursor = conn.cursor()
            if client_id:
                row = cursor.execute("SELECT * FROM canonical_rules WHERE rule_id = ? AND client_id = ?", (rule_id, client_id)).fetchone()
            else:
                row = cursor.execute("SELECT * FROM canonical_rules WHERE rule_id = ?", (rule_id,)).fetchone()
            if row:
                d = dict(row)
                return CanonicalRule(
                    rule_id=d["rule_id"],
                    client_id=d["client_id"],
                    process_name=d.get("process_name") or "general",
                    rule_text=d["rule_text"],
                    rule_type=d["rule_type"],
                    severity=d["severity"],
                    enforcement_mode=d["enforcement_mode"],
                    version=d["version"],
                    status=d["status"],
                    source_candidate_id=d.get("source_candidate_id"),
                    replaced_by_rule_id=d.get("replaced_by_rule_id"),
                    structured_scope=self._deserialize_scope(d.get("structured_scope_json")),
                    structured_constraint=self._deserialize_constraint(d.get("structured_constraint_json")),
                    approved_by=d["approved_by"],
                    approved_at=d.get("approved_at"),
                    created_at=d.get("created_at"),
                    updated_at=d.get("updated_at")
                )
        return None

    def get_active_rules(
        self,
        client_id: str,
        process_name: Optional[str] = None,
        system: Optional[str] = None,
        resource: Optional[str] = None,
        operation: Optional[str] = None
    ) -> List[CanonicalRule]:
        with db_session(self.db_path) as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM canonical_rules WHERE client_id = ? AND status = 'approved'"
            params = [client_id]

            if process_name and process_name != "all":
                query += " AND (process_name = ? OR process_name = 'general')"
                params.append(process_name)

            query += " ORDER BY version DESC, created_at DESC"
            rows = cursor.execute(query, params).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                rule = CanonicalRule(
                    rule_id=d["rule_id"],
                    client_id=d["client_id"],
                    process_name=d.get("process_name") or "general",
                    rule_text=d["rule_text"],
                    rule_type=d["rule_type"],
                    severity=d["severity"],
                    enforcement_mode=d["enforcement_mode"],
                    version=d["version"],
                    status=d["status"],
                    source_candidate_id=d.get("source_candidate_id"),
                    replaced_by_rule_id=d.get("replaced_by_rule_id"),
                    structured_scope=self._deserialize_scope(d.get("structured_scope_json")),
                    structured_constraint=self._deserialize_constraint(d.get("structured_constraint_json")),
                    approved_by=d["approved_by"],
                    approved_at=d.get("approved_at"),
                    created_at=d.get("created_at"),
                    updated_at=d.get("updated_at")
                )
                results.append(rule)
            return results

    # --- 6. EXECUTION RUNS & EVENTS ---
    def create_execution_run(self, run: ExecutionRunRecord) -> ExecutionRunRecord:
        now = self._now()
        scope_json = self._serialize_json(run.action_scope)
        rules_json = self._serialize_json(run.applied_rules_snapshot)
        payload_json = self._serialize_json(run.result_payload)

        with db_session(self.db_path) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO execution_runs
                    (run_id, company_id, user_id, correlation_id, action_scope_json, adapter_kind, status, redacted_input_hash, applied_rules_snapshot_json, odoo_task_id, odoo_task_url, result_payload_json, error_detail, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(company_id, correlation_id) DO UPDATE SET
                        status=excluded.status,
                        odoo_task_id=excluded.odoo_task_id,
                        odoo_task_url=excluded.odoo_task_url,
                        result_payload_json=excluded.result_payload_json,
                        applied_rules_snapshot_json=excluded.applied_rules_snapshot_json,
                        error_detail=excluded.error_detail
                    """,
                    (
                        run.run_id, run.company_id, run.user_id, run.correlation_id,
                        scope_json, run.adapter_kind, run.status.value if isinstance(run.status, RunStatus) else run.status,
                        run.redacted_input_hash, rules_json, run.odoo_task_id, run.odoo_task_url,
                        payload_json, run.error_detail, run.created_at or now
                    )
                )
        return run

    def get_execution_run(self, run_id: str, company_id: Optional[str] = None) -> Optional[ExecutionRunRecord]:
        with db_session(self.db_path) as conn:
            cursor = conn.cursor()
            if company_id:
                row = cursor.execute("SELECT * FROM execution_runs WHERE run_id = ? AND company_id = ?", (run_id, company_id)).fetchone()
            else:
                row = cursor.execute("SELECT * FROM execution_runs WHERE run_id = ?", (run_id,)).fetchone()
            if row:
                return self._parse_run_row(dict(row))
        return None

    def get_execution_run_by_correlation(self, company_id: str, correlation_id: str) -> Optional[ExecutionRunRecord]:
        with db_session(self.db_path) as conn:
            cursor = conn.cursor()
            row = cursor.execute(
                "SELECT * FROM execution_runs WHERE company_id = ? AND correlation_id = ?",
                (company_id, correlation_id)
            ).fetchone()
            if row:
                return self._parse_run_row(dict(row))
        return None

    def update_execution_run(self, run: ExecutionRunRecord) -> ExecutionRunRecord:
        payload_json = self._serialize_json(run.result_payload)
        rules_json = self._serialize_json(run.applied_rules_snapshot)

        with db_session(self.db_path) as conn:
            with conn:
                conn.execute(
                    """
                    UPDATE execution_runs
                    SET status = ?, odoo_task_id = ?, odoo_task_url = ?, result_payload_json = ?, applied_rules_snapshot_json = ?, error_detail = ?
                    WHERE run_id = ?
                    """,
                    (
                        run.status.value if isinstance(run.status, RunStatus) else run.status,
                        run.odoo_task_id, run.odoo_task_url, payload_json, rules_json,
                        run.error_detail, run.run_id
                    )
                )
        return run

    def add_execution_event(self, event: ExecutionEventRecord) -> ExecutionEventRecord:
        now = self._now()
        details_json = self._serialize_json(event.details)
        with db_session(self.db_path) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO execution_events (event_id, run_id, event_type, details_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (event.event_id, event.run_id, event.event_type.value, details_json, event.created_at or now)
                )
        return event

    def _parse_run_row(self, d: Dict[str, Any]) -> ExecutionRunRecord:
        scope = self._deserialize_scope(d.get("action_scope_json")) or ActionContext()
        rules = json.loads(d["applied_rules_snapshot_json"]) if d.get("applied_rules_snapshot_json") else []
        payload = json.loads(d["result_payload_json"]) if d.get("result_payload_json") else {}
        status = RunStatus(d["status"]) if d["status"] in [s.value for s in RunStatus] else RunStatus.CREATED

        return ExecutionRunRecord(
            run_id=d["run_id"],
            company_id=d["company_id"],
            user_id=d["user_id"],
            correlation_id=d["correlation_id"],
            action_scope=scope,
            adapter_kind=d.get("adapter_kind", "odoo17_xmlrpc"),
            status=status,
            redacted_input_hash=d.get("redacted_input_hash"),
            applied_rules_snapshot=rules,
            odoo_task_id=d.get("odoo_task_id"),
            odoo_task_url=d.get("odoo_task_url"),
            result_payload=payload,
            error_detail=d.get("error_detail"),
            created_at=d.get("created_at")
        )

    def list_review_events(self, client_id: str, limit: int = 50) -> List[ReviewEvent]:
        with db_session(self.db_path) as conn:
            cursor = conn.cursor()
            rows = cursor.execute(
                "SELECT * FROM review_events WHERE client_id = ? ORDER BY created_at DESC LIMIT ?",
                (client_id, limit)
            ).fetchall()
            return [ReviewEvent(**dict(r)) for r in rows]

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

    def delete_session(self, session_id: str, client_id: str) -> bool:
        with db_session(self.db_path) as conn:
            with conn:
                cur = conn.execute(
                    "DELETE FROM extraction_sessions WHERE session_id = ? AND client_id = ?",
                    (session_id, client_id)
                )
                return cur.rowcount > 0

    def purge_expired_sessions(self, client_id: str, retention_days: int = 90) -> int:
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
