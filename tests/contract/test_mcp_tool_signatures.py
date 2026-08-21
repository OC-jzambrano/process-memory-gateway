import inspect
from src.api.memory_tools import ProcessMemoryTools

def test_extract_tool_signature():
    """Tool 1: extract_memory_candidates parameter names."""
    sig = inspect.signature(ProcessMemoryTools.extract_memory_candidates)
    params = list(sig.parameters.keys())
    assert "interaction_text" in params
    assert "client_id" in params
    assert "process_name" in params
    assert "source_type" in params
    assert "principal" in params

def test_get_candidates_tool_signature():
    """Tool 2: get_candidate_rules signature."""
    sig = inspect.signature(ProcessMemoryTools.get_candidate_rules)
    params = list(sig.parameters.keys())
    assert "client_id" in params
    assert "status" in params
    assert "process_name" in params
    assert "principal" in params

def test_review_tool_signature():
    """Tool 3: review_candidate_rule signature."""
    sig = inspect.signature(ProcessMemoryTools.review_candidate_rule)
    params = list(sig.parameters.keys())
    assert "candidate_id" in params
    assert "decision" in params
    assert "reviewer" in params
    assert "client_id" in params
    assert "edited_rule_text" in params
    assert "notes" in params
    assert "principal" in params

def test_get_active_rules_tool_signature():
    """Tool 4: get_active_rules signature."""
    sig = inspect.signature(ProcessMemoryTools.get_active_rules)
    params = list(sig.parameters.keys())
    assert "client_id" in params
    assert "process_name" in params
    assert "principal" in params

def test_all_four_tools_exist():
    """The 4 MCP tools must always exist on ProcessMemoryTools."""
    tools = ProcessMemoryTools
    assert callable(getattr(tools, "extract_memory_candidates", None))
    assert callable(getattr(tools, "get_candidate_rules", None))
    assert callable(getattr(tools, "review_candidate_rule", None))
    assert callable(getattr(tools, "get_active_rules", None))

def test_all_tools_have_docstrings():
    """Every MCP tool must have a docstring (used as tool description by agents)."""
    tools = ProcessMemoryTools
    for name in ["extract_memory_candidates", "get_candidate_rules", "review_candidate_rule", "get_active_rules"]:
        method = getattr(tools, name)
        assert method.__doc__ is not None, f"Tool {name} is missing its docstring"
        assert len(method.__doc__.strip()) > 20, f"Tool {name} docstring is too short"
