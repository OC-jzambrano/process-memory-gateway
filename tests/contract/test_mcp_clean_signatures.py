import inspect
from server import (
    remember_company_instruction,
    list_memory_candidates,
    review_memory_candidate,
    get_company_context,
    create_project_task
)

def test_public_mcp_tools_contain_no_caller_controlled_identity_args():
    """
    Public MCP tools must never accept caller-controlled client_id,
    reviewer, role, or principal arguments. Identity is strictly derived
    from authenticated request context.
    """
    forbidden_args = {"client_id", "company_id", "reviewer", "reviewer_user_id", "role", "principal", "actor"}

    tools = [
        remember_company_instruction,
        list_memory_candidates,
        review_memory_candidate,
        get_company_context,
        create_project_task
    ]

    for tool_func in tools:
        sig = inspect.signature(tool_func)
        param_names = set(sig.parameters.keys())
        overlap = param_names.intersection(forbidden_args)
        assert not overlap, f"Tool '{tool_func.__name__}' exposes forbidden identity parameters: {overlap}"
