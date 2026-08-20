import pytest
from src.models.schemas import CandidateRule
from src.models.enums import RuleType, Severity, EnforcementMode, RuleStatus

def test_provenance_fields_integrity():
    interaction_text = "In this company, Manufacturing is only installed with approval from the Operations Lead."
    source_quote = "Manufacturing is only installed with approval from the Operations Lead"
    
    candidate = CandidateRule(
        candidate_id="cand_prov_01",
        session_id="sess_prov_01",
        client_id="client_demo",
        process_name="mrp_setup",
        rule_text="MRP installation requires Operations Lead approval.",
        rule_type=RuleType.APPROVAL_POLICY,
        severity=Severity.CRITICAL,
        enforcement_mode=EnforcementMode.BLOCKING,
        source_quote=source_quote,
        confidence=0.96,
        status=RuleStatus.PENDING_REVIEW
    )

    # 1. Source quote must be a verbatim substring of interaction text
    assert candidate.source_quote in interaction_text
    
    # 2. Confidence must be valid probability
    assert 0.0 <= candidate.confidence <= 1.0
    assert candidate.confidence >= 0.70

    # 3. Status must default to pending_review
    assert candidate.status == RuleStatus.PENDING_REVIEW
