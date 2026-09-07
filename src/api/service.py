import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from src.api.auth import AuthContextResolver
from src.api.auth_context import get_current_context
from src.extractor.service import ProcessMemoryExtractorService
from src.governance.memory_retriever import MemoryRetriever
from src.models.enums import (
    DecisionType,
    EnforcementMode,
    MCPTransport,
    RoleType,
    RuleStatus,
    RuleType,
    Severity,
    SourceType,
)
from src.models.schemas import (
    ActionContext,
    CandidateResult,
    CandidateRule,
    DeterministicConstraint,
    DownstreamMCPServer,
    DownstreamToolDefinition,
    ExtractionSession,
    MemoryPack,
    OrchestrationResult,
    RegisterDownstreamMCPResult,
    ReviewResult,
)
from src.orchestration.bedrock_orchestrator import BedrockOrchestrator
from src.orchestration.dispatcher import DownstreamDispatcher
from src.storage.base_repository import BaseRepository

logger = logging.getLogger(__name__)


class HostedProcessMemoryService:
    """
    Unified Application Service powering both Hosted Streamable HTTP MCP and local stdio MCP.
    Acts as a company memory repository and generic MCP orchestrator.
    Public methods enforce authenticated request context with zero caller-controlled identity arguments.
    """

    def __init__(
        self,
        repo: BaseRepository,
        extractor: ProcessMemoryExtractorService | None = None,
        orchestrator: BedrockOrchestrator | None = None,
        dispatcher: DownstreamDispatcher | None = None,
    ):
        self.repo = repo
        self.extractor = extractor or ProcessMemoryExtractorService()
        self.retriever = MemoryRetriever(repo=self.repo)
        self.auth_resolver = AuthContextResolver(repo=self.repo)
        self.orchestrator = orchestrator or BedrockOrchestrator()
        self.dispatcher = dispatcher or DownstreamDispatcher(repo=self.repo)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # --- 1. REMEMBER COMPANY INSTRUCTION ---
    def remember_company_instruction(
        self, instruction_text: str, context_hint: ActionContext | None = None
    ) -> CandidateResult:
        """
        Stages proposed operational rules or constraints from dialogue as pending_review.
        Does NOT enforce keyword-based DoD mappings.
        """
        ctx = get_current_context()
        self.auth_resolver.require_role(
            ctx, [RoleType.OWNER, RoleType.REVIEWER, RoleType.OPERATOR, RoleType.MEMBER]
        )

        # Infer candidate rules using extraction pipeline
        extraction_res = self.extractor.extract_candidates(
            interaction_text=instruction_text,
            client_id=ctx.company_id,
            process_name="general",
        )

        candidate_id = f"cand_{uuid.uuid4().hex}"
        rule_text = instruction_text.strip()
        confidence = 0.95
        source_quote = instruction_text.strip()
        rule_type = RuleType.OPERATIONAL_CONSTRAINT
        severity = Severity.INFO
        enforcement_mode = EnforcementMode.ADVISORY

        first_candidate = None
        if extraction_res.candidates:
            first_candidate = extraction_res.candidates[0]
            rule_text = first_candidate.rule_text
            confidence = first_candidate.confidence
            source_quote = first_candidate.source_quote
            rule_type = first_candidate.rule_type
            severity = first_candidate.severity
            enforcement_mode = first_candidate.enforcement_mode

        # Structured Scope & Constraint Mapping
        scope = context_hint or (
            first_candidate.structured_scope
            if first_candidate and first_candidate.structured_scope
            else ActionContext(
                system="odoo",
                application="project",
                resource="project.task",
                operation="create",
                fields=[],
            )
        )

        # Use constraint only if explicitly detected by extractor
        constraint = (
            first_candidate.structured_constraint
            if first_candidate and first_candidate.structured_constraint
            else None
        )

        candidate = CandidateRule(
            candidate_id=candidate_id,
            session_id=extraction_res.session_id,
            client_id=ctx.company_id,
            process_name="general",
            rule_text=rule_text,
            rule_type=rule_type,
            severity=severity,
            enforcement_mode=enforcement_mode,
            source_quote=source_quote,
            confidence=confidence,
            status=RuleStatus.PENDING_REVIEW,
            structured_scope=scope,
            structured_constraint=constraint,
            created_at=self._now(),
            updated_at=self._now(),
        )

        # 1. Create Extraction Session (provenance anchor)
        self.repo.create_session(
            ExtractionSession(
                session_id=extraction_res.session_id,
                client_id=ctx.company_id,
                process_name="general",
                source_type=SourceType.USER_INTERACTION,
                interaction_text=instruction_text,
                model_id=getattr(self.extractor, "model_id", "bedrock-haiku-4.5") or "bedrock",
                candidates_extracted=1,
                extracted_at=self._now(),
            )
        )

        # 2. Save candidate
        self.repo.save_candidates([candidate])

        return CandidateResult(
            status="staged",
            candidate_id=candidate_id,
            rule_text=rule_text,
            scope=scope,
            constraint=constraint,
            confidence=confidence,
            message=(
                f"Candidate rule staged as 'pending_review' (ID: {candidate_id}). "
                f"It is currently inactive and will NOT be enforced until approved by a company reviewer/owner."
            ),
        )

    # --- 2. LIST MEMORY CANDIDATES ---
    def list_memory_candidates(
        self, status: str = "pending_review"
    ) -> list[CandidateRule]:
        ctx = get_current_context()
        self.auth_resolver.require_role(
            ctx,
            [
                RoleType.OWNER,
                RoleType.REVIEWER,
                RoleType.OPERATOR,
                RoleType.AUDITOR,
                RoleType.MEMBER,
            ],
        )
        rule_status = (
            RuleStatus(status)
            if status in [s.value for s in RuleStatus]
            else RuleStatus.PENDING_REVIEW
        )
        return self.repo.list_candidates(client_id=ctx.company_id, status=rule_status)

    # --- 3. REVIEW MEMORY CANDIDATE ---
    def review_memory_candidate(
        self,
        candidate_id: str,
        decision: Literal["approve", "edit", "reject"],
        edited_rule_text: str | None = None,
        edited_scope: ActionContext | None = None,
        edited_constraint: DeterministicConstraint | dict[str, Any] | None = None,
        notes: str | None = None,
    ) -> ReviewResult:
        ctx = get_current_context()
        self.auth_resolver.require_role(ctx, [RoleType.OWNER, RoleType.REVIEWER])

        parsed_constraint = None
        if edited_constraint:
            if isinstance(edited_constraint, dict):
                parsed_constraint = DeterministicConstraint(**edited_constraint)
            else:
                parsed_constraint = edited_constraint

        canonical_rule = self.repo.review_candidate(
            candidate_id=candidate_id,
            decision=DecisionType(decision),
            reviewer=ctx.user_id,
            client_id=ctx.company_id,
            edited_rule_text=edited_rule_text,
            edited_scope=edited_scope,
            edited_constraint=parsed_constraint,
            notes=notes,
        )

        if decision in ("approve", "edit") and canonical_rule:
            return ReviewResult(
                status="approved",
                candidate_id=candidate_id,
                decision=DecisionType(decision),
                rule_id=canonical_rule.rule_id,
                version=canonical_rule.version,
                message=f"Candidate successfully approved into active Canonical Rule #{canonical_rule.rule_id} (v{canonical_rule.version}).",
            )
        else:
            return ReviewResult(
                status="rejected",
                candidate_id=candidate_id,
                decision=DecisionType.REJECT,
                message=f"Candidate '{candidate_id}' was rejected and will not be enforced.",
            )

    # --- 4. GET COMPANY CONTEXT (MEMORY PACK) ---
    def get_company_context(
        self,
        system: str = "odoo",
        application: str | None = None,
        resource: str | None = None,
        operation: str | None = None,
        fields: list[str] | None = None,
    ) -> MemoryPack:
        ctx = get_current_context()
        return self.retriever.retrieve_pack(
            company_id=ctx.company_id,
            company_slug=ctx.company_slug,
            system=system,
            application=application,
            resource=resource,
            operation=operation,
            fields=fields,
        )

    # --- 5. REGISTER DOWNSTREAM MCP SERVER ---
    def register_downstream_mcp(
        self,
        server_id: str,
        endpoint: str,
        transport: str = "streamable_http",
        available_tools: list[dict[str, Any]] | None = None,
        secret_ref: str | None = None,
        supported_action_contexts: list[dict[str, Any]] | None = None,
    ) -> RegisterDownstreamMCPResult:
        ctx = get_current_context()
        self.auth_resolver.require_role(ctx, [RoleType.OWNER, RoleType.REVIEWER])

        parsed_tools = []
        if available_tools:
            for t in available_tools:
                if isinstance(t, dict):
                    parsed_tools.append(
                        DownstreamToolDefinition(
                            name=t["name"],
                            description=t.get("description", ""),
                            input_schema=t.get("input_schema", {}),
                        )
                    )
                elif isinstance(t, DownstreamToolDefinition):
                    parsed_tools.append(t)

        parsed_contexts = []
        if supported_action_contexts:
            for c in supported_action_contexts:
                if isinstance(c, dict):
                    parsed_contexts.append(ActionContext(**c))
                elif isinstance(c, ActionContext):
                    parsed_contexts.append(c)

        transport_enum = MCPTransport.STREAMABLE_HTTP
        try:
            transport_enum = MCPTransport(transport)
        except ValueError:
            pass

        server = DownstreamMCPServer(
            company_id=ctx.company_id,
            server_id=server_id.strip(),
            endpoint=endpoint.strip(),
            transport=transport_enum,
            available_tools=parsed_tools,
            secret_ref=secret_ref,
            supported_action_contexts=parsed_contexts,
            created_at=self._now(),
            updated_at=self._now(),
        )

        self.repo.upsert_downstream_mcp(server)

        return RegisterDownstreamMCPResult(
            status="registered",
            server_id=server.server_id,
            transport=server.transport.value,
            tool_count=len(server.available_tools),
            message=f"Downstream MCP server '{server.server_id}' registered successfully with {len(server.available_tools)} tool(s).",
        )

    def list_downstream_mcps(self) -> list[DownstreamMCPServer]:
        ctx = get_current_context()
        return self.repo.list_downstream_mcps(company_id=ctx.company_id)

    # --- 6. RUN DOWNSTREAM REQUEST (ORCHESTRATION ENTRYPOINT) ---
    def run_downstream_request(
        self,
        user_request: str,
        action_context: ActionContext | dict[str, Any] | None = None,
        downstream_hint: str | None = None,
        correlation_id: str | None = None,
    ) -> OrchestrationResult:
        ctx = get_current_context()
        self.auth_resolver.require_role(
            ctx, [RoleType.OWNER, RoleType.REVIEWER, RoleType.OPERATOR, RoleType.MEMBER]
        )

        cid = correlation_id or f"corr_{uuid.uuid4().hex}"

        # Resolve action context
        if isinstance(action_context, dict):
            scope = ActionContext(**action_context)
        elif isinstance(action_context, ActionContext):
            scope = action_context
        else:
            scope = ActionContext(
                system="odoo",
                application="project",
                resource="project.task",
                operation="create",
                fields=[],
            )

        # 1. Fetch registered downstream servers for company
        registered_servers = self.repo.list_downstream_mcps(company_id=ctx.company_id)
        if downstream_hint:
            # Filter servers by hint if provided
            filtered = [
                s for s in registered_servers
                if s.server_id == downstream_hint or any(t.name == downstream_hint for t in s.available_tools)
            ]
            if filtered:
                registered_servers = filtered

        if not registered_servers:
            return OrchestrationResult(
                success=False,
                correlation_id=cid,
                server_id=downstream_hint or "unknown",
                tool_name="none",
                error=f"No downstream MCP servers registered for company '{ctx.company_id}'.",
            )

        # 2. Retrieve matching approved canonical rules
        approved_rules = self.repo.get_active_rules(
            client_id=ctx.company_id,
            system=scope.system,
            resource=scope.resource,
            operation=scope.operation,
        )

        # 3. Call Bedrock Orchestrator to synthesize structured tool call
        try:
            tool_call = self.orchestrator.orchestrate(
                company_slug=ctx.company_slug,
                action_context=scope,
                approved_rules=approved_rules,
                registered_servers=registered_servers,
                user_request=user_request,
            )
        except Exception as e:  # noqa: BLE001 - Catch all orchestration exceptions to return typed OrchestrationResult
            logger.error("Bedrock orchestration error: %s", e)
            return OrchestrationResult(
                success=False,
                correlation_id=cid,
                server_id=registered_servers[0].server_id,
                tool_name="orchestration_failed",
                error=f"Orchestration synthesis failed: {e}",
            )

        # 4. Dispatch tool call to downstream adapter with schema and protocol validation
        return self.dispatcher.dispatch(
            company_id=ctx.company_id,
            tool_call=tool_call,
            correlation_id=cid,
        )
