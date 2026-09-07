from abc import ABC, abstractmethod

from src.models.enums import DecisionType, RuleStatus
from src.models.schemas import (
    ActionContext,
    CandidateRule,
    CanonicalRule,
    Company,
    DeterministicConstraint,
    DownstreamMCPServer,
    ExtractionSession,
    Membership,
    OdooConnectionConfig,
    ReviewEvent,
    User,
)


class BaseRepository(ABC):
    """
    Abstract Base Repository interface defining multi-tenant persistence contracts
    supported across PostgreSQL (production) and SQLite (development/tests).
    """

    # 1. Company / Tenant Management
    @abstractmethod
    def get_company(self, company_id: str) -> Company | None: ...

    @abstractmethod
    def get_company_by_slug(self, company_slug: str) -> Company | None: ...

    @abstractmethod
    def upsert_company(self, company: Company) -> Company: ...

    # 2. User & Membership Management
    @abstractmethod
    def get_user(self, user_id: str) -> User | None: ...

    @abstractmethod
    def get_user_by_cognito_sub(self, cognito_sub: str) -> User | None: ...

    @abstractmethod
    def upsert_user(self, user: User) -> User: ...

    @abstractmethod
    def get_membership(self, company_id: str, user_id: str) -> Membership | None: ...

    @abstractmethod
    def upsert_membership(self, membership: Membership) -> Membership: ...

    # 3. Odoo Connection Configuration
    @abstractmethod
    def get_odoo_connection(self, company_id: str) -> OdooConnectionConfig | None: ...

    @abstractmethod
    def upsert_odoo_connection(
        self, config: OdooConnectionConfig
    ) -> OdooConnectionConfig: ...

    # 4. Extraction Sessions & Candidates
    @abstractmethod
    def create_session(self, session: ExtractionSession) -> ExtractionSession: ...

    @abstractmethod
    def save_candidates(
        self, candidates: list[CandidateRule]
    ) -> list[CandidateRule]: ...

    @abstractmethod
    def list_candidates(
        self,
        client_id: str,
        status: RuleStatus | None = RuleStatus.PENDING_REVIEW,
        process_name: str | None = None,
    ) -> list[CandidateRule]: ...

    @abstractmethod
    def get_candidate(
        self, candidate_id: str, client_id: str | None = None
    ) -> CandidateRule | None: ...

    # 5. Canonical Rules & Governance
    @abstractmethod
    def get_active_rules(
        self,
        client_id: str,
        process_name: str | None = None,
        system: str | None = None,
        resource: str | None = None,
        operation: str | None = None,
    ) -> list[CanonicalRule]: ...

    @abstractmethod
    def create_canonical_rule(self, rule: CanonicalRule) -> CanonicalRule: ...

    @abstractmethod
    def get_rule(
        self, rule_id: str, client_id: str | None = None
    ) -> CanonicalRule | None: ...

    @abstractmethod
    def review_candidate(
        self,
        candidate_id: str,
        decision: DecisionType | str,
        reviewer: str,
        client_id: str | None = None,
        edited_rule_text: str | None = None,
        edited_scope: ActionContext | None = None,
        edited_constraint: DeterministicConstraint | None = None,
        notes: str | None = None,
    ) -> CanonicalRule | None: ...

    @abstractmethod
    def supersede_rule(
        self,
        old_rule_id: str,
        new_rule: CanonicalRule,
        reviewer: str,
        notes: str | None = None,
    ) -> CanonicalRule: ...

    # 6. Downstream MCP Server Registry
    @abstractmethod
    def upsert_downstream_mcp(
        self, server: DownstreamMCPServer
    ) -> DownstreamMCPServer: ...

    @abstractmethod
    def get_downstream_mcp(
        self, company_id: str, server_id: str
    ) -> DownstreamMCPServer | None: ...

    @abstractmethod
    def list_downstream_mcps(
        self, company_id: str
    ) -> list[DownstreamMCPServer]: ...

    @abstractmethod
    def delete_downstream_mcp(
        self, company_id: str, server_id: str
    ) -> bool: ...

    # 7. Audit Trail
    @abstractmethod
    def list_review_events(
        self, client_id: str, limit: int = 50
    ) -> list[ReviewEvent]: ...
