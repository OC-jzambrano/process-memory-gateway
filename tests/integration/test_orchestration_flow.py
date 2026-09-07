import pytest

from src.api.auth_context import RequestContext, set_current_context
from src.api.service import HostedProcessMemoryService
from src.models.enums import (
    CompanyStatus,
    EnforcementMode,
    MembershipStatus,
    RoleType,
    RuleType,
    Severity,
)
from src.models.schemas import (
    ActionContext,
    CanonicalRule,
    Company,
    Membership,
    OrchestrationToolCall,
    User,
)
from src.orchestration.bedrock_orchestrator import BedrockOrchestrator
from src.storage.repository import MemoryRepository


@pytest.fixture
def multi_tenant_setup(tmp_path):
    repo = MemoryRepository(db_path=tmp_path / "orchestration_flow.db")

    # Setup Company A
    repo.upsert_company(Company(company_id="co_alpha", company_slug="alpha", name="Alpha Corp", status=CompanyStatus.ACTIVE))
    repo.upsert_user(User(user_id="user_alpha", email="alpha@test.com", name="Alpha Owner", status="active"))
    repo.upsert_membership(Membership(membership_id="m_alpha", company_id="co_alpha", user_id="user_alpha", role=RoleType.OWNER, status=MembershipStatus.ACTIVE))

    # Setup Company B
    repo.upsert_company(Company(company_id="co_beta", company_slug="beta", name="Beta Corp", status=CompanyStatus.ACTIVE))
    repo.upsert_user(User(user_id="user_beta", email="beta@test.com", name="Beta Owner", status="active"))
    repo.upsert_membership(Membership(membership_id="m_beta", company_id="co_beta", user_id="user_beta", role=RoleType.OWNER, status=MembershipStatus.ACTIVE))

    # Intercept orchestrator with deterministic mock
    observed_rule_ids = []

    def mock_synthesize(**kwargs):
        rules = kwargs.get("approved_rules", [])
        observed_rule_ids.extend(r.rule_id for r in rules)
        user_req = kwargs.get("user_request", "")
        # Emulate model adhering to rule: prefix with [ENG] if rule exists
        name = f"[ENG] {user_req}" if rules else user_req
        return OrchestrationToolCall(
            server_id="odoo-main",
            tool_name="create_record",
            arguments={"model": "project.task", "values": {"name": name}},
        )

    orchestrator = BedrockOrchestrator(mock_handler=mock_synthesize)
    service = HostedProcessMemoryService(repo=repo, orchestrator=orchestrator)
    return service, repo, observed_rule_ids


def test_full_orchestration_lifecycle_with_memory_enforcement(multi_tenant_setup):
    """
    Test end-to-end flow:
    1. Capture instruction -> pending_review
    2. Review and approve candidate -> canonical rule
    3. Register downstream MCP server
    4. Run downstream request -> synthesizes tool call adhering to memory, dispatches safely
    """
    service, _repo, observed_rule_ids = multi_tenant_setup

    # Authenticate as Alpha
    set_current_context(
        RequestContext(
            company_id="co_alpha",
            company_slug="alpha",
            user_id="user_alpha",
            email="alpha@test.com",
            role=RoleType.OWNER,
        )
    )

    try:
        # Step 1: Stage instruction
        staged = service.remember_company_instruction(
            instruction_text="Engineering tasks must always start with [ENG] tag.",
            context_hint=ActionContext(system="odoo", application="project", resource="project.task", operation="create"),
        )
        assert staged.status == "staged"
        cand_id = staged.candidate_id

        # Step 2: List and approve candidate
        candidates = service.list_memory_candidates(status="pending_review")
        assert any(c.candidate_id == cand_id for c in candidates)

        reviewed = service.review_memory_candidate(candidate_id=cand_id, decision="approve")
        assert reviewed.status == "approved"
        assert reviewed.rule_id is not None

        # Step 3: Verify company context retrieval returns active rule
        context = service.get_company_context(system="odoo", application="project", resource="project.task", operation="create")
        assert len(context.rules) == 1
        assert context.rules[0].rule_id == reviewed.rule_id

        # Unrelated approved memory must not be injected into this action.
        _repo.create_canonical_rule(
            CanonicalRule(
                rule_id="rule_finance_only",
                client_id="co_alpha",
                process_name="general",
                rule_text="Finance records require a controller review.",
                rule_type=RuleType.OPERATIONAL_CONSTRAINT,
                severity=Severity.INFO,
                enforcement_mode=EnforcementMode.ADVISORY,
                structured_scope=ActionContext(
                    system="odoo", application="accounting", resource="account.move", operation="create"
                ),
                approved_by="user_alpha",
            )
        )

        # Step 4: Register downstream MCP server
        reg_res = service.register_downstream_mcp(
            server_id="odoo-main",
            endpoint="mock://odoo-alpha",
            transport="internal_mock",
            available_tools=[
                {
                    "name": "create_record",
                    "description": "Creates record",
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
        assert reg_res.status == "registered"
        assert reg_res.server_id == "odoo-main"

        # Step 5: Run downstream request
        result = service.run_downstream_request(
            user_request="Refactor database schema",
            action_context=ActionContext(system="odoo", application="project", resource="project.task", operation="create"),
        )
        assert result.success is True
        assert observed_rule_ids == [reviewed.rule_id]
        assert result.server_id == "odoo-main"
        assert result.tool_name == "create_record"
        assert result.result["status"] == "mock_success"
        # Verify synthesized arguments adhered to company memory
        received = result.result["received_arguments"]
        assert received["values"]["name"] == "[ENG] Refactor database schema"

    finally:
        set_current_context(None)


def test_multi_tenant_downstream_isolation(multi_tenant_setup):
    """
    Company B cannot see, access, or call Company A's registered downstream MCP servers or memory.
    """
    service, repo, _observed_rule_ids = multi_tenant_setup

    # 1. Company A registers an internal mock MCP server
    set_current_context(RequestContext(company_id="co_alpha", company_slug="alpha", user_id="user_alpha", email="alpha@test.com", role=RoleType.OWNER))
    service.register_downstream_mcp(
        server_id="odoo-alpha-secret",
        endpoint="mock://alpha-private",
        transport="internal_mock",
        available_tools=[{"name": "private_tool", "description": "Private tool", "input_schema": {"type": "object"}}],
    )
    set_current_context(None)

    # 2. Company B authenticates
    set_current_context(RequestContext(company_id="co_beta", company_slug="beta", user_id="user_beta", email="beta@test.com", role=RoleType.OWNER))

    try:
        # Company B's registered server list must NOT include Company A's server
        beta_servers = repo.list_downstream_mcps(company_id="co_beta")
        assert len(beta_servers) == 0

        # Company B attempting to run downstream request against Company A's server fails
        result = service.run_downstream_request(
            user_request="Invoke secret tool",
            downstream_hint="odoo-alpha-secret",
        )
        assert result.success is False
        assert "No downstream MCP servers registered for company 'co_beta'" in (result.error or "")

    finally:
        set_current_context(None)
