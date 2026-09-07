import json
import logging
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from src.config import ALLOWED_HOSTS
from src.models.schemas import ActionContext

logger = logging.getLogger(__name__)

__all__ = [
    "create_mcp_server",
    "get_company_context",
    "get_default_server",
    "get_default_service",
    "list_memory_candidates",
    "register_downstream_mcp",
    "register_tools",
    "remember_company_instruction",
    "review_memory_candidate",
    "run_downstream_request",
]

# Lazy runtime dependencies - deferred to avoid initializing database at import time
_default_service = None
_default_server = None


def get_default_service():
    """Lazily initializes the production HostedProcessMemoryService on first runtime use."""
    global _default_service
    if _default_service is None:
        from src.api.service import HostedProcessMemoryService
        from src.extractor.service import ProcessMemoryExtractorService
        from src.storage.repository import MemoryRepository

        repo = MemoryRepository()
        extractor = ProcessMemoryExtractorService()
        _default_service = HostedProcessMemoryService(repo=repo, extractor=extractor)
    return _default_service


def register_tools(mcp_app: FastMCP, service: Any) -> None:
    """
    Registers exactly the six approved public MCP tools onto the given FastMCP instance.
    All caller identity is server-resolved from the authenticated request context.
    """

    # --- TOOL 1: REMEMBER COMPANY INSTRUCTION ---
    @mcp_app.tool()
    def remember_company_instruction(
        instruction_text: str, context_hint: dict[str, Any] | None = None
    ) -> str:
        """
        Stages a proposed company instruction or business rule into memory_candidates (pending_review).
        Does NOT activate or enforce the rule until approved by a company owner or reviewer.

        Args:
            instruction_text: Natural language statement of the company policy, constraint, or convention.
            context_hint: Optional structured dictionary indicating target system, application, resource, or field.

        Returns:
            JSON string containing CandidateResult with candidate ID, previewed scope, constraint, and status.
        """
        scope = ActionContext(**context_hint) if context_hint else None
        result = service.remember_company_instruction(
            instruction_text=instruction_text, context_hint=scope
        )
        return result.model_dump_json(indent=2)

    # --- TOOL 2: LIST MEMORY CANDIDATES ---
    @mcp_app.tool()
    def list_memory_candidates(status: str = "pending_review") -> str:
        """
        Lists staged memory candidates for the authenticated company awaiting human review.

        Args:
            status: Status filter, defaults to 'pending_review'.

        Returns:
            JSON string containing list of Candidate rules.
        """
        candidates = service.list_memory_candidates(status=status)
        return json.dumps([c.model_dump() for c in candidates], indent=2)

    # --- TOOL 3: REVIEW MEMORY CANDIDATE ---
    @mcp_app.tool()
    def review_memory_candidate(
        candidate_id: str,
        decision: Literal["approve", "edit", "reject"],
        edited_rule_text: str | None = None,
        edited_scope: dict[str, Any] | None = None,
        edited_constraint: dict[str, Any] | None = None,
        notes: str | None = None,
    ) -> str:
        """
        Processes human sign-off on a staged memory candidate.
        Approving promotes the candidate into an active, versioned Canonical Rule.

        Args:
            candidate_id: ID of the candidate to review (e.g. 'cand_abc123').
            decision: 'approve', 'edit', or 'reject'.
            edited_rule_text: Refined rule text if decision is 'edit'.
            edited_scope: Optional modified scope dictionary.
            edited_constraint: Optional modified deterministic constraint dictionary.
            notes: Optional audit notes explaining the review rationale.

        Returns:
            JSON string containing ReviewResult.
        """
        scope = ActionContext(**edited_scope) if edited_scope else None
        result = service.review_memory_candidate(
            candidate_id=candidate_id,
            decision=decision,
            edited_rule_text=edited_rule_text,
            edited_scope=scope,
            edited_constraint=edited_constraint,
            notes=notes,
        )
        return result.model_dump_json(indent=2)

    # --- TOOL 4: GET COMPANY CONTEXT (MEMORY PACK) ---
    @mcp_app.tool()
    def get_company_context(
        system: str = "odoo",
        application: str | None = None,
        resource: str | None = None,
        operation: str | None = None,
        fields: list[str] | None = None,
    ) -> str:
        """
        Retrieves a small, relevant Memory Pack of active canonical rules for the authenticated company.

        Args:
            system: Target system, defaults to 'odoo'.
            application: Target application, e.g. 'project'.
            resource: Target resource/model, e.g. 'project.task'.
            operation: Target operation, e.g. 'create'.
            fields: Optional list of field names.

        Returns:
            JSON string containing MemoryPack with active rules, versions, scopes, and constraints.
        """
        pack = service.get_company_context(
            system=system,
            application=application,
            resource=resource,
            operation=operation,
            fields=fields,
        )
        return pack.model_dump_json(indent=2)

    # --- TOOL 5: REGISTER DOWNSTREAM MCP SERVER ---
    @mcp_app.tool()
    def register_downstream_mcp(
        server_id: str,
        endpoint: str,
        transport: str = "streamable_http",
        available_tools: list[dict[str, Any]] | None = None,
        secret_ref: str | None = None,
        supported_action_contexts: list[dict[str, Any]] | None = None,
    ) -> str:
        """
        Registers or updates a downstream MCP server configuration for the authenticated company.

        Args:
            server_id: Unique identifier for the server (e.g. 'odoo17', 'jira-mcp').
            endpoint: URL endpoint or connection identifier.
            transport: Transport mechanism ('streamable_http', 'stdio', 'odoo_xmlrpc', 'internal_mock').
            available_tools: List of tool definition dicts with 'name', 'description', and 'input_schema'.
            secret_ref: Optional reference to credentials in Secrets Manager or environment.
            supported_action_contexts: Optional list of supported ActionContext dicts.

        Returns:
            JSON string containing RegisterDownstreamMCPResult.
        """
        result = service.register_downstream_mcp(
            server_id=server_id,
            endpoint=endpoint,
            transport=transport,
            available_tools=available_tools,
            secret_ref=secret_ref,
            supported_action_contexts=supported_action_contexts,
        )
        return result.model_dump_json(indent=2)

    # --- TOOL 6: RUN DOWNSTREAM REQUEST (ORCHESTRATION ENTRYPOINT) ---
    @mcp_app.tool()
    def run_downstream_request(
        user_request: str,
        action_context: dict[str, Any] | None = None,
        downstream_hint: str | None = None,
        correlation_id: str | None = None,
    ) -> str:
        """
        Main orchestration entrypoint.
        Retrieves approved company memory matching the action context, injects memory into
        a Bedrock prompt alongside registered tools, requires a structured tool call from the model,
        validates the call against downstream schemas, and executes the downstream tool safely.

        Args:
            user_request: Natural language request from the user or client agent.
            action_context: Target action context dict (system, application, resource, operation, fields).
            downstream_hint: Optional server or tool name hint.
            correlation_id: Optional unique idempotency tracking identifier.

        Returns:
            JSON string containing OrchestrationResult.
        """
        scope = ActionContext(**action_context) if action_context else None
        result = service.run_downstream_request(
            user_request=user_request,
            action_context=scope,
            downstream_hint=downstream_hint,
            correlation_id=correlation_id,
        )
        return result.model_dump_json(indent=2)


