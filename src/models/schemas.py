from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field, field_validator
from src.models.enums import (
    RuleType,
    Severity,
    RuleStatus,
    EnforcementMode,
    DecisionType,
    SourceType,
    EventType,
    ExtractionMode,
    RoleType,
    RunStatus,
    ConstraintKind,
    CompanyStatus,
    MembershipStatus,
    ExecutionEventType
)

# 1. Action Scope & Deterministic Constraints
class ActionContext(BaseModel):
    system: str = Field(default="odoo", description="Target system, e.g. 'odoo'.")
    application: Optional[str] = Field(default="project", description="Target application/module, e.g. 'project'.")
    resource: Optional[str] = Field(default="project.task", description="Target model/resource, e.g. 'project.task'.")
    operation: Optional[str] = Field(default="create", description="Target operation, e.g. 'create', 'write', 'delete'.")
    fields: List[str] = Field(default_factory=list, description="Target field names, e.g. ['definition_of_done'].")

class DeterministicConstraint(BaseModel):
    kind: ConstraintKind = Field(default=ConstraintKind.REQUIRED_NONEMPTY_LIST)
    field: str = Field(default="definition_of_done")
    min_items: int = Field(default=1)
    params: Dict[str, Any] = Field(default_factory=dict)

# 2. Authenticated Request Context & Principal
class RequestContext(BaseModel):
    company_id: str = Field(min_length=1)
    company_slug: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    email: str = Field(min_length=1)
    role: RoleType = Field(default=RoleType.OWNER)
    client_agent: Optional[str] = Field(default=None, description="e.g. 'codex', 'claude_code', 'antigravity'")

