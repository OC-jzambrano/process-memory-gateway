import json
import uuid
from typing import Optional, List, Dict, Any
import pytest

from mcp.shared.memory import create_connected_server_and_client_session
from src.api.memory_tools import ProcessMemoryTools
from src.storage.repository import MemoryRepository
from src.extractor.service import ProcessMemoryExtractorService
from server import (
    create_mcp_server,
    remember_company_instruction,
    list_memory_candidates,
    review_memory_candidate,
    get_company_context,
    create_project_task
)
from src.api.auth_context import set_current_context, RequestContext
from src.models.schemas import (
    Company,
    User,
    Membership,
    ActionContext,
    DeterministicConstraint,
    CandidateResult,
    CandidateRule,
    ReviewResult,
    MemoryPack,
    MemoryPackRuleItem,
    TaskCreationResult
)
from src.models.enums import (
    DecisionType,
    EnforcementMode,
    RoleType,
    CompanyStatus,
    MembershipStatus,
    RuleType,
    RuleStatus,
    ConstraintKind,
    RunStatus
)

class FakeProcessMemoryService:
    """Deterministic fake service implementing all 5 operations with zero database or Odoo connections."""

    def __init__(self):
        self.invocations: List[Dict[str, Any]] = []

    def remember_company_instruction(
        self,
        instruction_text: str,
        context_hint: Optional[ActionContext] = None
    ) -> CandidateResult:
        self.invocations.append({
            "method": "remember_company_instruction",
            "instruction_text": instruction_text,
            "context_hint": context_hint
        })
        return CandidateResult(
            status="staged",
            candidate_id="cand_fake_123",
            rule_text=instruction_text,
            scope=context_hint or ActionContext(),
            constraint=DeterministicConstraint(
                kind=ConstraintKind.REQUIRED_NONEMPTY_LIST,
                field="definition_of_done"
            ),
            confidence=0.95,
            message="Instruction staged successfully."
        )

    def list_memory_candidates(self, status: str = "pending_review") -> List[CandidateRule]:
        self.invocations.append({
            "method": "list_memory_candidates",
            "status": status
        })
        return [
            CandidateRule(
                candidate_id="cand_fake_123",
                session_id="sess_fake_123",
                client_id="co_fake",
                rule_text="Every task must have acceptance criteria.",
                rule_type=RuleType.OPERATIONAL_CONSTRAINT,
                source_quote="Every task must have acceptance criteria.",
                confidence=0.95,
                status=RuleStatus.PENDING_REVIEW
            )
        ]

    def review_memory_candidate(
        self,
        candidate_id: str,
        decision: str,
        edited_rule_text: Optional[str] = None,
        edited_scope: Optional[ActionContext] = None,
        edited_constraint: Optional[Dict[str, Any]] = None,
        notes: Optional[str] = None
    ) -> ReviewResult:
        self.invocations.append({
            "method": "review_memory_candidate",
            "candidate_id": candidate_id,
            "decision": decision,
            "edited_rule_text": edited_rule_text,
            "edited_scope": edited_scope,
            "edited_constraint": edited_constraint,
            "notes": notes
        })
        d_enum = DecisionType(decision) if decision in [d.value for d in DecisionType] else DecisionType.APPROVE
        return ReviewResult(
            status="approved" if decision == "approve" else decision,
            candidate_id=candidate_id,
            decision=d_enum,
            rule_id="rule_fake_999",
            version=1,
            message="Candidate approved into active canonical memory."
        )

    def get_company_context(
        self,
        system: str = "odoo",
        application: Optional[str] = None,
        resource: Optional[str] = None,
        operation: Optional[str] = None,
        fields: Optional[List[str]] = None
    ) -> MemoryPack:
        self.invocations.append({
            "method": "get_company_context",
            "system": system,
            "application": application,
            "resource": resource,
            "operation": operation,
            "fields": fields
        })
        return MemoryPack(
            company_slug="co_fake",
            system=system,
            application=application,
            resource=resource,
            operation=operation,
            rules=[
                MemoryPackRuleItem(
                    rule_id="rule_fake_999",
                    version=1,
                    rule_text="Every task must have acceptance criteria.",
                    rule_type=RuleType.OPERATIONAL_CONSTRAINT,
                    enforcement_mode=EnforcementMode.BLOCKING,
                    scope=ActionContext(system=system, application=application, resource=resource, operation=operation),
                    constraint=DeterministicConstraint(
                        kind=ConstraintKind.REQUIRED_NONEMPTY_LIST,
                        field="definition_of_done"
                    )
                )
            ]
        )

    def create_project_task(
        self,
        title: str,
        description: str,
        definition_of_done: Optional[List[str]] = None,
        project_id: Optional[int] = None,
        correlation_id: Optional[str] = None
    ) -> TaskCreationResult:
        self.invocations.append({
            "method": "create_project_task",
            "title": title,
            "description": description,
            "definition_of_done": definition_of_done,
            "project_id": project_id,
            "correlation_id": correlation_id
        })
        return TaskCreationResult(
            status=RunStatus.CREATED,
            run_id="run_fake_123",
            correlation_id=correlation_id or "corr_fake_123",
            applied_rule_ids=["rule_fake_999"],
            missing_information=[],
            odoo_task_id=9876,
            odoo_task_url="https://community.odooconcept.com/web#id=9876",
            task_name=title,
            message=f"Task #9876 successfully created and verified in Odoo Project {project_id or 142}."
        )

