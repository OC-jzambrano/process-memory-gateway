import pytest
from pathlib import Path
import tempfile

from src.storage.repository import MemoryRepository
from src.models.schemas import Client, ExtractionSession, CandidateRule
from src.models.enums import RuleStatus, RuleType, Severity, EnforcementMode, DecisionType

@pytest.fixture
def repo():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    r = MemoryRepository(db_path=db_path)
    client = Client(client_id="client_flow", client_name="Flow Corp")
    r.upsert_client(client)
    
    session = ExtractionSession(
        session_id="sess_flow_01",
        client_id="client_flow",
        process_name="bom_creation",
        interaction_text="BOMs must include version numbers. Do not create duplicate components.",
        model_id="claude-3-5-haiku",
        candidates_extracted=2
    )
    r.create_session(session)
    yield r
    if db_path.exists():
        db_path.unlink()

def test_approve_candidate_lifecycle(repo):
    cand = CandidateRule(
        candidate_id="cand_app_01",
        session_id="sess_flow_01",
        client_id="client_flow",
        process_name="bom_creation",
        rule_text="BOM names must include version string.",
        rule_type=RuleType.NAMING_CONVENTION,
        severity=Severity.WARNING,
        enforcement_mode=EnforcementMode.ADVISORY,
        source_quote="BOMs must include version numbers",
        confidence=0.92,
        status=RuleStatus.PENDING_REVIEW
    )
    repo.save_candidates([cand])

    # 1. Before approval: active rules are empty
    assert len(repo.get_active_rules("client_flow", "bom_creation")) == 0

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
    active = repo.get_active_rules("client_flow", "bom_creation")
    assert len(active) == 1
    assert active[0].rule_text == "BOM names must include version string."

    # 4. Review event audit log exists
    events = repo.get_review_events("cand_app_01")
    assert len(events) == 1
    assert events[0].decision == DecisionType.APPROVE
    assert events[0].reviewer == "juan_zambrano"

def test_reject_candidate_lifecycle(repo):
    cand = CandidateRule(
        candidate_id="cand_rej_01",
        session_id="sess_flow_01",
        client_id="client_flow",
        process_name="bom_creation",
        rule_text="Temporary rule that should be rejected.",
        rule_type=RuleType.BUSINESS_PREFERENCE,
        severity=Severity.INFO,
        enforcement_mode=EnforcementMode.ADVISORY,
        source_quote="Temporary rule",
        confidence=0.75,
        status=RuleStatus.PENDING_REVIEW
    )
    repo.save_candidates([cand])

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
    assert len(repo.get_active_rules("client_flow", "bom_creation")) == 0

def test_edit_and_supersede_versioning(repo):
    cand = CandidateRule(
        candidate_id="cand_edit_01",
        session_id="sess_flow_01",
        client_id="client_flow",
        process_name="bom_creation",
        rule_text="Initial rule text.",
        rule_type=RuleType.DATA_VALIDATION,
        severity=Severity.CRITICAL,
        enforcement_mode=EnforcementMode.BLOCKING,
        source_quote="Initial rule",
        confidence=0.90,
        status=RuleStatus.PENDING_REVIEW
    )
    repo.save_candidates([cand])

    # 1. Edit & Approve
    canonical_v1 = repo.review_candidate(
        candidate_id="cand_edit_01",
        decision=DecisionType.EDIT,
        reviewer="lead_architect",
        edited_rule_text="Refined rule text: SKU duplicate check is mandatory before component creation."
    )
    assert canonical_v1.rule_text == "Refined rule text: SKU duplicate check is mandatory before component creation."
    assert canonical_v1.version == 1

    # 2. Supersede to v2
    canonical_v2 = repo.supersede_rule(
        old_rule_id=canonical_v1.rule_id,
        new_rule_text="Updated v2: SKU duplicate check applies across all internal companies.",
        reviewer="operations_director"
    )
    assert canonical_v2.version == 2
    assert canonical_v2.rule_text == "Updated v2: SKU duplicate check applies across all internal companies."

    # Active rules query returns only the active version
    active = repo.get_active_rules("client_flow", "bom_creation")
    assert len(active) == 1
    assert active[0].version == 2
