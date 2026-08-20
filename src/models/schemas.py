from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field
from src.models.enums import (
    RuleType,
    Severity,
    RuleStatus,
    EnforcementMode,
    DecisionType,
    SourceType
)

# 1. Client & Business Process Models
class Client(BaseModel):
    client_id: str
    client_name: str
    industry: Optional[str] = None
    odoo_url: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

class BusinessProcess(BaseModel):
    process_id: str
    client_id: str
    process_name: str
    description: Optional[str] = None
    created_at: Optional[str] = None

# 2. Provenance & Extraction Session
class ExtractionSession(BaseModel):
    session_id: str
    client_id: str
    process_name: Optional[str] = None
    source_type: SourceType = SourceType.USER_INTERACTION
    interaction_text: str
    model_id: str
    model_temperature: Optional[float] = 0.0
    candidates_extracted: int = 0
    extracted_at: Optional[str] = None

# 3. Candidate Rule (Inferred, starts as pending_review)
class CandidateRule(BaseModel):
    candidate_id: str
    session_id: str
    client_id: str
    process_name: Optional[str] = "general"
    rule_text: str
    rule_type: RuleType = RuleType.OPERATIONAL_CONSTRAINT
    severity: Severity = Severity.INFO
    enforcement_mode: EnforcementMode = EnforcementMode.ADVISORY
    source_quote: str
    confidence: float = Field(ge=0.0, le=1.0)
    status: RuleStatus = RuleStatus.PENDING_REVIEW
    promoted_to_rule_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

# 4. Canonical Rule (Approved, Versioned, Enforceable)
class CanonicalRule(BaseModel):
    rule_id: str
    client_id: str
    process_name: Optional[str] = "general"
    rule_text: str
    rule_type: RuleType
    severity: Severity
    enforcement_mode: EnforcementMode
    version: int = 1
    status: RuleStatus = RuleStatus.APPROVED # or APPROVED
    source_candidate_id: Optional[str] = None
    replaced_by_rule_id: Optional[str] = None
    approved_by: str
    approved_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

# 5. Review Event (Immutable Audit Record)
class ReviewEvent(BaseModel):
    event_id: str
    candidate_id: str
    reviewer: str
    decision: DecisionType
    edited_rule_text: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[str] = None

# 6. LLM Extraction Raw Item & Result Models
class ExtractedRuleItem(BaseModel):
    rule_text: str = Field(description="Clear, concise imperative statement of the rule or constraint.")
    rule_type: RuleType = Field(description="Category of the rule.")
    severity: Severity = Field(default=Severity.INFO, description="Importance or criticality of the rule.")
    enforcement_mode: EnforcementMode = Field(default=EnforcementMode.ADVISORY, description="Recommended enforcement mechanism.")
    source_quote: str = Field(description="Verbatim exact phrase/substring from the input text from which this rule was inferred.")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score from 0.0 to 1.0.")

class ExtractedPayload(BaseModel):
    rules: List[ExtractedRuleItem] = Field(default_factory=list)
    reasoning: Optional[str] = Field(default="", description="Brief explanation of why these rules were extracted.")

class ExtractionResult(BaseModel):
    session_id: str
    client_id: str
    process_name: Optional[str]
    candidates: List[CandidateRule]
    raw_payload: Optional[ExtractedPayload] = None