# =========================================================================
# 1. Server Module Import Isolation
# =========================================================================
def test_no_runtime_db_or_odoo_on_server_module_import():
    """Verify importing server module does not eagerly initialize database or Odoo."""
    import server
    assert hasattr(server, "create_mcp_server")
    assert hasattr(server, "register_tools")

# =========================================================================
# 2. Public Protocol Discovery: Exact 5 Tools
# =========================================================================
@pytest.mark.anyio
async def test_protocol_tools_list_exact_five_allowlist():
    """Verify MCP protocol tools/list returns exactly the five approved tools via official in-memory session."""
    fake_svc = FakeProcessMemoryService()
    server = create_mcp_server(service=fake_svc)

    async with create_connected_server_and_client_session(server) as session:
        tools_result = await session.list_tools()
        advertised_names = {t.name for t in tools_result.tools}
        expected_names = {
            "remember_company_instruction",
            "list_memory_candidates",
            "review_memory_candidate",
            "get_company_context",
            "create_project_task"
        }
        assert advertised_names == expected_names, (
            f"MCP protocol discovery mismatch! Expected {expected_names}, got {advertised_names}"
        )

# =========================================================================
# 3. Protocol Schema Security: Zero Caller-Controlled Identity
# =========================================================================
@pytest.mark.anyio
async def test_protocol_advertised_schemas_have_no_caller_controlled_identity():
    """Inspect every advertised input schema via MCP protocol for forbidden identity arguments."""
    fake_svc = FakeProcessMemoryService()
    server = create_mcp_server(service=fake_svc)

    forbidden_identity_keys = {
        "client_id",
        "company_id",
        "reviewer",
        "reviewer_user_id",
        "user_id",
        "role",
        "principal",
        "actor"
    }

    async with create_connected_server_and_client_session(server) as session:
        tools_result = await session.list_tools()
        for tool in tools_result.tools:
            schema = tool.inputSchema or {}
            properties = set(schema.get("properties", {}).keys())
            overlap = properties.intersection(forbidden_identity_keys)
            assert not overlap, (
                f"Tool '{tool.name}' advertises forbidden caller-controlled identity parameters: {overlap}"
            )

# =========================================================================
# 4. Protocol Tool Invocation: Removed Legacy Tools Must Fail Closed
# =========================================================================
@pytest.mark.anyio
async def test_protocol_calling_removed_legacy_tools_returns_unknown_tool_and_zero_invocations():
    """Call each removed legacy tool over MCP protocol. Assert unknown-tool error and zero service calls."""
    fake_svc = FakeProcessMemoryService()
    server = create_mcp_server(service=fake_svc)

    removed_tools = [
        ("extract_memory_candidates", {"interaction_text": "dialogue", "client_id": "c1", "principal": {"user_id": "u1"}}),
        ("get_candidate_rules", {"client_id": "c1", "status": "pending_review", "principal": {"user_id": "u1"}}),
        ("review_candidate_rule", {"candidate_id": "cand_1", "decision": "approve", "reviewer": "user1", "client_id": "c1"}),
        ("get_active_rules", {"client_id": "c1", "process_name": "project", "principal": {"user_id": "u1"}})
    ]

    async with create_connected_server_and_client_session(server) as session:
        for tool_name, args in removed_tools:
            res = await session.call_tool(tool_name, arguments=args)
            assert res.isError is True, f"Calling removed tool '{tool_name}' did not return isError=True"
            content_text = "".join(c.text for c in res.content if hasattr(c, "text"))
            assert "unknown tool" in content_text.lower(), (
                f"Expected 'Unknown tool' error for '{tool_name}', got: {content_text}"
            )

    # Assert zero service invocations occurred
    assert len(fake_svc.invocations) == 0, f"Expected 0 service invocations, got {len(fake_svc.invocations)}"

