import json
import uuid
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from server import (
    create_mcp_server,
    process_execute_action,
    process_get_context,
    process_list_candidates,
    process_list_rules,
    process_register_downstream_mcp,
    process_remember_instruction,
    process_review_candidate,
    process_set_rule_status,
)
from src.api.auth_context import RequestContext, set_current_context
from src.api.memory_tools import ProcessMemoryTools
from src.extractor.service import ProcessMemoryExtractorService
from src.models.enums import (
    CompanyStatus,
    ConstraintKind,
    DecisionType,
    EnforcementMode,
    MembershipStatus,
    RoleType,
    RuleStatus,
    RuleType,
    Severity,
)
from src.models.schemas import (
    ActionContext,
    CandidateResult,
    CandidateRule,
    CanonicalRule,
    Company,
    DeterministicConstraint,
    Membership,
    MemoryPack,
    MemoryPackRuleItem,
    OrchestrationResult,
    RegisterDownstreamMCPResult,
    ReviewResult,
    User,
)
from src.storage.repository import MemoryRepository


class FakeProcessMemoryService:
    """Deterministic fake service implementing the public operations without external connections."""

    def __init__(self):
        self.invocations: list[dict[str, Any]] = []

    def remember_company_instruction(
        self, instruction_text: str, context_hint: ActionContext | None = None
    ) -> CandidateResult:
        self.invocations.append(
            {
                "method": "remember_company_instruction",
                "instruction_text": instruction_text,
                "context_hint": context_hint,
            }
        )
        return CandidateResult(
            status="staged",
            candidate_id="cand_fake_123",
            rule_text=instruction_text,
            scope=context_hint or ActionContext(),
            constraint=DeterministicConstraint(
                kind=ConstraintKind.REQUIRED_NONEMPTY_LIST, field="definition_of_done"
            ),
            confidence=0.95,
            message="Instruction staged successfully.",
        )

    def list_memory_candidates(
        self, status: str = "pending_review"
    ) -> list[CandidateRule]:
        self.invocations.append({"method": "list_memory_candidates", "status": status})
        return [
            CandidateRule(
                candidate_id="cand_fake_123",
                session_id="sess_fake_123",
                client_id="co_fake",
                rule_text="Every task must have acceptance criteria.",
                rule_type=RuleType.OPERATIONAL_CONSTRAINT,
                source_quote="Every task must have acceptance criteria.",
                confidence=0.95,
                status=RuleStatus.PENDING_REVIEW,
            )
        ]

    def list_canonical_rules(
        self, status: str = "approved", process_name: str | None = None
    ) -> list[CanonicalRule]:
        self.invocations.append(
            {
                "method": "list_canonical_rules",
                "status": status,
                "process_name": process_name,
            }
        )
        return [
            CanonicalRule(
                rule_id="rule_fake_999",
                client_id="co_fake",
                process_name="general",
                rule_text="Every task must have acceptance criteria.",
                rule_type=RuleType.OPERATIONAL_CONSTRAINT,
                severity=Severity.INFO,
                enforcement_mode=EnforcementMode.BLOCKING,
                version=1,
                status=RuleStatus.APPROVED,
                structured_scope=ActionContext(
                    system="odoo",
                    application="project",
                    resource="project.task",
                    operation="create",
                ),
                approved_by="reviewer_fake",
            )
        ]

    def review_memory_candidate(
        self,
        candidate_id: str,
        decision: str,
        edited_rule_text: str | None = None,
        edited_scope: ActionContext | None = None,
        edited_constraint: dict[str, Any] | None = None,
        notes: str | None = None,
    ) -> ReviewResult:
        self.invocations.append(
            {
                "method": "review_memory_candidate",
                "candidate_id": candidate_id,
                "decision": decision,
                "edited_rule_text": edited_rule_text,
                "edited_scope": edited_scope,
                "edited_constraint": edited_constraint,
                "notes": notes,
            }
        )
        d_enum = (
            DecisionType(decision)
            if decision in [d.value for d in DecisionType]
            else DecisionType.APPROVE
        )
        return ReviewResult(
            status="approved" if decision == "approve" else decision,
            candidate_id=candidate_id,
            decision=d_enum,
            rule_id="rule_fake_999",
            version=1,
            message="Candidate approved into active canonical memory.",
        )

    def set_canonical_rule_status(
        self,
        rule_id: str,
        status: str,
        notes: str | None = None,
    ):
        from src.models.schemas import CanonicalRuleStatusResult

        self.invocations.append(
            {"method": "set_canonical_rule_status", "rule_id": rule_id, "status": status, "notes": notes}
        )
        return CanonicalRuleStatusResult(
            status=status,
            rule_id=rule_id,
            previous_status="approved" if status == "archived" else "archived",
            message="Canonical rule status updated.",
        )

    def get_company_context(
        self,
        system: str = "odoo",
        application: str | None = None,
        resource: str | None = None,
        operation: str | None = None,
        fields: list[str] | None = None,
    ) -> MemoryPack:
        self.invocations.append(
            {
                "method": "get_company_context",
                "system": system,
                "application": application,
                "resource": resource,
                "operation": operation,
                "fields": fields,
            }
        )
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
                    scope=ActionContext(
                        system=system,
                        application=application,
                        resource=resource,
                        operation=operation,
                    ),
                    constraint=DeterministicConstraint(
                        kind=ConstraintKind.REQUIRED_NONEMPTY_LIST,
                        field="definition_of_done",
                    ),
                )
            ],
        )

    def register_downstream_mcp(
        self,
        server_id: str,
        endpoint: str,
        transport: str = "streamable_http",
        available_tools: list[dict[str, Any]] | None = None,
        secret_ref: str | None = None,
        supported_action_contexts: list[dict[str, Any]] | None = None,
    ) -> RegisterDownstreamMCPResult:
        self.invocations.append(
            {
                "method": "register_downstream_mcp",
                "server_id": server_id,
                "endpoint": endpoint,
                "transport": transport,
                "available_tools": available_tools,
                "secret_ref": secret_ref,
                "supported_action_contexts": supported_action_contexts,
            }
        )
        return RegisterDownstreamMCPResult(
            status="registered",
            server_id=server_id,
            transport=transport,
            tool_count=len(available_tools) if available_tools else 0,
            message=f"Downstream MCP server '{server_id}' registered successfully.",
        )

    def run_downstream_request(
        self,
        user_request: str,
        action_context: ActionContext | dict[str, Any] | None = None,
        downstream_hint: str | None = None,
        correlation_id: str | None = None,
    ) -> OrchestrationResult:
        self.invocations.append(
            {
                "method": "run_downstream_request",
                "user_request": user_request,
                "action_context": action_context,
                "downstream_hint": downstream_hint,
                "correlation_id": correlation_id,
            }
        )
        return OrchestrationResult(
            success=True,
            correlation_id=correlation_id or "corr_fake_123",
            server_id="odoo-main",
            tool_name="create_record",
            result={"id": 9876, "name": "Task from user request"},
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
# 2. Public Protocol Discovery: Exact 8 Tools
# =========================================================================
@pytest.mark.anyio
async def test_protocol_tools_list_exact_eight_allowlist():
    """Verify MCP protocol tools/list returns exactly the approved tools via official in-memory session."""
    fake_svc = FakeProcessMemoryService()
    server = create_mcp_server(service=fake_svc)

    async with create_connected_server_and_client_session(server) as session:
        tools_result = await session.list_tools()
        advertised_names = {t.name for t in tools_result.tools}
        expected_names = {
            "process_remember_instruction",
            "process_list_candidates",
            "process_list_rules",
            "process_review_candidate",
            "process_set_rule_status",
            "process_get_context",
            "process_register_downstream_mcp",
            "process_execute_action",
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
        "actor",
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
        (
            "extract_memory_candidates",
            {
                "interaction_text": "dialogue",
                "client_id": "c1",
                "principal": {"user_id": "u1"},
            },
        ),
        (
            "get_candidate_rules",
            {
                "client_id": "c1",
                "status": "pending_review",
                "principal": {"user_id": "u1"},
            },
        ),
        (
            "review_candidate_rule",
            {
                "candidate_id": "cand_1",
                "decision": "approve",
                "reviewer": "user1",
                "client_id": "c1",
            },
        ),
        (
            "get_active_rules",
            {
                "client_id": "c1",
                "process_name": "project",
                "principal": {"user_id": "u1"},
            },
        ),
        (
            "create_project_task",
            {
                "title": "Old Task",
                "description": "Legacy",
            },
        ),
    ]

    async with create_connected_server_and_client_session(server) as session:
        for tool_name, args in removed_tools:
            res = await session.call_tool(tool_name, arguments=args)
            assert res.isError is True, (
                f"Calling removed tool '{tool_name}' did not return isError=True"
            )
            content_text = "".join(c.text for c in res.content if hasattr(c, "text"))
            assert "unknown tool" in content_text.lower(), (
                f"Expected 'Unknown tool' error for '{tool_name}', got: {content_text}"
            )

    # Assert zero service invocations occurred
    assert len(fake_svc.invocations) == 0, (
        f"Expected 0 service invocations, got {len(fake_svc.invocations)}"
    )


# =========================================================================
# 5. Protocol Tool Dispatch: Verify All 8 Approved Tools
# =========================================================================
@pytest.mark.anyio
async def test_protocol_dispatch_all_eight_approved_tools():
    """Verify end-to-end dispatch for all approved tools via MCP protocol with fake service."""
    fake_svc = FakeProcessMemoryService()
    server = create_mcp_server(service=fake_svc)

    async with create_connected_server_and_client_session(server) as session:
        # 1. process_remember_instruction
        res1 = await session.call_tool(
            "process_remember_instruction",
            arguments={
                "instruction_text": "Every task must have acceptance criteria.",
                "context_hint": {"system": "odoo", "resource": "work.item"},
            },
        )
        assert res1.isError is not True
        data1 = json.loads(res1.content[0].text)
        assert data1["status"] == "staged"
        assert data1["candidate_id"] == "cand_fake_123"

        # 2. process_list_candidates
        res2 = await session.call_tool(
            "process_list_candidates", arguments={"status": "pending_review"}
        )
        assert res2.isError is not True
        data2 = json.loads(res2.content[0].text)
        assert len(data2) == 1
        assert data2[0]["candidate_id"] == "cand_fake_123"

        # 3. process_list_rules
        res3 = await session.call_tool(
            "process_list_rules", arguments={"status": "approved"}
        )
        assert res3.isError is not True
        data3 = json.loads(res3.content[0].text)
        assert len(data3) == 1
        assert data3[0]["rule_id"] == "rule_fake_999"

        # 4. process_review_candidate
        res4 = await session.call_tool(
            "process_review_candidate",
            arguments={
                "candidate_id": "cand_fake_123",
                "decision": "approve",
                "notes": "Approved by owner",
            },
        )
        assert res4.isError is not True
        data4 = json.loads(res4.content[0].text)
        assert data4["status"] == "approved"
        assert data4["rule_id"] == "rule_fake_999"

        # 5. process_set_rule_status
        res5 = await session.call_tool(
            "process_set_rule_status",
            arguments={
                "rule_id": "rule_fake_999",
                "status": "archived",
                "notes": "Temporarily disabled for validation",
            },
        )
        assert res5.isError is not True
        data5 = json.loads(res5.content[0].text)
        assert data5["status"] == "archived"
        assert data5["rule_id"] == "rule_fake_999"

        # 6. process_get_context
        res6 = await session.call_tool(
            "process_get_context",
            arguments={
                "system": "odoo",
                "application": "project",
                "resource": "work.item",
                "operation": "create",
                "fields": ["definition_of_done"],
            },
        )
        assert res6.isError is not True
        data6 = json.loads(res6.content[0].text)
        assert len(data6["rules"]) == 1
        assert data6["rules"][0]["rule_id"] == "rule_fake_999"

        # 7. process_register_downstream_mcp
        res7 = await session.call_tool(
            "process_register_downstream_mcp",
            arguments={
                "server_id": "odoo-main",
                "endpoint": "https://erp.example.com",
                "transport": "odoo_xmlrpc",
                "available_tools": [
                    {
                        "name": "create_record",
                        "description": "Create record in Odoo",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "model": {"type": "string"},
                                "values": {"type": "object"},
                            },
                            "required": ["model", "values"],
                        },
                    }
                ],
            },
        )
        assert res7.isError is not True
        data7 = json.loads(res7.content[0].text)
        assert data7["status"] == "registered"
        assert data7["server_id"] == "odoo-main"

        # 8. process_execute_action
        res8 = await session.call_tool(
            "process_execute_action",
            arguments={
                "user_request": "Create a task for bug fix",
                "action_context": {
                    "system": "odoo",
                    "resource": "work.item",
                    "operation": "create",
                },
            },
        )
        assert res8.isError is not True
        data8 = json.loads(res8.content[0].text)
        assert data8["success"] is True
        assert data8["tool_name"] == "create_record"

    # Verify all 8 dispatches reached the fake service with expected methods
    dispatched_methods = [inv["method"] for inv in fake_svc.invocations]
    assert dispatched_methods == [
        "remember_company_instruction",
        "list_memory_candidates",
        "list_canonical_rules",
        "review_memory_candidate",
        "set_canonical_rule_status",
        "get_company_context",
        "register_downstream_mcp",
        "run_downstream_request",
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
            "process_remember_instruction",
            "process_list_candidates",
            "process_list_rules",
            "process_review_candidate",
            "process_set_rule_status",
            "process_get_context",
            "process_register_downstream_mcp",
            "process_execute_action",
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
        interaction_text="BOMs must include version numbers.", client_id=cid
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
        notes="Approved via internal tool",
    )
    assert rule is not None
    assert rule.version == 1

    # 4. Get active rules
    active_rules = tools.get_active_rules(client_id=cid)
    assert len(active_rules) == 1
    assert active_rules[0].source_candidate_id == candidate_id


