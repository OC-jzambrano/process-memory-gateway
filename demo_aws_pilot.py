"""Offline demonstration of the company-memory MCP orchestration flow."""

from src.api.auth_context import set_current_context
from src.api.service import HostedProcessMemoryService
from src.models.enums import CompanyStatus, MembershipStatus, RoleType
from src.models.schemas import (
    Company,
    Membership,
    OrchestrationToolCall,
    RequestContext,
    User,
)
from src.orchestration.bedrock_orchestrator import BedrockOrchestrator
from src.storage.repository import MemoryRepository


def run_pilot_demo() -> None:
    repo = MemoryRepository()
    service = HostedProcessMemoryService(
        repo=repo,
        orchestrator=BedrockOrchestrator(
            mock_handler=lambda **_: OrchestrationToolCall(
                server_id="odoo-main",
                tool_name="create_record",
                arguments={
                    "model": "project.task",
                    "values": {"name": "[ENG] Refactor database schema"},
                },
            )
        ),
    )

    repo.upsert_company(
        Company(
            company_id="pilot_company",
            company_slug="pilot_company",
            name="Pilot Company",
            status=CompanyStatus.ACTIVE,
        )
    )
    repo.upsert_user(
        User(user_id="demo_owner", email="owner@example.com", name="Demo Owner")
    )
    repo.upsert_membership(
        Membership(
            membership_id="mem_pilot",
            company_id="pilot_company",
            user_id="demo_owner",
            role=RoleType.OWNER,
            status=MembershipStatus.ACTIVE,
        )
    )
    set_current_context(
        RequestContext(
            company_id="pilot_company",
            company_slug="pilot_company",
            user_id="demo_owner",
            email="owner@example.com",
            role=RoleType.OWNER,
        )
    )

    try:
        staged = service.remember_company_instruction(
            "Engineering tasks must always start with [ENG] tag."
        )
        reviewed = service.review_memory_candidate(
            candidate_id=staged.candidate_id, decision="approve"
        )
        service.register_downstream_mcp(
            server_id="odoo-main",
            endpoint="mock://odoo",
            transport="internal_mock",
            available_tools=[
                {
                    "name": "create_record",
                    "description": "Create an Odoo record",
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
        result = service.run_downstream_request(
            user_request="Refactor database schema",
            action_context={
                "system": "odoo",
                "application": "project",
                "resource": "project.task",
                "operation": "create",
            },
        )
        print(f"Approved rule: {reviewed.rule_id}")
        print(f"Downstream result: {result.result}")
    finally:
        set_current_context(None)


if __name__ == "__main__":
    run_pilot_demo()
