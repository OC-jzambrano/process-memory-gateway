from src.models.enums import DecisionType, ExtractionMode

def test_extraction_result_field_presence(memory_tools):
    result = memory_tools.extract_memory_candidates("Some rule must be followed.", "test_client", "general")
    assert hasattr(result, 'session_id')
    assert hasattr(result, 'candidates')
    assert hasattr(result, 'raw_payload')
    assert hasattr(result, 'extraction_mode')
    assert hasattr(result, 'error_detail')
    for c in result.candidates:
        required = ['candidate_id', 'rule_type', 'source_quote', 'confidence', 'status']
        for field in required:
            assert hasattr(c, field), f"Missing field: {field}"

def test_canonical_rule_field_presence(repo, make_candidate):
    make_candidate(candidate_id="cand_contract_01", rule_text="Contract test rule.")
    rule = repo.review_candidate("cand_contract_01", DecisionType.APPROVE, "reviewer", client_id="test_client")

    required = ['rule_id', 'client_id', 'process_name', 'rule_text', 'rule_type', 'version', 'status', 'approved_by']
    for field in required:
        assert hasattr(rule, field), f"Missing canonical rule field: {field}"
