from abc import ABC, abstractmethod
from typing import Optional, List, Union
from src.models.schemas import (
    Company,
    User,
    Membership,
    OdooConnectionConfig,
    ExtractionSession,
    CandidateRule,
    CanonicalRule,
    ReviewEvent,
    ExecutionRunRecord,
    ExecutionEventRecord,
    ActionContext,
    DeterministicConstraint
)
from src.models.enums import RuleStatus, DecisionType

class BaseRepository(ABC):
    """
    Abstract Base Repository interface defining multi-tenant persistence contracts
    supported across PostgreSQL (production) and SQLite (development/tests).
    """

    # 1. Company / Tenant Management
    @abstractmethod
    def get_company(self, company_id: str) -> Optional[Company]: ...

    @abstractmethod
    def get_company_by_slug(self, company_slug: str) -> Optional[Company]: ...

    @abstractmethod
    def upsert_company(self, company: Company) -> Company: ...

    # 2. User & Membership Management
    @abstractmethod
    def get_user(self, user_id: str) -> Optional[User]: ...

    @abstractmethod
    def upsert_user(self, user: User) -> User: ...

    @abstractmethod
    def get_membership(self, company_id: str, user_id: str) -> Optional[Membership]: ...

    @abstractmethod
    def upsert_membership(self, membership: Membership) -> Membership: ...

    # 3. Odoo Connection Configuration
    @abstractmethod
    def get_odoo_connection(self, company_id: str) -> Optional[OdooConnectionConfig]: ...

    @abstractmethod
    def upsert_odoo_connection(self, config: OdooConnectionConfig) -> OdooConnectionConfig: ...

    # 4. Extraction Sessions & Candidates
    @abstractmethod
    def create_session(self, session: ExtractionSession) -> ExtractionSession: ...

    @abstractmethod
    def save_candidates(self, candidates: List[CandidateRule]) -> List[CandidateRule]: ...

    @abstractmethod
    def list_candidates(
        self,
        client_id: str,
        status: Optional[RuleStatus] = RuleStatus.PENDING_REVIEW,
        process_name: Optional[str] = None
    ) -> List[CandidateRule]: ...

    @abstractmethod
    def get_candidate(self, candidate_id: str, client_id: Optional[str] = None) -> Optional[CandidateRule]: ...

    # 5. Canonical Rules & Governance
    @abstractmethod
    def get_active_rules(
        self,
        client_id: str,
        process_name: Optional[str] = None,
        system: Optional[str] = None,
        resource: Optional[str] = None,
        operation: Optional[str] = None
    ) -> List[CanonicalRule]: ...

    @abstractmethod
    def get_rule(self, rule_id: str, client_id: Optional[str] = None) -> Optional[CanonicalRule]: ...

    @abstractmethod
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
    ) -> Optional[CanonicalRule]: ...

    @abstractmethod
    def supersede_rule(
        self,
        old_rule_id: str,
        new_rule: CanonicalRule,
        reviewer: str,
        notes: Optional[str] = None
    ) -> CanonicalRule: ...

    # 6. Execution Runs & Audit Evidence
    @abstractmethod
    def create_execution_run(self, run: ExecutionRunRecord) -> ExecutionRunRecord: ...

    @abstractmethod
    def get_execution_run(self, run_id: str, company_id: Optional[str] = None) -> Optional[ExecutionRunRecord]: ...

    @abstractmethod
    def get_execution_run_by_correlation(self, company_id: str, correlation_id: str) -> Optional[ExecutionRunRecord]: ...

    @abstractmethod
    def update_execution_run(self, run: ExecutionRunRecord) -> ExecutionRunRecord: ...

    @abstractmethod
    def add_execution_event(self, event: ExecutionEventRecord) -> ExecutionEventRecord: ...

    @abstractmethod
    def list_review_events(self, client_id: str, limit: int = 50) -> List[ReviewEvent]: ...
