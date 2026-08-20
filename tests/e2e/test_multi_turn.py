def test_cumulative_extraction_across_turns(memory_tools):
    """Multiple conversation turns build cumulative candidate rules."""
    r1 = memory_tools.extract_memory_candidates("BOMs must include version numbers in name.", "test_client", "mrp")
    r2 = memory_tools.extract_memory_candidates(
        "Manufacturing requires approval from the team leader.", "test_client", "purchasing"
    )

    all_pending = memory_tools.get_candidate_rules("test_client")
    assert len(all_pending) == len(r1.candidates) + len(r2.candidates)
    assert len(all_pending) >= 2
