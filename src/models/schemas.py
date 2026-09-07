from typing import Any

from pydantic import BaseModel, Field, field_validator

from src.models.enums import (
    CompanyStatus,
    ConstraintKind,
    DecisionType,
    EnforcementMode,
    EventType,
    ExecutionEventType,
    ExecutionPhase,
    ExtractionMode,
    MCPTransport,
    MembershipStatus,
    RoleType,
    RuleStatus,
    RuleType,
    RunStatus,
    Severity,
    SourceType,
)


# 1. Action Scope & Deterministic Constraints
class ActionContext(BaseModel):
    system: str = Field(default="odoo", description="Target system, e.g. 'odoo'.")
    application: str | None = Field(
        default="project", description="Target application/module, e.g. 'project'."
    )
    resource: str | None = Field(
        default="project.task",
        description="Target model/resource, e.g. 'project.task'.",
    )
    operation: str | None = Field(
        default="create",
        description="Target operation, e.g. 'create', 'write', 'delete'.",
    )
    fields: list[str] = Field(
        default_factory=list,
        description="Target field names, e.g. ['definition_of_done'].",
    )


class DeterministicConstraint(BaseModel):
    kind: ConstraintKind = Field(default=ConstraintKind.REQUIRED_NONEMPTY_LIST)
    field: str = Field(default="definition_of_done")
    min_items: int = Field(default=1)
    params: dict[str, Any] = Field(default_factory=dict)


# 2. Authenticated Request Context & Principal
class RequestContext(BaseModel):
    company_id: str = Field(min_length=1)
    company_slug: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    email: str = Field(min_length=1)
    role: RoleType = Field(default=RoleType.OWNER)
    client_agent: str | None = Field(
        default=None, description="e.g. 'codex', 'claude_code', 'antigravity'"
    )


class Principal(BaseModel):
    client_id: str = Field(
        min_length=1, description="Tenant ID of the authenticated caller."
    )
    user_id: str = Field(min_length=1, description="Unique user or agent identifier.")
    role: str = Field(default="reviewer", description="Role of caller.")
    permissions: list[str] = Field(default_factory=lambda: ["read", "write", "review"])

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
    created_at: str | None = None
    updated_at: str | None = None


class User(BaseModel):
    user_id: str = Field(min_length=1)
    email: str = Field(min_length=1)
    name: str = Field(min_length=1)
    cognito_sub: str | None = None
    status: str = Field(default="active")
    created_at: str | None = None


class Membership(BaseModel):
    membership_id: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    role: RoleType = Field(default=RoleType.MEMBER)
    status: MembershipStatus = Field(default=MembershipStatus.ACTIVE)
    created_at: str | None = None


class OdooConnectionConfig(BaseModel):
    connection_id: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    secret_arn: str | None = None
    odoo_url: str = Field(default="https://community.odooconcept.com")
    odoo_db: str = Field(default="community")
    default_project_id: int = Field(default=142)
    status: str = Field(default="active")
    created_at: str | None = None


# Backward compatibility Client & BusinessProcess
class Client(BaseModel):
    client_id: str = Field(min_length=1)
    client_name: str = Field(min_length=1)
    industry: str | None = None
    odoo_url: str | None = None
    notes: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class BusinessProcess(BaseModel):
    process_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    process_name: str = Field(min_length=1)
    description: str | None = None
    created_at: str | None = None


# 4. Provenance & Extraction Session
class ExtractionSession(BaseModel):
    session_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    process_name: str | None = "general"
    source_type: SourceType = SourceType.USER_INTERACTION
    interaction_text: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    model_temperature: float | None = 0.0
    candidates_extracted: int = 0
    extracted_at: str | None = None


# 5. Candidate Rule (Inferred, starts as pending_review)
class CandidateRule(BaseModel):
    candidate_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    process_name: str | None = "general"
    rule_text: str = Field(min_length=1)
    rule_type: RuleType = RuleType.OPERATIONAL_CONSTRAINT
    severity: Severity = Severity.INFO
    enforcement_mode: EnforcementMode = EnforcementMode.ADVISORY
    source_quote: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    status: RuleStatus = RuleStatus.PENDING_REVIEW
    structured_scope: ActionContext | None = None
    structured_constraint: DeterministicConstraint | None = None
    promoted_to_rule_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


# 6. Canonical Rule (Approved, Versioned, Enforceable)
class CanonicalRule(BaseModel):
    rule_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    process_name: str | None = "general"
    rule_text: str = Field(min_length=1)
    rule_type: RuleType
    severity: Severity
    enforcement_mode: EnforcementMode
    version: int = Field(ge=1, default=1)
    status: RuleStatus = RuleStatus.APPROVED
    source_candidate_id: str | None = None
    replaced_by_rule_id: str | None = None
    structured_scope: ActionContext | None = None
    structured_constraint: DeterministicConstraint | None = None
    approved_by: str = Field(min_length=1)
    approved_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


