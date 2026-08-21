import pytest
from pydantic import ValidationError
from src.models.schemas import (
    Client,
    CandidateRule,
    CanonicalRule,
    ExtractedRuleItem,
    Principal
)
from src.models.enums import RuleStatus, Severity, RuleType, EnforcementMode

def test_confidence_above_max_rejected():
    with pytest.raises(ValidationError):
        CandidateRule(
            candidate_id="c1", session_id="s1", client_id="x",
            rule_text="t", source_quote="t", confidence=1.5
        )

def test_confidence_below_min_rejected():
    with pytest.raises(ValidationError):
        CandidateRule(
            candidate_id="c1", session_id="s1", client_id="x",
            rule_text="t", source_quote="t", confidence=-0.1
        )

def test_candidate_defaults():
    cand = CandidateRule(
        candidate_id="c1", session_id="s1", client_id="x",
        rule_text="test rule", source_quote="test", confidence=0.5
    )
    assert cand.status == RuleStatus.PENDING_REVIEW
    assert cand.process_name == "general"
    assert cand.severity == Severity.INFO
    assert cand.enforcement_mode == EnforcementMode.ADVISORY

def test_extracted_rule_item_field_descriptions():
    schema = ExtractedRuleItem.model_json_schema()
    assert "source_quote" in schema["properties"]
    assert "description" in schema["properties"]["source_quote"]
    assert "rule_text" in schema["properties"]
    assert "confidence" in schema["properties"]

def test_client_model_creation():
    client = Client(client_id="c_01", client_name="Enterprise Corp")
    assert client.client_id == "c_01"
    assert client.client_name == "Enterprise Corp"
    assert client.odoo_url is None

def test_canonical_rule_model():
    rule = CanonicalRule(
        rule_id="r_01",
        client_id="c_01",
        rule_text="Mandatory BOM review",
        rule_type=RuleType.APPROVAL_POLICY,
        severity=Severity.CRITICAL,
        enforcement_mode=EnforcementMode.BLOCKING,
        approved_by="admin"
    )
    assert rule.version == 1
    assert rule.status == RuleStatus.APPROVED

def test_principal_model_and_validation():
    p = Principal(client_id="tenant_01", user_id="agent_smith")
    assert p.client_id == "tenant_01"
    assert p.user_id == "agent_smith"
    assert p.role == "reviewer"
    assert "review" in p.permissions

    # Blank client_id / user_id must be rejected
    with pytest.raises(ValidationError):
        Principal(client_id="", user_id="valid_user")

    with pytest.raises(ValidationError):
        Principal(client_id="valid_client", user_id="   ")
