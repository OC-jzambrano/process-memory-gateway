def test_demo_scenario_matches_run_demo(memory_tools):
    """Mirrors the run_demo.py flow as an automated test."""
    # 1. Extract
    result = memory_tools.extract_memory_candidates(
        "In this company, Manufacturing is only installed with approval from the Operations Lead. "
        "BOMs must include version numbers. Do not create duplicate components if the SKU already exists.",
        "test_client", "manufacturing_setup"
    )
    assert len(result.candidates) >= 3

    # 2. Approve all
    for c in result.candidates:
        memory_tools.review_candidate_rule(c.candidate_id, "approve", "demo_reviewer")

    # 3. Verify all are now active rules
    active = memory_tools.get_active_rules("test_client", "manufacturing_setup")
    assert len(active) == len(result.candidates)
