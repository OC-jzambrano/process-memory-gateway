import json
import uuid
from server import (
    extract_memory_candidates,
    get_candidate_rules,
    review_candidate_rule,
    get_active_rules
)

def test_mcp_server_extract_tool():
    cid = f"mcp_test_{uuid.uuid4().hex[:8]}"
    res_str = extract_memory_candidates(
        interaction_text="Manufacturing requires approval from the lead.",
        client_id=cid,
        process_name="mrp"
    )
    data = json.loads(res_str)
    assert "session_id" in data
    assert data["client_id"] == cid
    assert data["candidates_count"] >= 1

def test_mcp_server_get_and_review_tools():
    cid = f"mcp_test_{uuid.uuid4().hex[:8]}"
    # 1. Extract
    res_str = extract_memory_candidates(
        interaction_text="BOMs must include version numbers.",
        client_id=cid
    )
    extract_data = json.loads(res_str)
    candidate_id = extract_data["candidates"][0]["candidate_id"]

    # 2. Get pending candidates via MCP tool
    inbox_str = get_candidate_rules(client_id=cid)
    inbox_data = json.loads(inbox_str)
    assert inbox_data["count"] >= 1

    # 3. Review candidate via MCP tool
    review_str = review_candidate_rule(
        candidate_id=candidate_id,
        decision="approve",
        reviewer="juan_zambrano",
        client_id=cid,
        notes="Approved via MCP tool"
    )
    review_data = json.loads(review_str)
    assert review_data["status"] == "success"

    # 4. Get active rules via MCP tool
    active_str = get_active_rules(client_id=cid)
    active_data = json.loads(active_str)
    assert active_data["active_rules_count"] == 1
    assert active_data["rules"][0]["source_candidate_id"] == candidate_id
