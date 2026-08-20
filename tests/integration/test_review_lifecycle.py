import pytest
from src.models.schemas import CandidateRule
from src.models.enums import RuleStatus, RuleType, Severity, EnforcementMode, DecisionType

def test_approve_candidate_lifecycle(repo, make_candidate):
    make_candidate(
        candidate_id="cand_app_01",
        rule_text="BOM names must include version string.",
        rule_type=RuleType.NAMING_CONVENTION
    )

    # 1. Before approval: active rules are empty
    assert len(repo.get_active_rules("test_client", "general")) == 0

    # 2. Approve candidate
    canonical = repo.review_candidate(
        candidate_id="cand_app_01",
        decision=DecisionType.APPROVE,
        reviewer="juan_zambrano",
        notes="Approved for standard BOM naming policy."
    )
    assert canonical is not None
    assert canonical.version == 1
    assert canonical.approved_by == "juan_zambrano"

    # 3. After approval: active rules contains canonical rule
    active = repo.get_active_rules("test_client", "general")
    assert len(active) == 1
    assert active[0].rule_text == "BOM names must include version string."

    # 4. Review event audit log exists
    events = repo.get_review_events("cand_app_01")
    assert len(events) == 1
    assert events[0].decision == DecisionType.APPROVE
    assert events[0].reviewer == "juan_zambrano"

def test_reject_candidate_lifecycle(repo, make_candidate):
    make_candidate(
        candidate_id="cand_rej_01",
        rule_text="Temporary rule that should be rejected.",
        rule_type=RuleType.BUSINESS_PREFERENCE
    )

    # Reject candidate
    canonical = repo.review_candidate(
        candidate_id="cand_rej_01",
        decision=DecisionType.REJECT,
        reviewer="juan_zambrano",
        notes="Not a permanent company policy."
    )
    assert canonical is None

    # Candidate status is rejected
    updated_cand = repo.get_candidate("cand_rej_01")
    assert updated_cand.status == RuleStatus.REJECTED

    # Active rules remain empty
    assert len(repo.get_active_rules("test_client", "general")) == 0

def test_edit_and_approve_lifecycle(repo, make_candidate):
    make_candidate(
        candidate_id="cand_edit_01",
        rule_text="Initial unrefined text.",
        rule_type=RuleType.DATA_VALIDATION
    )

    canonical = repo.review_candidate(
        candidate_id="cand_edit_01",
        decision=DecisionType.EDIT,
        reviewer="lead_architect",
        edited_rule_text="Refined rule text: SKU duplicate check is mandatory before component creation."
    )
    assert canonical is not None
    assert canonical.rule_text == "Refined rule text: SKU duplicate check is mandatory before component creation."
    assert canonical.version == 1

    active = repo.get_active_rules("test_client", "general")
    assert len(active) == 1
    assert active[0].rule_text == "Refined rule text: SKU duplicate check is mandatory before component creation."
