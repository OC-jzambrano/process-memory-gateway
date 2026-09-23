import json
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
    CanonicalRule,
    CanonicalRuleStatusResult,
    DeterministicConstraint,
    DownstreamMCPServer,
    DownstreamToolDefinition,
    ExtractionSession,
    MemoryPack,
    OrchestrationResult,
    OrchestrationToolCall,
    RegisterDownstreamMCPResult,
    ReviewResult,
)
from src.orchestration.bedrock_orchestrator import BedrockOrchestrator
from src.orchestration.dispatcher import DownstreamDispatcher
from src.storage.base_repository import BaseRepository
from src.utils.privacy import sanitize_evidence

logger = logging.getLogger(__name__)


def _default_odoo_xmlrpc_tools() -> list[DownstreamToolDefinition]:
    return [
        DownstreamToolDefinition(
            name="create_record",
            description="Create an Odoo record via XML-RPC using explicit model and values arguments.",
            input_schema={
                "type": "object",
                "properties": {
                    "model": {"type": "string"},
                    "values": {"type": "object"},
                },
                "required": ["model", "values"],
            },
        ),
        DownstreamToolDefinition(
            name="execute_kw",
            description="Execute an Odoo model method via XML-RPC using explicit model, method, args, and kwargs.",
            input_schema={
                "type": "object",
                "properties": {
                    "model": {"type": "string"},
                    "method": {"type": "string"},
                    "args": {"type": "array"},
                    "kwargs": {"type": "object"},
                },
                "required": ["model", "method"],
            },
        ),
    ]


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
            else ActionContext()
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

    def list_canonical_rules(
        self, status: str = "approved", process_name: str | None = None
    ) -> list[CanonicalRule]:
        """List canonical rules for audit/review without action-scope matching."""
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
        if status == "all":
            rule_status = "all"
        else:
            rule_status = (
                RuleStatus(status)
                if status in [s.value for s in RuleStatus]
                else RuleStatus.APPROVED
            )
        return self.repo.list_canonical_rules(
            client_id=ctx.company_id,
            status=rule_status,
            process_name=process_name,
        )

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

    def set_canonical_rule_status(
        self,
        rule_id: str,
        status: Literal["approved", "archived"],
        notes: str | None = None,
    ) -> CanonicalRuleStatusResult:
        """Enable or disable one canonical rule for the authenticated company."""
        ctx = get_current_context()
        self.auth_resolver.require_role(ctx, [RoleType.OWNER, RoleType.REVIEWER])

        rule = self.repo.get_rule(rule_id=rule_id, client_id=ctx.company_id)
        if not rule:
            raise ValueError(
                f"Canonical Rule with ID '{rule_id}' not found for tenant '{ctx.company_id}'."
            )

        updated = self.repo.set_rule_status(
            rule_id=rule_id,
            status=status,
            reviewer=ctx.user_id,
            client_id=ctx.company_id,
            notes=notes,
        )
        return CanonicalRuleStatusResult(
            status=updated.status.value,
            rule_id=updated.rule_id,
            previous_status=rule.status.value,
            message=(
                f"Canonical Rule '{updated.rule_id}' is now '{updated.status.value}' "
                "and will be excluded from prompts when archived."
            ),
        )

    # --- 4. GET COMPANY CONTEXT (MEMORY PACK) ---
    def get_company_context(
        self,
        system: str | None = None,
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
        if transport_enum == MCPTransport.ODOO_XMLRPC and not parsed_tools:
            parsed_tools = _default_odoo_xmlrpc_tools()

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
        if transport_enum == MCPTransport.ODOO_XMLRPC:
            server.metadata = {
                **server.metadata,
                "registered_actor": self._resolve_registered_odoo_actor(server, ctx),
            }

        self.repo.upsert_downstream_mcp(server)

        return RegisterDownstreamMCPResult(
            status="registered",
            server_id=server.server_id,
            transport=server.transport.value,
            tool_count=len(server.available_tools),
            message=f"Downstream MCP server '{server.server_id}' registered successfully with {len(server.available_tools)} tool(s).",
        )

    def _resolve_registered_odoo_actor(
        self, server: DownstreamMCPServer, ctx
    ) -> dict[str, Any]:
        """Resolve the Odoo user behind the registered XML-RPC credentials."""
        try:
            connector = self.dispatcher._resolve_odoo_connector(server)
            uid = connector.authenticate()
            users = connector.execute_kw(
                model="res.users",
                method="read",
                args=[
                    [uid],
                    ["id", "name", "login", "email", "active"],
                ],
            )
            user = users[0] if isinstance(users, list) and users else {}
            if not isinstance(user, dict) or user.get("id") != uid:
                return {
                    "odoo_user_id": None,
                    "registered_by_user_id": ctx.user_id,
                    "registered_by_email": ctx.email,
                    "source": "downstream_registration_credentials",
                    "default_assignee_when_unspecified": False,
                    "resolution_error": "registered_odoo_user_not_readable",
                }
            return {
                "odoo_user_id": uid,
                "name": user.get("name"),
                "login": user.get("login") or getattr(connector, "login", None),
                "email": user.get("email"),
                "active": user.get("active", True),
                "registered_by_user_id": ctx.user_id,
                "registered_by_email": ctx.email,
                "source": "downstream_registration_credentials",
                "default_assignee_when_unspecified": bool(user.get("active", True)),
            }
        except Exception as exc:  # noqa: BLE001 - registration metadata must not leak secrets
            logger.warning(
                "Could not resolve registered Odoo actor for server '%s': %s",
                server.server_id,
                sanitize_evidence(str(exc)),
            )
            return {
                "odoo_user_id": None,
                "registered_by_user_id": ctx.user_id,
                "registered_by_email": ctx.email,
                "source": "downstream_registration_credentials",
                "default_assignee_when_unspecified": False,
                "resolution_error": "unavailable",
            }

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
            scope = ActionContext()

        # 1. Fetch registered downstream servers for company
        registered_servers = self.repo.list_downstream_mcps(company_id=ctx.company_id)
        if downstream_hint:
            filtered = [
                s for s in registered_servers
                if s.server_id == downstream_hint or any(t.name == downstream_hint for t in s.available_tools)
            ]
            if not filtered:
                return OrchestrationResult(
                    success=False,
                    correlation_id=cid,
                    server_id=downstream_hint,
                    tool_name="none",
                    error=(
                        f"Downstream '{downstream_hint}' is not registered for "
                        f"company '{ctx.company_slug}'."
                    ),
                )
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
        pack = self.retriever.retrieve_pack(
            company_id=ctx.company_id,
            company_slug=ctx.company_slug,
            system=scope.system,
            application=scope.application,
            resource=scope.resource,
            operation=scope.operation,
            fields=scope.fields,
        )
        if pack.omitted_count:
            return OrchestrationResult(
                success=False, correlation_id=cid,
                server_id=downstream_hint or "unknown", tool_name="none",
                error="Relevant company memory exceeds the context budget; narrow the action context before executing.",
                metadata={"memory_omitted_count": pack.omitted_count},
            )
        selected_ids = {r.rule_id for r in pack.rules}
        approved_rules = [
            rule for rule in self.repo.get_active_rules(client_id=ctx.company_id)
            if rule.rule_id in selected_ids
        ]
        actor_context = self._build_actor_context(ctx, registered_servers)
        include_trace_details = self._can_view_trace_details(ctx)
        memory_trace = self._build_memory_trace(
            pack,
            approved_rules,
            actor_context,
            include_details=include_trace_details,
        )

        # 3. Call Bedrock Orchestrator to synthesize structured tool call
        try:
            tool_call = self.orchestrator.orchestrate(
                company_slug=ctx.company_slug,
                action_context=scope,
                approved_rules=approved_rules,
                registered_servers=registered_servers,
                user_request=user_request,
                actor_context=actor_context,
            )
        except Exception as e:  # noqa: BLE001 - Catch all orchestration exceptions to return typed OrchestrationResult
            logger.error("Bedrock orchestration error: %s", e)
            fallback_call = self._try_structured_fallback_tool_call(
                user_request=user_request,
                registered_servers=registered_servers,
                downstream_hint=downstream_hint,
            )
            if fallback_call:
                result = self.dispatcher.dispatch(
                    company_id=ctx.company_id,
                    tool_call=fallback_call,
                    correlation_id=cid,
                )
                result.metadata = {
                    **result.metadata,
                    "orchestration_fallback": "structured_json",
                    "orchestration_error": str(e),
                    "memory_trace": memory_trace,
                    "tool_call": self._format_tool_call_trace(
                        fallback_call,
                        include_details=include_trace_details,
                    ),
                }
                return result
            return OrchestrationResult(
                success=False,
                correlation_id=cid,
                server_id=registered_servers[0].server_id,
                tool_name="orchestration_failed",
                error=f"Orchestration synthesis failed: {e}",
                metadata={"memory_trace": memory_trace},
            )

        # 4. Dispatch tool call to downstream adapter with schema and protocol validation
        result = self.dispatcher.dispatch(
            company_id=ctx.company_id,
            tool_call=tool_call,
            correlation_id=cid,
        )
        result.metadata = {
            **result.metadata,
            "memory_trace": memory_trace,
            "tool_call": self._format_tool_call_trace(
                tool_call,
                include_details=include_trace_details,
            ),
        }
        return result

    def _build_actor_context(
        self,
        ctx,
        registered_servers: list[DownstreamMCPServer],
    ) -> dict[str, Any]:
        actor_context: dict[str, Any] = {
            "authenticated_user": {
                "user_id": ctx.user_id,
                "email": ctx.email,
                "role": ctx.role.value if hasattr(ctx.role, "value") else str(ctx.role),
            },
            "assignment_policy": (
                "Use the authenticated Odoo actor as the default assignee only when "
                "the user request does not name another assignee. If the user names "
                "another person, resolve that person explicitly and use their Odoo ID."
            ),
        }

        odoo_actor_ids: dict[str, Any] = {}
        for server in registered_servers:
            if server.transport != MCPTransport.ODOO_XMLRPC:
                continue
            registered_actor = (server.metadata or {}).get("registered_actor")
            if isinstance(registered_actor, dict) and registered_actor.get("odoo_user_id"):
                if registered_actor.get("registered_by_user_id") != ctx.user_id:
                    odoo_actor_ids[server.server_id] = {
                        "odoo_user_id": None,
                        "source": registered_actor.get(
                            "source", "downstream_registration_credentials"
                        ),
                        "default_assignee_when_unspecified": False,
                        "resolution_error": "registered_actor_belongs_to_different_opm_user",
                    }
                    continue
                verified_actor = self._verify_registered_odoo_actor(server, registered_actor)
                odoo_actor_ids[server.server_id] = {
                    "odoo_user_id": verified_actor.get("odoo_user_id"),
                    "source": verified_actor.get("source"),
                    "default_assignee_when_unspecified": verified_actor.get(
                        "default_assignee_when_unspecified"
                    ),
                    **(
                        {"resolution_error": verified_actor["resolution_error"]}
                        if verified_actor.get("resolution_error")
                        else {}
                    ),
                }
                continue
            try:
                connector = self.dispatcher._resolve_odoo_connector(server)
                matches = connector.execute_kw(
                    model="res.users",
                    method="search_read",
                    args=[
                        [
                            "|",
                            ["login", "=", ctx.email],
                            ["email", "=", ctx.email],
                        ]
                    ],
                    kwargs={
                        "fields": ["id", "name", "login", "email", "active"],
                        "limit": 2,
                    },
                )
                active_matches = [
                    user
                    for user in matches
                    if isinstance(user, dict) and user.get("active", True)
                ]
                if len(active_matches) != 1:
                    odoo_actor_ids[server.server_id] = {
                        "odoo_user_id": None,
                        "default_assignee_when_unspecified": False,
                        "resolution_error": "no_unique_active_user_for_authenticated_email",
                    }
                    continue
                actor = active_matches[0]
                odoo_actor_ids[server.server_id] = {
                    "odoo_user_id": actor["id"],
                    "source": "authenticated_opm_email_lookup",
                    "default_assignee_when_unspecified": True,
                }
            except Exception as exc:  # noqa: BLE001 - actor context must not block execution
                logger.warning(
                    "Could not resolve Odoo actor context for server '%s': %s",
                    server.server_id,
                    exc,
                )
                odoo_actor_ids[server.server_id] = {
                    "odoo_user_id": None,
                    "default_assignee_when_unspecified": False,
                    "resolution_error": "unavailable",
                }
        if odoo_actor_ids:
            actor_context["odoo"] = odoo_actor_ids
        return actor_context

    def _verify_registered_odoo_actor(
        self, server: DownstreamMCPServer, registered_actor: dict[str, Any]
    ) -> dict[str, Any]:
        """Verify a persisted registered actor still exists and is active before use."""
        uid = registered_actor.get("odoo_user_id")
        if not uid:
            return {
                "odoo_user_id": None,
                "source": registered_actor.get("source", "downstream_registration_credentials"),
                "default_assignee_when_unspecified": False,
                "resolution_error": "missing_registered_odoo_user_id",
            }
        try:
            connector = self.dispatcher._resolve_odoo_connector(server)
            users = connector.execute_kw(
                model="res.users",
                method="read",
                args=[[uid], ["id", "active"]],
            )
            user = users[0] if isinstance(users, list) and users else {}
            if not isinstance(user, dict) or user.get("id") != uid:
                return {
                    "odoo_user_id": None,
                    "source": registered_actor.get(
                        "source", "downstream_registration_credentials"
                    ),
                    "default_assignee_when_unspecified": False,
                    "resolution_error": "registered_odoo_user_not_readable",
                }
            active = bool(user.get("active", True))
            return {
                "odoo_user_id": uid if active else None,
                "source": registered_actor.get(
                    "source", "downstream_registration_credentials"
                ),
                "default_assignee_when_unspecified": active,
                **({} if active else {"resolution_error": "registered_odoo_user_inactive"}),
            }
        except Exception as exc:  # noqa: BLE001 - actor verification must not block execution
            logger.warning(
                "Could not verify registered Odoo actor for server '%s': %s",
                server.server_id,
                sanitize_evidence(str(exc)),
            )
            return {
                "odoo_user_id": None,
                "source": registered_actor.get("source", "downstream_registration_credentials"),
                "default_assignee_when_unspecified": False,
                "resolution_error": "registered_actor_verification_failed",
            }

    @staticmethod
    def _build_memory_trace(
        pack: MemoryPack,
        approved_rules: list[CanonicalRule],
        actor_context: dict[str, Any],
        include_details: bool = False,
    ) -> dict[str, Any]:
        trace_actor_context = (
            actor_context if include_details else HostedProcessMemoryService._redact_actor_context(actor_context)
        )
        return {
            "company_slug": pack.company_slug,
            "action_scope": {
                "system": pack.system,
                "application": pack.application,
                "resource": pack.resource,
                "operation": pack.operation,
            },
            "retrieved_rule_count": len(pack.rules),
            "omitted_count": pack.omitted_count,
            "message": pack.message,
            "applied_rules": [
                {
                    "rule_id": rule.rule_id,
                    "version": rule.version,
                    "enforcement_mode": rule.enforcement_mode.value,
                    **(
                        {
                            "rule_text": rule.rule_text,
                            "rule_type": rule.rule_type.value,
                            "scope": rule.structured_scope.model_dump()
                            if rule.structured_scope
                            else None,
                        }
                        if include_details
                        else {}
                    ),
                }
                for rule in approved_rules
            ],
            "actor_context": trace_actor_context,
        }

    @staticmethod
    def _can_view_trace_details(ctx) -> bool:
        return ctx.role in {RoleType.OWNER, RoleType.REVIEWER, RoleType.AUDITOR}

    @staticmethod
    def _redact_actor_context(actor_context: dict[str, Any]) -> dict[str, Any]:
        redacted = {
            "authenticated_user": {
                "user_id": actor_context.get("authenticated_user", {}).get("user_id"),
                "role": actor_context.get("authenticated_user", {}).get("role"),
            },
            "assignment_policy": actor_context.get("assignment_policy"),
        }
        if "odoo" in actor_context:
            redacted["odoo"] = {
                server_id: {
                    "odoo_user_id": values.get("odoo_user_id"),
                    "source": values.get("source"),
                    "default_assignee_when_unspecified": values.get(
                        "default_assignee_when_unspecified"
                    ),
                    **(
                        {"resolution_error": values["resolution_error"]}
                        if values.get("resolution_error")
                        else {}
                    ),
                }
                for server_id, values in actor_context["odoo"].items()
                if isinstance(values, dict)
            }
        return redacted

    @staticmethod
    def _format_tool_call_trace(
        tool_call: OrchestrationToolCall,
        include_details: bool = False,
    ) -> dict[str, Any]:
        summary = {
            "server_id": tool_call.server_id,
            "tool_name": tool_call.tool_name,
        }
        if include_details:
            summary["arguments"] = sanitize_evidence(tool_call.arguments)
        elif isinstance(tool_call.arguments, dict) and "model" in tool_call.arguments:
            summary["model"] = tool_call.arguments["model"]
        return summary

    def _try_structured_fallback_tool_call(
        self,
        user_request: str,
        registered_servers: list[DownstreamMCPServer],
        downstream_hint: str | None = None,
    ) -> OrchestrationToolCall | None:
        """
        Deterministic escape hatch for agents that already know the exact downstream call.
        This keeps explicit operational calls working during transient LLM outages.
        """
        payload = self._extract_json_object(user_request)
        if not isinstance(payload, dict):
            return None

        if {"server_id", "tool_name", "arguments"} <= set(payload):
            server_id = str(payload["server_id"])
            allowed_server = next(
                (s for s in registered_servers if s.server_id == server_id), None
            )
            if not allowed_server:
                return None
            arguments = payload.get("arguments")
            if isinstance(arguments, dict):
                return OrchestrationToolCall(
                    server_id=server_id,
                    tool_name=str(payload["tool_name"]),
                    arguments=arguments,
                )
            return None

        server = self._select_fallback_server(registered_servers, downstream_hint)
        if not server:
            return None

        if "model" in payload and "values" in payload:
            return OrchestrationToolCall(
                server_id=server.server_id,
                tool_name="create_record",
                arguments={"model": payload["model"], "values": payload["values"]},
            )
        if "model" in payload and "method" in payload:
            return OrchestrationToolCall(
                server_id=server.server_id,
                tool_name="execute_kw",
                arguments={
                    "model": payload["model"],
                    "method": payload["method"],
                    "args": payload.get("args", []),
                    "kwargs": payload.get("kwargs", {}),
                },
            )
        return None

    @staticmethod
    def _select_fallback_server(
        registered_servers: list[DownstreamMCPServer],
        downstream_hint: str | None,
    ) -> DownstreamMCPServer | None:
        if downstream_hint:
            return next((s for s in registered_servers if s.server_id == downstream_hint), None)
        if len(registered_servers) == 1:
            return registered_servers[0]
        odoo_servers = [s for s in registered_servers if s.transport == MCPTransport.ODOO_XMLRPC]
        if len(odoo_servers) == 1:
            return odoo_servers[0]
        return None

    @staticmethod
    def _extract_json_object(text: str) -> dict[str, Any] | None:
        stripped = text.strip()
        candidates = [stripped]
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end > start:
            candidates.append(stripped[start : end + 1])
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None
