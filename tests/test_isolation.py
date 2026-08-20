import pytest
from pathlib import Path
import tempfile
import uuid

from src.storage.repository import MemoryRepository
from src.models.schemas import Client, ExtractionSession, CandidateRule
from src.models.enums import RuleStatus, RuleType, Severity, EnforcementMode

@pytest.fixture
def repo():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    r = MemoryRepository(db_path=db_path)
    client = Client(client_id="demo_corp", client_name="Demo Corp")
    r.upsert_client(client)
    yield r
    if db_path.exists():
        db_path.unlink()

def test_pending_candidates_are_strictly_isolated(repo):
    """
    Guarantees that candidates in 'pending_review' status are NEVER returned
    by get_active_rules queries.
    """
    session = ExtractionSession(
        session_id="sess_iso_01",
        client_id="demo_corp",
        process_name="mrp_setup",
        interaction_text="Never install MRP without lead approval.",
        model_id="test-model",
        candidates_extracted=1
    )
    repo.create_session(session)

    candidate = CandidateRule(
        candidate_id="cand_iso_01",
        session_id="sess_iso_01",
        client_id="demo_corp",
        process_name="mrp_setup",
        rule_text="MRP installation requires Operations Lead approval.",
        rule_type=RuleType.APPROVAL_POLICY,
        severity=Severity.CRITICAL,
        enforcement_mode=EnforcementMode.BLOCKING,
        source_quote="Never install MRP without lead approval.",
        confidence=0.98,
        status=RuleStatus.PENDING_REVIEW
    )
    repo.save_candidates([candidate])

    # 1. Candidate is in pending list
    pending = repo.list_candidates("demo_corp", status=RuleStatus.PENDING_REVIEW)
    assert len(pending) == 1
    assert pending[0].candidate_id == "cand_iso_01"

    # 2. Active rules MUST be empty
    active_rules = repo.get_active_rules("demo_corp", process_name="mrp_setup")
    assert len(active_rules) == 0, "Security Failure: Pending candidate was leaked into active rules!"
