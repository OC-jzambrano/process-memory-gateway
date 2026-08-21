import sqlite3
import pytest
from src.models.enums import DecisionType, RuleType
from src.models.schemas import ExtractionSession, CandidateRule

def test_sql_injection_in_client_id_is_harmless(repo):
    """SQL injection via client_id should return empty results, not crash or leak data."""
    malicious_id = "'; DROP TABLE clients; --"
    result = repo.list_candidates(malicious_id)
    assert result == []

    # Table clients must still exist
    fetched = repo.get_client("test_client")
    assert fetched is not None

def test_composite_fk_prevents_cross_tenant_session_hijack(repo_two_clients):
    """
    A candidate cannot be saved with a session_id belonging to client_a
    while assigning client_b as the candidate's client_id.
    """
    repo = repo_two_clients
    # 1. Create session for client_a
    repo.create_session(ExtractionSession(
        session_id="sess_tenant_a",
        client_id="client_a",
        interaction_text="Private discussion for tenant A",
        model_id="test"
    ))

    # 2. Attempt to create candidate for client_b using client_a's session
    cross_candidate = CandidateRule(
        candidate_id="cand_cross_01",
        session_id="sess_tenant_a",
        client_id="client_b",  # Hijack attempt
        rule_text="Stolen rule",
        rule_type=RuleType.OPERATIONAL_CONSTRAINT,
        source_quote="Private discussion",
        confidence=0.9
    )

    with pytest.raises(sqlite3.IntegrityError):
        repo.save_candidates([cross_candidate])

def test_review_events_are_strictly_immutable(repo, make_candidate, temp_db):
    """Database triggers must block UPDATE or DELETE operations on review_events."""
    make_candidate(candidate_id="cand_audit_01", rule_text="Audit trail rule")
    repo.review_candidate("cand_audit_01", DecisionType.APPROVE, "lead_reviewer", client_id="test_client")

    from src.storage.db import get_connection
    conn = get_connection(temp_db)

    # 1. Attempt to UPDATE review_events must fail via trigger
    with pytest.raises(sqlite3.DatabaseError, match="strictly append-only"):
        conn.execute("UPDATE review_events SET reviewer = 'malicious_actor'")

    # 2. Attempt to DELETE from review_events must fail via trigger
    with pytest.raises(sqlite3.DatabaseError, match="strictly append-only"):
        conn.execute("DELETE FROM review_events")

    conn.close()

def test_cannot_replay_candidate_approval(repo, make_candidate):
    """A candidate cannot be approved twice to create duplicate active rules."""
    make_candidate(candidate_id="cand_replay_01", rule_text="Single approval rule")
    
    # First approval succeeds
    r1 = repo.review_candidate("cand_replay_01", DecisionType.APPROVE, "reviewer", client_id="test_client")
    assert r1 is not None

    # Second approval attempt must raise ValueError (state transition guard)
    with pytest.raises(ValueError, match="cannot be reviewed"):
        repo.review_candidate("cand_replay_01", DecisionType.APPROVE, "reviewer", client_id="test_client")

def test_cannot_supersede_already_superseded_rule(repo, make_candidate):
    """An already superseded rule cannot be superseded again (prevents branch split)."""
    make_candidate(candidate_id="cand_branch_01", rule_text="Original rule")
    v1 = repo.review_candidate("cand_branch_01", DecisionType.APPROVE, "reviewer", client_id="test_client")

    # v1 -> v2
    v2 = repo.supersede_rule(v1.rule_id, "v2 rule", "reviewer", client_id="test_client")
    assert v2.version == 2

    # Attempting to supersede v1 again must fail
    with pytest.raises(ValueError, match="cannot be superseded"):
        repo.supersede_rule(v1.rule_id, "divergent v2 rule", "reviewer", client_id="test_client")