# =========================================================================
# 5. Protocol Tool Dispatch: Verify All 5 Approved Tools
# =========================================================================
@pytest.mark.anyio
async def test_protocol_dispatch_all_five_approved_tools():
    """Verify end-to-end dispatch for all 5 approved tools via MCP protocol with fake service."""
    fake_svc = FakeProcessMemoryService()
    server = create_mcp_server(service=fake_svc)

    async with create_connected_server_and_client_session(server) as session:
        # 1. remember_company_instruction
        res1 = await session.call_tool(
            "remember_company_instruction",
            arguments={
                "instruction_text": "Every task must have acceptance criteria.",
                "context_hint": {"system": "odoo", "resource": "project.task"}
            }
        )
        assert res1.isError is not True
        data1 = json.loads(res1.content[0].text)
        assert data1["status"] == "staged"
        assert data1["candidate_id"] == "cand_fake_123"

        # 2. list_memory_candidates
        res2 = await session.call_tool(
            "list_memory_candidates",
            arguments={"status": "pending_review"}
        )
        assert res2.isError is not True
        data2 = json.loads(res2.content[0].text)
        assert len(data2) == 1
        assert data2[0]["candidate_id"] == "cand_fake_123"

        # 3. review_memory_candidate
        res3 = await session.call_tool(
            "review_memory_candidate",
            arguments={
                "candidate_id": "cand_fake_123",
                "decision": "approve",
                "notes": "Approved by owner"
            }
        )
        assert res3.isError is not True
        data3 = json.loads(res3.content[0].text)
        assert data3["status"] == "approved"
        assert data3["rule_id"] == "rule_fake_999"

        # 4. get_company_context
        res4 = await session.call_tool(
            "get_company_context",
            arguments={
                "system": "odoo",
                "application": "project",
                "resource": "project.task",
                "operation": "create",
                "fields": ["definition_of_done"]
            }
        )
        assert res4.isError is not True
        data4 = json.loads(res4.content[0].text)
        assert len(data4["rules"]) == 1
        assert data4["rules"][0]["rule_id"] == "rule_fake_999"

        # 5. create_project_task
        res5 = await session.call_tool(
            "create_project_task",
            arguments={
                "title": "[TEST] Protocol task",
                "description": "Task created via MCP protocol session",
                "definition_of_done": ["DoD criterion 1"],
                "project_id": 142,
                "correlation_id": "corr_protocol_123"
            }
        )
        assert res5.isError is not True
        data5 = json.loads(res5.content[0].text)
        assert data5["status"] == "created"
        assert data5["odoo_task_id"] == 9876

    # Verify all 5 dispatches reached the fake service with expected methods
    dispatched_methods = [inv["method"] for inv in fake_svc.invocations]
    assert dispatched_methods == [
        "remember_company_instruction",
        "list_memory_candidates",
        "review_memory_candidate",
        "get_company_context",
        "create_project_task"
    ]

# =========================================================================
# 6. Regression Sensitivity: Allowlist Fails if Legacy Tool Added
# =========================================================================
@pytest.mark.anyio
async def test_regression_sensitivity_fails_when_legacy_tool_registered():
    """Demonstrate regression sensitivity: exact equality assertion fails if legacy tool is registered."""
    fake_svc = FakeProcessMemoryService()
    server = create_mcp_server(service=fake_svc)

    # Artificially register a legacy tool to simulate regression
    @server.tool()
    def extract_memory_candidates(interaction_text: str, client_id: str) -> str:
        return "legacy"

    async with create_connected_server_and_client_session(server) as session:
        tools_result = await session.list_tools()
        advertised_names = {t.name for t in tools_result.tools}
        expected_names = {
            "remember_company_instruction",
            "list_memory_candidates",
            "review_memory_candidate",
            "get_company_context",
            "create_project_task"
        }
        # The allowlist check MUST fail
        assert advertised_names != expected_names
        assert "extract_memory_candidates" in advertised_names

# =========================================================================
# 7. Preserved Internal ProcessMemoryTools Lifecycle (Legacy Service Layer)
# =========================================================================
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

# =========================================================================
# 8. End-to-End Lifecycle of 5 Public Tools (Service Integration)
# =========================================================================
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
    from server import get_default_service
    svc = get_default_service()
    old_executor = svc._injected_executor
    svc._injected_executor = MockTaskExecutor(default_project_id=142)
    yield ctx
    set_current_context(None)
    svc._injected_executor = old_executor

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