class Principal(BaseModel):
    client_id: str = Field(min_length=1, description="Tenant ID of the authenticated caller.")
    user_id: str = Field(min_length=1, description="Unique user or agent identifier.")
    role: str = Field(default="reviewer", description="Role of caller.")
    permissions: List[str] = Field(default_factory=lambda: ["read", "write", "review"])

    @field_validator("client_id", "user_id")
    @classmethod
    def not_empty_string(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Identity fields cannot be empty or whitespace.")
        return s

# 3. Core Multi-Tenant Entities
class Company(BaseModel):
    company_id: str = Field(min_length=1)
    company_slug: str = Field(min_length=1)
    name: str = Field(min_length=1)
    status: CompanyStatus = Field(default=CompanyStatus.ACTIVE)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

class User(BaseModel):
    user_id: str = Field(min_length=1)
    email: str = Field(min_length=1)
    name: str = Field(min_length=1)
    cognito_sub: Optional[str] = None
    status: str = Field(default="active")
    created_at: Optional[str] = None

class Membership(BaseModel):
    membership_id: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    role: RoleType = Field(default=RoleType.MEMBER)
    status: MembershipStatus = Field(default=MembershipStatus.ACTIVE)
    created_at: Optional[str] = None

class OdooConnectionConfig(BaseModel):
    connection_id: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    secret_arn: Optional[str] = None
    odoo_url: str = Field(default="https://community.odooconcept.com")
    odoo_db: str = Field(default="community")
    default_project_id: int = Field(default=142)
    created_at: Optional[str] = None

# Backward compatibility Client & BusinessProcess
class Client(BaseModel):
    client_id: str = Field(min_length=1)
    client_name: str = Field(min_length=1)
    industry: Optional[str] = None
    odoo_url: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

class BusinessProcess(BaseModel):
    process_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    process_name: str = Field(min_length=1)
    description: Optional[str] = None
    created_at: Optional[str] = None

# 4. Provenance & Extraction Session
class ExtractionSession(BaseModel):
    session_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    process_name: Optional[str] = "general"
    source_type: SourceType = SourceType.USER_INTERACTION
    interaction_text: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    model_temperature: Optional[float] = 0.0
    candidates_extracted: int = 0
    extracted_at: Optional[str] = None

# 5. Candidate Rule (Inferred, starts as pending_review)
class CandidateRule(BaseModel):
    candidate_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    process_name: Optional[str] = "general"
    rule_text: str = Field(min_length=1)
    rule_type: RuleType = RuleType.OPERATIONAL_CONSTRAINT
    severity: Severity = Severity.INFO
    enforcement_mode: EnforcementMode = EnforcementMode.ADVISORY
    source_quote: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    status: RuleStatus = RuleStatus.PENDING_REVIEW
    structured_scope: Optional[ActionContext] = None
    structured_constraint: Optional[DeterministicConstraint] = None
    promoted_to_rule_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

# 6. Canonical Rule (Approved, Versioned, Enforceable)
class CanonicalRule(BaseModel):
    rule_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    process_name: Optional[str] = "general"
    rule_text: str = Field(min_length=1)
    rule_type: RuleType
    severity: Severity
    enforcement_mode: EnforcementMode
    version: int = Field(ge=1, default=1)
    status: RuleStatus = RuleStatus.APPROVED
    source_candidate_id: Optional[str] = None
    replaced_by_rule_id: Optional[str] = None
    structured_scope: Optional[ActionContext] = None
    structured_constraint: Optional[DeterministicConstraint] = None
    approved_by: str = Field(min_length=1)
    approved_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

# 7. Review Event (Immutable Audit Record)
class ReviewEvent(BaseModel):
    event_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    candidate_id: Optional[str] = None
    rule_id: Optional[str] = None
    event_type: EventType = EventType.CANDIDATE_REVIEW
    reviewer: str = Field(min_length=1)
    decision: DecisionType
    edited_rule_text: Optional[str] = None
    edited_scope: Optional[ActionContext] = None
    edited_constraint: Optional[DeterministicConstraint] = None
    notes: Optional[str] = None
    created_at: Optional[str] = None

# 8. Scoped Memory Pack Models
class MemoryPackRuleItem(BaseModel):
    rule_id: str
    version: int
    rule_text: str
    rule_type: RuleType
    enforcement_mode: EnforcementMode
    scope: Optional[ActionContext] = None
    constraint: Optional[DeterministicConstraint] = None

class MemoryPack(BaseModel):
    company_slug: str
    system: str
    application: Optional[str] = None
    resource: Optional[str] = None
    operation: Optional[str] = None
    rules: List[MemoryPackRuleItem] = Field(default_factory=list)
    token_budget_used: int = 0
    message: Optional[str] = None

# 9. MCP Public Tool Result Models
class CandidateResult(BaseModel):
    status: str = "staged"
    candidate_id: str
    rule_text: str
    scope: ActionContext
    constraint: Optional[DeterministicConstraint] = None
    confidence: float
    message: str

class ReviewResult(BaseModel):
    status: str
    candidate_id: str
    decision: DecisionType
    rule_id: Optional[str] = None
    version: Optional[int] = None
    message: str

class TaskCreationResult(BaseModel):
    status: RunStatus
    run_id: str
    correlation_id: str
    applied_rule_ids: List[str] = Field(default_factory=list)
    missing_information: List[str] = Field(default_factory=list)
    odoo_task_id: Optional[int] = None
    odoo_task_url: Optional[str] = None
    task_name: Optional[str] = None
    message: str

class TaskRecord(BaseModel):
    id: int
    name: str
    description: str
    project_id: int
    project_name: Optional[str] = None

# 10. Execution Runs & Evidence Records
class ExecutionRunRecord(BaseModel):
    run_id: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    action_scope: ActionContext
    adapter_kind: str = Field(default="odoo17_xmlrpc")
    status: RunStatus = Field(default=RunStatus.CREATED)
    redacted_input_hash: Optional[str] = None
    applied_rules_snapshot: List[Dict[str, Any]] = Field(default_factory=list)
    odoo_task_id: Optional[int] = None
    odoo_task_url: Optional[str] = None
    result_payload: Dict[str, Any] = Field(default_factory=dict)
    error_detail: Optional[str] = None
    created_at: Optional[str] = None

class ExecutionEventRecord(BaseModel):
    event_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    event_type: ExecutionEventType
    details: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None

# 11. LLM Extraction Models
class ExtractedRuleItem(BaseModel):
    rule_text: str = Field(min_length=1, description="Imperative statement of the rule.")
    rule_type: RuleType = Field(description="Category of the rule.")
    severity: Severity = Field(default=Severity.INFO)
    enforcement_mode: EnforcementMode = Field(default=EnforcementMode.ADVISORY)
    source_quote: str = Field(min_length=1, description="Verbatim quote from input text.")
    confidence: float = Field(ge=0.0, le=1.0)
    structured_scope: Optional[ActionContext] = None
    structured_constraint: Optional[DeterministicConstraint] = None

class ExtractedPayload(BaseModel):
    rules: List[ExtractedRuleItem] = Field(default_factory=list)
    reasoning: Optional[str] = Field(default="")
    extraction_mode: ExtractionMode = Field(default=ExtractionMode.BEDROCK_LLM)
    error_detail: Optional[str] = Field(default=None)

class ExtractionResult(BaseModel):
    session_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    process_name: Optional[str] = "general"
    candidates: List[CandidateRule]
    extraction_mode: ExtractionMode = Field(default=ExtractionMode.BEDROCK_LLM)
    error_detail: Optional[str] = None
    raw_payload: Optional[ExtractedPayload] = None
