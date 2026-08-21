import json
import logging
from typing import Optional
from mcp.server.fastmcp import FastMCP

from src.api.memory_tools import ProcessMemoryTools
from src.models.schemas import Principal
from src.models.enums import RuleStatus, DecisionType

# Configure Logging (to stderr so stdout is reserved for JSON-RPC transport)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("process-memory-mcp")

# Initialize FastMCP Server
mcp = FastMCP("odoo-process-memory")
tools = ProcessMemoryTools()

@mcp.tool()
def extract_memory_candidates(
    interaction_text: str,
    client_id: str,
    process_name: str = "general",
    reviewer_user_id: str = "ai_agent"
) -> str:
    """
    Analyzes user or consultant dialogue to extract tacit and explicit operational business rules.
    Attaches exact source quotes, confidence scores, and stages rules in 'pending_review' status.
    
    Args:
        interaction_text: The conversational text or user dialogue turn to analyze.
        client_id: The unique tenant / client identifier.
        process_name: The ERP business process context (e.g. 'mrp', 'sales', 'inventory', 'general').
        reviewer_user_id: Identifier of the agent or caller initiating the extraction.
    """
    principal = Principal(client_id=client_id, user_id=reviewer_user_id, role="agent")
    result = tools.extract_memory_candidates(
        interaction_text=interaction_text,
        client_id=client_id,
        process_name=process_name,
        principal=principal
    )
    
    output = {
        "session_id": result.session_id,
        "client_id": result.client_id,
        "process_name": result.process_name,
        "extraction_mode": result.extraction_mode.value,
        "candidates_count": len(result.candidates),
        "candidates": [c.model_dump() for c in result.candidates]
    }
    return json.dumps(output, indent=2)

@mcp.tool()
def get_candidate_rules(
    client_id: str,
    status: str = "pending_review",
    process_name: Optional[str] = None,
    reviewer_user_id: str = "ai_agent"
) -> str:
    """
    Retrieves candidate rules awaiting human review for a given client/tenant.
    
    Args:
        client_id: The unique tenant / client identifier.
        status: Status filter ('pending_review', 'approved', 'rejected'). Defaults to 'pending_review'.
        process_name: Optional business process filter.
        reviewer_user_id: Identifier of the caller.
    """
    principal = Principal(client_id=client_id, user_id=reviewer_user_id)
    candidates = tools.get_candidate_rules(
        client_id=client_id,
        status=RuleStatus(status.lower()) if status else RuleStatus.PENDING_REVIEW,
        process_name=process_name,
        principal=principal
    )
    
    output = {
        "client_id": client_id,
        "count": len(candidates),
        "candidates": [c.model_dump() for c in candidates]
    }
    return json.dumps(output, indent=2)

@mcp.tool()
def review_candidate_rule(
    candidate_id: str,
    decision: str,
    reviewer: str,
    client_id: str,
    edited_rule_text: Optional[str] = None,
    notes: Optional[str] = None
) -> str:
    """
    Processes human sign-off on a candidate rule:
    - 'approve': Promotes candidate to active canonical rule (v1).
    - 'edit': Promotes edited rule text to active canonical rule.
    - 'reject': Marks candidate as rejected.
    Records an immutable audit event in review_events.
    
    Args:
        candidate_id: Unique identifier of the candidate rule to review.
        decision: Review decision ('approve', 'reject', 'edit').
        reviewer: Name or user ID of the human reviewer.
        client_id: Tenant / client identifier.
        edited_rule_text: Refined rule text (required if decision is 'edit').
        notes: Optional explanation or context for the decision.
    """
    principal = Principal(client_id=client_id, user_id=reviewer, role="reviewer")
    canonical_rule = tools.review_candidate_rule(
        candidate_id=candidate_id,
        decision=DecisionType(decision.lower()),
        reviewer=reviewer,
        client_id=client_id,
        edited_rule_text=edited_rule_text,
        notes=notes,
        principal=principal
    )
    
    if canonical_rule:
        output = {
            "status": "success",
            "decision": decision,
            "message": f"Candidate '{candidate_id}' promoted to Canonical Rule '{canonical_rule.rule_id}' (version {canonical_rule.version}).",
            "canonical_rule": canonical_rule.model_dump()
        }
    else:
        output = {
            "status": "success",
            "decision": decision,
            "message": f"Candidate '{candidate_id}' was rejected and archived."
        }
    return json.dumps(output, indent=2)

@mcp.tool()
def get_active_rules(
    client_id: str,
    process_name: Optional[str] = None,
    reviewer_user_id: str = "ai_agent"
) -> str:
    """
    Retrieves approved, active canonical business rules for policy enforcement and context retrieval.
    GUARANTEE: Unapproved 'pending_review' candidate rules are NEVER returned.
    
    Args:
        client_id: The unique tenant / client identifier.
        process_name: Optional process context (e.g. 'mrp', 'inventory'). Returns process rules + general rules.
        reviewer_user_id: Identifier of the caller.
    """
    principal = Principal(client_id=client_id, user_id=reviewer_user_id)
    rules = tools.get_active_rules(
        client_id=client_id,
        process_name=process_name,
        principal=principal
    )
    
    output = {
        "client_id": client_id,
        "process_name": process_name or "all",
        "active_rules_count": len(rules),
        "rules": [r.model_dump() for r in rules]
    }
    return json.dumps(output, indent=2)

if __name__ == "__main__":
    logger.info("Starting Process Memory MCP Server (stdio transport)...")
    mcp.run(transport="stdio")
