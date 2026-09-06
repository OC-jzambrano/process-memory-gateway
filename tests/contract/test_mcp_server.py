import json
import uuid
import pytest
from src.api.memory_tools import ProcessMemoryTools
from src.storage.repository import MemoryRepository
from src.extractor.service import ProcessMemoryExtractorService
from server import (
    mcp,
    remember_company_instruction,
    list_memory_candidates,
    review_memory_candidate,
    get_company_context,
    create_project_task
)
from src.api.auth_context import set_current_context, RequestContext
from src.models.schemas import Company, User, Membership
from src.models.enums import RoleType, CompanyStatus, MembershipStatus

@pytest.fixture
def clean_context():
    cid = f"test_co_{uuid.uuid4().hex[:8]}"
    repo = MemoryRepository()
    repo.upsert_company(Company(
        company_id=cid,
        company_slug=cid,
        name="Test Company",
        status=CompanyStatus.ACTIVE
    ))
    repo.upsert_user(User(
        user_id="test_user",
        email="test@example.com",
        name="Test User",
        status="active"
    ))
    repo.upsert_membership(Membership(
        membership_id=f"mem_{cid}",
        company_id=cid,
        user_id="test_user",
        role=RoleType.OWNER,
        status=MembershipStatus.ACTIVE
    ))
    ctx = RequestContext(
        company_id=cid,
        company_slug=cid,
        user_id="test_user",
        email="test@example.com",
        role=RoleType.OWNER
    )
    set_current_context(ctx)
    from src.integrations.mock_executor import MockTaskExecutor
    from server import service
    old_executor = service._injected_executor
    service._injected_executor = MockTaskExecutor(default_project_id=142)
    yield ctx
    set_current_context(None)
    service._injected_executor = old_executor

def test_exactly_five_discoverable_tools():
    """Verify that FastMCP exposes exactly the five agreed public tools."""
    tool_names = set(mcp._tool_manager._tools.keys())
    expected_tools = {
        "remember_company_instruction",
        "list_memory_candidates",
        "review_memory_candidate",
        "get_company_context",
        "create_project_task"
    }
    assert tool_names == expected_tools, f"Expected exactly 5 tools {expected_tools}, found {tool_names}"

def test_internal_process_memory_tools_lifecycle():
    """Verify legacy internal ProcessMemoryTools service layer works correctly."""
    repo = MemoryRepository()
    extractor = ProcessMemoryExtractorService()
    tools = ProcessMemoryTools(repo=repo, extractor=extractor)
    cid = f"mcp_test_{uuid.uuid4().hex[:8]}"

    # 1. Extract
    result = tools.extract_memory_candidates(
        interaction_text="BOMs must include version numbers.",
        client_id=cid
    )
    assert len(result.candidates) >= 1
    candidate_id = result.candidates[0].candidate_id

    # 2. Get pending candidates
    candidates = tools.get_candidate_rules(client_id=cid)
    assert len(candidates) >= 1

    # 3. Review candidate
    rule = tools.review_candidate_rule(
        candidate_id=candidate_id,
        decision="approve",
        reviewer="juan_zambrano",
        client_id=cid,
        notes="Approved via internal tool"
    )
    assert rule is not None
    assert rule.version == 1

    # 4. Get active rules
    active_rules = tools.get_active_rules(client_id=cid)
    assert len(active_rules) == 1
    assert active_rules[0].source_candidate_id == candidate_id

def test_public_five_tools_lifecycle(clean_context):
    """Verify end-to-end execution of the 5 public tools."""
    # 1. remember_company_instruction
    stage_json = remember_company_instruction(
        instruction_text="Every task must have acceptance criteria."
    )
    stage_data = json.loads(stage_json)
    assert stage_data["status"] == "staged"
    candidate_id = stage_data["candidate_id"]

    # 2. list_memory_candidates
    candidates_json = list_memory_candidates(status="pending_review")
    candidates = json.loads(candidates_json)
    assert len(candidates) >= 1

    # 3. review_memory_candidate
    review_json = review_memory_candidate(
        candidate_id=candidate_id,
        decision="approve"
    )
    review_data = json.loads(review_json)
    assert review_data["status"] == "approved"
    assert review_data["rule_id"] is not None

    # 4. get_company_context
    context_json = get_company_context(system="odoo", application="project", resource="project.task", operation="create")
    context_data = json.loads(context_json)
    assert len(context_data["rules"]) >= 1

    # 5. create_project_task
    task_json = create_project_task(
        title="[PM-TEST] Public tool task",
        description="Verify public task tool works",
        definition_of_done=["Task is verified"],
        correlation_id=f"corr_{uuid.uuid4().hex}"
    )
    task_data = json.loads(task_json)
    assert task_data["status"] in ("created", "needs_clarification")
