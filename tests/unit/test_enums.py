import pytest
from src.models.enums import (
    RuleType,
    Severity,
    RuleStatus,
    EnforcementMode,
    DecisionType,
    SourceType,
    EventType,
    ExtractionMode,
    LLMProviderType
)

def test_rule_type_has_six_members():
    assert len(RuleType) == 6

def test_rule_type_string_coercion():
    assert RuleType("approval_policy") == RuleType.APPROVAL_POLICY
    assert RuleType("data_validation") == RuleType.DATA_VALIDATION
    assert RuleType("naming_convention") == RuleType.NAMING_CONVENTION
    assert RuleType("business_preference") == RuleType.BUSINESS_PREFERENCE
    assert RuleType("operational_constraint") == RuleType.OPERATIONAL_CONSTRAINT
    assert RuleType("security_restriction") == RuleType.SECURITY_RESTRICTION

def test_invalid_rule_type_raises():
    with pytest.raises(ValueError):
        RuleType("nonexistent_type")

def test_severity_members():
    assert set(s.value for s in Severity) == {"critical", "warning", "info"}

def test_rule_status_covers_full_lifecycle():
    statuses = {s.value for s in RuleStatus}
    assert statuses >= {"pending_review", "approved", "rejected", "superseded", "archived"}

def test_decision_type_values():
    assert set(d.value for d in DecisionType) == {"approve", "reject", "edit", "supersede", "escalate"}

def test_event_type_values():
    assert set(e.value for e in EventType) == {"candidate_review", "rule_superseded", "rule_archived"}

def test_extraction_mode_values():
    assert set(m.value for m in ExtractionMode) == {"openai_llm", "bedrock_llm", "local_fallback"}

def test_llm_provider_type_values():
    assert set(p.value for p in LLMProviderType) == {"openai", "bedrock", "auto", "local"}

def test_enforcement_mode_values():
    assert set(e.value for e in EnforcementMode) == {"blocking", "requires_approval", "advisory"}

def test_source_type_values():
    assert set(s.value for s in SourceType) == {
        "user_interaction", "meeting_transcript", "ticket_comment", "slack_message", "document_upload"
    }