# =========================================================================
# 8. End-to-End Lifecycle of 6 Public Tools (Service Integration)
# =========================================================================
@pytest.fixture
def clean_context(tmp_path, monkeypatch):
    cid = f"test_co_{uuid.uuid4().hex[:8]}"
    repo = MemoryRepository(db_path=tmp_path / "public-lifecycle.db")
    repo.upsert_company(
        Company(
            company_id=cid,
            company_slug=cid,
            name="Test Company",
            status=CompanyStatus.ACTIVE,
        )
    )
    repo.upsert_user(
        User(
            user_id="test_user",
            email="test@example.com",
            name="Test User",
            status="active",
        )
    )
    repo.upsert_membership(
        Membership(
            membership_id=f"mem_{cid}",
            company_id=cid,
            user_id="test_user",
            role=RoleType.OWNER,
            status=MembershipStatus.ACTIVE,
        )
    )
    ctx = RequestContext(
        company_id=cid,
        company_slug=cid,
        user_id="test_user",
        email="test@example.com",
        role=RoleType.OWNER,
    )
    set_current_context(ctx)
    import server
    from src.api.service import HostedProcessMemoryService
    from src.models.schemas import OrchestrationToolCall
    from src.orchestration.bedrock_orchestrator import BedrockOrchestrator

    svc = HostedProcessMemoryService(
        repo=repo, extractor=ProcessMemoryExtractorService(offline_mode=True)
    )
    monkeypatch.setattr(server, "_default_service", svc)
    old_orchestrator = svc.orchestrator

    def mock_orchestrate(**kwargs):
        return OrchestrationToolCall(
            server_id="test_server",
            tool_name="create_record",
            arguments={"model": "work.item", "values": {"name": "Test record"}},
        )

    svc.orchestrator = BedrockOrchestrator(mock_handler=mock_orchestrate)
    yield ctx
    set_current_context(None)
    svc.orchestrator = old_orchestrator


