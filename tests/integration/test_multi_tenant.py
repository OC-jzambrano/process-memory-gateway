from src.models.schemas import ExtractionSession, CandidateRule
from src.models.enums import RuleStatus, RuleType, Severity, EnforcementMode, DecisionType

def test_client_a_rules_invisible_to_client_b(repo_two_clients):
    repo = repo_two_clients
    repo.create_session(ExtractionSession(
        session_id="sess_a", client_id="client_a",
        process_name="mrp", interaction_text="Test", model_id="test", candidates_extracted=1
    ))
    cand = CandidateRule(
        candidate_id="cand_a", session_id="sess_a", client_id="client_a",
        process_name="mrp", rule_text="Rule for A", rule_type=RuleType.OPERATIONAL_CONSTRAINT,
        severity=Severity.WARNING, enforcement_mode=EnforcementMode.ADVISORY,
        source_quote="Test", confidence=0.9, status=RuleStatus.PENDING_REVIEW
    )
    repo.save_candidates([cand])
    repo.review_candidate("cand_a", DecisionType.APPROVE, "reviewer")

    # Client A sees it, Client B does not
    assert len(repo.get_active_rules("client_a")) == 1
    assert len(repo.get_active_rules("client_b")) == 0

def test_candidate_inbox_is_tenant_scoped(repo_two_clients):
    repo = repo_two_clients
    repo.create_session(ExtractionSession(
        session_id="sess_a2", client_id="client_a",
        process_name="mrp", interaction_text="Test", model_id="test", candidates_extracted=1
    ))
    cand = CandidateRule(
        candidate_id="cand_a2", session_id="sess_a2", client_id="client_a",
        process_name="mrp", rule_text="Pending rule for A", rule_type=RuleType.OPERATIONAL_CONSTRAINT,
        severity=Severity.INFO, enforcement_mode=EnforcementMode.ADVISORY,
        source_quote="Test", confidence=0.85, status=RuleStatus.PENDING_REVIEW
    )
    repo.save_candidates([cand])

    assert len(repo.list_candidates("client_a")) == 1
    assert len(repo.list_candidates("client_b")) == 0
