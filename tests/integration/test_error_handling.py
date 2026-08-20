import pytest
from src.models.enums import DecisionType

def test_review_nonexistent_candidate_raises(repo):
    with pytest.raises(ValueError, match="not found"):
        repo.review_candidate("nonexistent_id", DecisionType.APPROVE, "reviewer")

def test_supersede_nonexistent_rule_raises(repo):
    with pytest.raises(ValueError, match="not found"):
        repo.supersede_rule("nonexistent_rule", "new text", "reviewer")

def test_empty_candidate_list_for_unknown_client(repo):
    assert repo.list_candidates("unknown_client") == []

def test_get_nonexistent_candidate_returns_none(repo):
    assert repo.get_candidate("nonexistent_cand_id") is None