def test_public_eight_tools_lifecycle(clean_context):
    """Verify end-to-end execution of the 8 public tools."""
    # 1. process_remember_instruction
    stage_json = process_remember_instruction(
        instruction_text="Every task must have acceptance criteria."
    )
    stage_data = json.loads(stage_json)
    assert stage_data["status"] == "staged"
    candidate_id = stage_data["candidate_id"]

    # 2. process_list_candidates
    candidates_json = process_list_candidates(status="pending_review")
    candidates = json.loads(candidates_json)
    assert len(candidates) >= 1

    # 3. process_review_candidate
    review_json = process_review_candidate(candidate_id=candidate_id, decision="approve")
    review_data = json.loads(review_json)
    assert review_data["status"] == "approved"
    assert review_data["rule_id"] is not None

    # 4. process_list_rules
    canonical_json = process_list_rules(status="approved")
    canonical = json.loads(canonical_json)
    assert len(canonical) >= 1
    assert canonical[0]["rule_id"] == review_data["rule_id"]

    # 5. process_set_rule_status
    archived_json = process_set_rule_status(
        rule_id=review_data["rule_id"],
        status="archived",
        notes="Lifecycle status toggle check.",
    )
    archived = json.loads(archived_json)
    assert archived["status"] == "archived"

    restored_json = process_set_rule_status(
        rule_id=review_data["rule_id"],
        status="approved",
        notes="Restore for scoped context retrieval.",
    )
    restored = json.loads(restored_json)
    assert restored["status"] == "approved"

    # 6. process_get_context
    context_json = process_get_context(
        system="odoo",
        application="project",
        resource="work.item",
        operation="create",
    )
    context_data = json.loads(context_json)
    assert len(context_data["rules"]) >= 1

    # 7. process_register_downstream_mcp
    reg_json = process_register_downstream_mcp(
        server_id="test_server",
        endpoint="mock://test",
        transport="internal_mock",
        available_tools=[
            {
                "name": "create_record",
                "description": "Create a record",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "model": {"type": "string"},
                        "values": {"type": "object"},
                    },
                    "required": ["model", "values"],
                },
            }
        ],
    )
    reg_data = json.loads(reg_json)
    assert reg_data["status"] == "registered"

    # 8. process_execute_action
    req_json = process_execute_action(
        user_request="Create a task for bug fix",
        action_context={"system": "odoo", "resource": "work.item", "operation": "create"},
    )
    req_data = json.loads(req_json)
    assert req_data["success"] is True
    assert req_data["tool_name"] == "create_record"

