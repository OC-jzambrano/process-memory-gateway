from src.models.enums import RuleStatus, RuleType

def test_bedrock_extract_three_benchmark_rules(memory_tools):
    """
    Tests the real extraction pipeline on the benchmark dialogue:
    - Rule 1: Approval policy for Manufacturing installation.
    - Rule 2: Naming convention for BOMs (versioning).
    - Rule 3: Data validation (no duplicate SKU components).
    """
    dialogue = (
        "In this company, Manufacturing is only installed with approval from the Operations Lead. "
        "BOMs must include version numbers. Do not create duplicate components if the SKU already exists."
    )

    result = memory_tools.extract_memory_candidates(
        interaction_text=dialogue,
        client_id="test_client",
        process_name="manufacturing_setup"
    )

    assert result.session_id is not None
    assert len(result.candidates) >= 3, f"Expected at least 3 candidates, got {len(result.candidates)}"

    # Check rule types extracted
    rule_types = [c.rule_type for c in result.candidates]
    assert RuleType.APPROVAL_POLICY in rule_types or any("approval" in c.rule_text.lower() for c in result.candidates)
    
    # Check that all candidates start in pending_review
    for cand in result.candidates:
        assert cand.status == RuleStatus.PENDING_REVIEW
        assert cand.confidence >= 0.70
        assert len(cand.source_quote) > 0

    # Verify pending candidates are in database inbox
    pending_inbox = memory_tools.get_candidate_rules("test_client")
    assert len(pending_inbox) == len(result.candidates)

    # Verify active rules are 0 before review
    active = memory_tools.get_active_rules("test_client")
    assert len(active) == 0

def test_complete_lifecycle(memory_tools):
    """
    Full lifecycle:
    1. Extract candidates from dialogue
    2. Approve first, reject second
    3. Verify active rules = 1
    4. Verify pending = 0
    """
    result = memory_tools.extract_memory_candidates(
        "Manufacturing requires approval from the lead. BOMs must include version numbers.",
        "test_client", "mrp"
    )
    assert len(result.candidates) >= 2

    # Approve first candidate
    memory_tools.review_candidate_rule(result.candidates[0].candidate_id, "approve", "reviewer")
    # Reject second
    memory_tools.review_candidate_rule(result.candidates[1].candidate_id, "reject", "reviewer")

    active = memory_tools.get_active_rules("test_client")
    assert len(active) == 1

    # No pending candidates remain (all reviewed)
    pending = memory_tools.get_candidate_rules("test_client")
    assert len(pending) == 0
