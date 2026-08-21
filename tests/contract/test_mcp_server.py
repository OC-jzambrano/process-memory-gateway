import json
from server import (
    extract_memory_candidates,
    get_candidate_rules,
    review_candidate_rule,
    get_active_rules
)

def test_mcp_server_extract_tool():
    res_str = extract_memory_candidates(
        interaction_text="Manufacturing requires approval from the lead.",
        client_id="mcp_test_client",
        process_name="mrp"
    )
    data = json.loads(res_str)
    assert "session_id" in data
    assert data["client_id"] == "mcp_test_client"
    assert data["candidates_count"] >= 1

def test_mcp_server_get_and_review_tools():
    # 1. Extract
    res_str = extract_memory_candidates(
        interaction_text="BOMs must include version numbers.",
        client_id="mcp_test_client_2"
    )
    extract_data = json.loads(res_str)
    candidate_id = extract_data["candidates"][0]["candidate_id"]

    # 2. Get pending candidates via MCP tool
    inbox_str = get_candidate_rules(client_id="mcp_test_client_2")
    inbox_data = json.loads(inbox_str)
    assert inbox_data["count"] >= 1

    # 3. Review candidate via MCP tool
    review_str = review_candidate_rule(
        candidate_id=candidate_id,
        decision="approve",
        reviewer="juan_zambrano",
        client_id="mcp_test_client_2",
        notes="Approved via MCP tool"
    )
    review_data = json.loads(review_str)
    assert review_data["status"] == "success"

    # 4. Get active rules via MCP tool
    active_str = get_active_rules(client_id="mcp_test_client_2")
    active_data = json.loads(active_str)
    assert active_data["active_rules_count"] == 1
    assert active_data["rules"][0]["source_candidate_id"] == candidate_id
