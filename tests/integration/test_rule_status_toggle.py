import pytest

from src.models.enums import DecisionType, RuleStatus


def test_archiving_rule_excludes_it_and_restore_reactivates_it(repo, make_candidate):
    make_candidate(candidate_id="cand_toggle", rule_text="Toggle me")
    rule = repo.review_candidate("cand_toggle", DecisionType.APPROVE, "owner")

    archived = repo.set_rule_status(
        rule_id=rule.rule_id,
        status=RuleStatus.ARCHIVED,
        reviewer="owner",
        client_id="test_client",
        notes="Temporarily disabled",
    )
    assert archived.status == RuleStatus.ARCHIVED
    assert repo.get_active_rules("test_client") == []

    restored = repo.set_rule_status(
        rule_id=rule.rule_id,
        status=RuleStatus.APPROVED,
        reviewer="owner",
        client_id="test_client",
        notes="Re-enabled",
    )
    assert restored.status == RuleStatus.APPROVED
    assert len(repo.get_active_rules("test_client")) == 1


def test_rule_status_toggle_rejects_invalid_or_repeated_transitions(repo, make_candidate):
    make_candidate(candidate_id="cand_toggle_invalid")
    rule = repo.review_candidate("cand_toggle_invalid", DecisionType.APPROVE, "owner")

    with pytest.raises(ValueError, match="only be toggled"):
        repo.set_rule_status(rule.rule_id, "superseded", "owner", "test_client")

    with pytest.raises(ValueError, match="already 'approved'"):
        repo.set_rule_status(rule.rule_id, "approved", "owner", "test_client")
