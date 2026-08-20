from src.models.enums import DecisionType

def test_triple_supersede_chain(repo, make_candidate):
    make_candidate(candidate_id="cand_v1", rule_text="Version 1 rule")
    v1 = repo.review_candidate("cand_v1", DecisionType.APPROVE, "reviewer")
    assert v1.version == 1

    v2 = repo.supersede_rule(v1.rule_id, "Version 2 rule", "reviewer")
    assert v2.version == 2
    assert v2.replaced_by_rule_id is None

    v3 = repo.supersede_rule(v2.rule_id, "Version 3 rule", "reviewer")
    assert v3.version == 3

    # Only v3 is returned in active rules query
    active = repo.get_active_rules("test_client")
    assert len(active) == 1
    assert active[0].version == 3
    assert active[0].rule_text == "Version 3 rule"