# 7. Review Event (Immutable Audit Record)
class ReviewEvent(BaseModel):
    event_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    candidate_id: str | None = None
    rule_id: str | None = None
    event_type: EventType = EventType.CANDIDATE_REVIEW
    reviewer: str = Field(min_length=1)
    decision: DecisionType
    edited_rule_text: str | None = None
    edited_scope: ActionContext | None = None
    edited_constraint: DeterministicConstraint | None = None
    notes: str | None = None
    created_at: str | None = None


# 8. Scoped Memory Pack Models
class MemoryPackRuleItem(BaseModel):
    rule_id: str
    version: int
    rule_text: str
    rule_type: RuleType
    enforcement_mode: EnforcementMode
    scope: ActionContext | None = None
    constraint: DeterministicConstraint | None = None


class MemoryPack(BaseModel):
    company_slug: str
    system: str
    application: str | None = None
    resource: str | None = None
    operation: str | None = None
    rules: list[MemoryPackRuleItem] = Field(default_factory=list)
    token_budget_used: int = 0
    message: str | None = None


# 9. MCP Public Tool Result Models
class CandidateResult(BaseModel):
    status: str = "staged"
    candidate_id: str
    rule_text: str
    scope: ActionContext
    constraint: DeterministicConstraint | None = None
    confidence: float
    message: str


class ReviewResult(BaseModel):
    status: str
    candidate_id: str
    decision: DecisionType
    rule_id: str | None = None
    version: int | None = None
    message: str


# 10. Downstream MCP Registry & Orchestration Models
class DownstreamToolDefinition(BaseModel):
    name: str = Field(min_length=1)
    description: str = Field(default="")
    input_schema: dict[str, Any] = Field(default_factory=dict)


class DownstreamMCPServer(BaseModel):
    company_id: str = Field(min_length=1)
    server_id: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    transport: MCPTransport = Field(default=MCPTransport.STREAMABLE_HTTP)
    available_tools: list[DownstreamToolDefinition] = Field(default_factory=list)
    secret_ref: str | None = None
    supported_action_contexts: list[ActionContext] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None


class RegisterDownstreamMCPResult(BaseModel):
    status: str = "registered"
    server_id: str
    transport: str
    tool_count: int
    message: str


class OrchestrationToolCall(BaseModel):
    server_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class OrchestrationResult(BaseModel):
    success: bool
    correlation_id: str
    server_id: str
    tool_name: str
    result: Any = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskCreationResult(BaseModel):
    status: RunStatus
    run_id: str
    correlation_id: str
    applied_rule_ids: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    odoo_task_id: int | None = None
    odoo_task_url: str | None = None
    task_name: str | None = None
    error_code: str | None = None
    message: str


class TaskRecord(BaseModel):
    id: int
    name: str
    description: str
    project_id: int
    project_name: str | None = None


class CreateTaskOutcome(BaseModel):
    phase: ExecutionPhase
    task_id: int | None = None
    task_record: TaskRecord | None = None
    is_success: bool = False
    error_code: str | None = None
    error_detail: str | None = None


# 10. Execution Runs & Evidence Records
class ExecutionRunRecord(BaseModel):
    run_id: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    action_scope: ActionContext
    adapter_kind: str = Field(default="odoo17_xmlrpc")
    status: RunStatus = Field(default=RunStatus.CREATED)
    redacted_input_hash: str | None = None
    hash_algorithm_version: str = Field(default="v2")
    connection_snapshot: dict[str, Any] | None = None
    execution_token: str | None = None
    applied_rules_snapshot: list[dict[str, Any]] = Field(default_factory=list)
    odoo_task_id: int | None = None
    odoo_task_url: str | None = None
    result_payload: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_detail: str | None = None
    created_at: str | None = None


class ExecutionEventRecord(BaseModel):
    event_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    event_type: ExecutionEventType
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None


# 11. LLM Extraction Models
class ExtractedRuleItem(BaseModel):
    rule_text: str = Field(
        min_length=1, description="Imperative statement of the rule."
    )
    rule_type: RuleType = Field(description="Category of the rule.")
    severity: Severity = Field(default=Severity.INFO)
    enforcement_mode: EnforcementMode = Field(default=EnforcementMode.ADVISORY)
    source_quote: str = Field(
        min_length=1, description="Verbatim quote from input text."
    )
    confidence: float = Field(ge=0.0, le=1.0)
    structured_scope: ActionContext | None = None
    structured_constraint: DeterministicConstraint | None = None


class ExtractedPayload(BaseModel):
    rules: list[ExtractedRuleItem] = Field(default_factory=list)
    reasoning: str | None = Field(default="")
    extraction_mode: ExtractionMode = Field(default=ExtractionMode.BEDROCK_LLM)
    error_detail: str | None = Field(default=None)


class ExtractionResult(BaseModel):
    session_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    process_name: str | None = "general"
    candidates: list[CandidateRule]
    extraction_mode: ExtractionMode = Field(default=ExtractionMode.BEDROCK_LLM)
    error_detail: str | None = None
    raw_payload: ExtractedPayload | None = None
