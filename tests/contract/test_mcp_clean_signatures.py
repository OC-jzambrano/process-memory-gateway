"""
Supplementary contract tests verifying Python-level function signatures for MCP tools.
"""

import inspect

import server
from server import (
    get_company_context,
    list_memory_candidates,
    register_downstream_mcp,
    remember_company_instruction,
    review_memory_candidate,
    run_downstream_request,
)


def test_public_mcp_tools_contain_no_caller_controlled_identity_args():
    """
    Public MCP tool functions must never accept caller-controlled client_id,
    reviewer, role, or principal arguments. Identity is strictly derived
    from authenticated request context.
    """
    forbidden_args = {
        "client_id",
        "company_id",
        "reviewer",
        "reviewer_user_id",
        "role",
        "principal",
        "actor",
    }

    tools = [
        remember_company_instruction,
        list_memory_candidates,
        review_memory_candidate,
        get_company_context,
        register_downstream_mcp,
        run_downstream_request,
    ]

    for tool_func in tools:
        sig = inspect.signature(tool_func)
        param_names = set(sig.parameters.keys())
        overlap = param_names.intersection(forbidden_args)
        assert not overlap, (
            f"Tool '{tool_func.__name__}' exposes forbidden identity parameters: {overlap}"
        )


def test_server_module_does_not_export_legacy_tools():
    """
    Legacy tool names must not be exported as module attributes or callables.
    """
    legacy_tools = [
        "extract_memory_candidates",
        "get_candidate_rules",
        "get_active_rules",
        "review_candidate_rule",
        "create_project_task",
    ]
    for legacy_name in legacy_tools:
        assert not hasattr(server, legacy_name), (
            f"Legacy tool '{legacy_name}' must not be exposed on server module"
        )
