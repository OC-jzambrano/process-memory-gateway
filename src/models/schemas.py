from datetime import datetime, timezone
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator
from src.models.enums import (
    RuleType,
    Severity,
    RuleStatus,
    EnforcementMode,
    DecisionType,
    SourceType,
    EventType,
    ExtractionMode
)

# 1. Security Principal / Request Context
class Principal(BaseModel):
    client_id: str = Field(min_length=1, description="Tenant ID of the authenticated caller.")
    user_id: str = Field(min_length=1, description="Unique user or agent identifier.")
    role: str = Field(default="reviewer", description="Role of caller: 'admin', 'reviewer', 'auditor', 'agent'.")
    permissions: List[str] = Field(default_factory=lambda: ["read", "write", "review"])

    @field_validator("client_id", "user_id")
    @classmethod
    def not_empty_string(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Identity fields cannot be empty or whitespace.")
        return s

# 2. Client & Business Process Models
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

# 3. Provenance & Extraction Session
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

# 4. Candidate Rule (Inferred, starts as pending_review)
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
    promoted_to_rule_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

# 5. Canonical Rule (Approved, Versioned, Enforceable)
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
    approved_by: str = Field(min_length=1)
    approved_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

# 6. Review Event (Immutable Audit Record)
class ReviewEvent(BaseModel):
    event_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    candidate_id: Optional[str] = None
    rule_id: Optional[str] = None
    event_type: EventType = EventType.CANDIDATE_REVIEW
    reviewer: str = Field(min_length=1)
    decision: DecisionType
    edited_rule_text: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[str] = None

# 7. LLM Extraction Raw Item & Result Models
class ExtractedRuleItem(BaseModel):
    rule_text: str = Field(min_length=1, description="Clear, concise imperative statement of the rule or constraint.")
    rule_type: RuleType = Field(description="Category of the rule.")
    severity: Severity = Field(default=Severity.INFO, description="Importance or criticality of the rule.")
    enforcement_mode: EnforcementMode = Field(default=EnforcementMode.ADVISORY, description="Recommended enforcement mechanism.")
    source_quote: str = Field(min_length=1, description="Verbatim exact phrase/substring from the input text from which this rule was inferred.")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score from 0.0 to 1.0.")

class ExtractedPayload(BaseModel):
    rules: List[ExtractedRuleItem] = Field(default_factory=list)
    reasoning: Optional[str] = Field(default="", description="Brief explanation of why these rules were extracted.")
    extraction_mode: ExtractionMode = Field(default=ExtractionMode.BEDROCK_LLM)
    error_detail: Optional[str] = Field(default=None, description="Error detail if fallback occurred.")

class ExtractionResult(BaseModel):
    session_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    process_name: Optional[str] = "general"
    candidates: List[CandidateRule]
    extraction_mode: ExtractionMode = Field(default=ExtractionMode.BEDROCK_LLM)
    error_detail: Optional[str] = None
    raw_payload: Optional[ExtractedPayload] = None