def create_mcp_server(service: Any | None = None) -> FastMCP:
    """
    Factory constructing a FastMCP server with the six approved tools registered.
    Accepts an injected service instance (production HostedProcessMemoryService or Fake for tests).
    """
    svc = service if service is not None else get_default_service()
    mcp_app = FastMCP(
        "AWS-Process-Memory-Gateway",
        dependencies=["pydantic", "fastmcp"],
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=ALLOWED_HOSTS
        ),
    )
    register_tools(mcp_app, svc)
    return mcp_app


def get_default_server() -> FastMCP:
    """Returns the default lazily initialized FastMCP server instance."""
    global _default_server
    if _default_server is None:
        _default_server = create_mcp_server()
    return _default_server


# Module-level callable wrappers delegating to default service for direct Python calls
def remember_company_instruction(
    instruction_text: str, context_hint: dict[str, Any] | None = None
) -> str:
    scope = ActionContext(**context_hint) if context_hint else None
    result = get_default_service().remember_company_instruction(
        instruction_text=instruction_text, context_hint=scope
    )
    return result.model_dump_json(indent=2)


def list_memory_candidates(status: str = "pending_review") -> str:
    candidates = get_default_service().list_memory_candidates(status=status)
    return json.dumps([c.model_dump() for c in candidates], indent=2)


def review_memory_candidate(
    candidate_id: str,
    decision: Literal["approve", "edit", "reject"],
    edited_rule_text: str | None = None,
    edited_scope: dict[str, Any] | None = None,
    edited_constraint: dict[str, Any] | None = None,
    notes: str | None = None,
) -> str:
    scope = ActionContext(**edited_scope) if edited_scope else None
    result = get_default_service().review_memory_candidate(
        candidate_id=candidate_id,
        decision=decision,
        edited_rule_text=edited_rule_text,
        edited_scope=scope,
        edited_constraint=edited_constraint,
        notes=notes,
    )
    return result.model_dump_json(indent=2)


def get_company_context(
    system: str = "odoo",
    application: str | None = None,
    resource: str | None = None,
    operation: str | None = None,
    fields: list[str] | None = None,
) -> str:
    pack = get_default_service().get_company_context(
        system=system,
        application=application,
        resource=resource,
        operation=operation,
        fields=fields,
    )
    return pack.model_dump_json(indent=2)


def register_downstream_mcp(
    server_id: str,
    endpoint: str,
    transport: str = "streamable_http",
    available_tools: list[dict[str, Any]] | None = None,
    secret_ref: str | None = None,
    supported_action_contexts: list[dict[str, Any]] | None = None,
) -> str:
    result = get_default_service().register_downstream_mcp(
        server_id=server_id,
        endpoint=endpoint,
        transport=transport,
        available_tools=available_tools,
        secret_ref=secret_ref,
        supported_action_contexts=supported_action_contexts,
    )
    return result.model_dump_json(indent=2)


def run_downstream_request(
    user_request: str,
    action_context: dict[str, Any] | None = None,
    downstream_hint: str | None = None,
    correlation_id: str | None = None,
) -> str:
    scope = ActionContext(**action_context) if action_context else None
    result = get_default_service().run_downstream_request(
        user_request=user_request,
        action_context=scope,
        downstream_hint=downstream_hint,
        correlation_id=correlation_id,
    )
    return result.model_dump_json(indent=2)


def __getattr__(name: str):
    if name == "mcp":
        return get_default_server()
    if name == "service":
        return get_default_service()
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


if __name__ == "__main__":
    get_default_server().run()
