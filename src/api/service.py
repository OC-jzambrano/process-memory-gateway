import uuid
import hashlib
import json
import logging
from typing import Optional, List, Dict, Any, Literal, Union, Callable
from datetime import datetime, timezone

from src.config import AWS_REGION, ALLOW_LIVE_ODOO_WRITES
from src.storage.base_repository import BaseRepository
from src.extractor.service import ProcessMemoryExtractorService
from src.governance.memory_retriever import MemoryRetriever
from src.governance.task_validator import TaskValidator
from src.integrations.base_executor import TaskExecutor
from src.integrations.odoo17_xmlrpc import Odoo17XmlRpcExecutor, OdooAccessDeniedError, OdooExecutionError
from src.integrations.mock_executor import MockTaskExecutor
from src.api.auth_context import get_current_context, AuthContextResolver
from src.utils.privacy import sanitize_evidence
from src.models.schemas import (
    ActionContext,
    DeterministicConstraint,
    ExtractionSession,
    CandidateRule,
    CandidateResult,
    ReviewResult,
    TaskCreationResult,
    MemoryPack,
    ExecutionRunRecord,
    ExecutionEventRecord
)
from src.models.enums import (
    RoleType,
    RunStatus,
    DecisionType,
    RuleStatus,
    RuleType,
    Severity,
    EnforcementMode,
    SourceType,
    ConstraintKind,
    ExecutionEventType,
    ExecutionPhase
)

logger = logging.getLogger(__name__)

def compute_canonical_hash_v2(
    title: str,
    description: str,
    definition_of_done: Optional[List[str]],
    project_id: int,
    action_scope: ActionContext,
    connection_identity: Dict[str, Any]
) -> str:
    cleaned_dod = [
        item.strip() for item in (definition_of_done or [])
        if isinstance(item, str) and item.strip()
    ]
    payload = {
        "title": title.strip(),
        "description": description.strip(),
        "definition_of_done": cleaned_dod,
        "project_id": int(project_id),
        "action_scope": action_scope.model_dump() if hasattr(action_scope, "model_dump") else dict(action_scope),
        "connection_identity": connection_identity
    }
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

def compute_legacy_hash_v1(
    title: str,
    description: str,
    definition_of_done: Optional[List[str]],
    project_id: int
) -> str:
    return hashlib.sha256(f"{title}:{description}:{definition_of_done}:{project_id}".encode()).hexdigest()

class HostedProcessMemoryService:
    """
    Unified Application Service powering both Hosted Streamable HTTP MCP and local stdio MCP.
    Public methods enforce authenticated request context with zero caller-controlled identity arguments.
    """

    def __init__(
        self,
        repo: BaseRepository,
        extractor: Optional[ProcessMemoryExtractorService] = None,
        executor: Optional[TaskExecutor] = None,
        executor_factory: Optional[Callable[[str], TaskExecutor]] = None
    ):
        self.repo = repo
        self.extractor = extractor or ProcessMemoryExtractorService()
        self.retriever = MemoryRetriever(repo=self.repo)
        self.validator = TaskValidator()
        self.auth_resolver = AuthContextResolver(repo=self.repo)
        self._injected_executor = executor
        self._executor_factory = executor_factory

        # Startup reconciliation: recover any runs abandoned in run_started state
        self.repo.reconcile_abandoned_runs()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def resolve_executor_for_company(self, company_id: str) -> TaskExecutor:
        """Resolves task executor for company. Fails closed with zero environment fallback."""
        if self._executor_factory is not None:
            return self._executor_factory(company_id)

        if self._injected_executor is not None:
            return self._injected_executor

        conn_config = self.repo.get_odoo_connection(company_id)
        if not conn_config or conn_config.status != "active":
            raise OdooExecutionError(f"No active Odoo connection configured for company '{company_id}'. Routing fails closed.")

        if not conn_config.odoo_url or not conn_config.odoo_db or conn_config.default_project_id is None:
            raise OdooExecutionError(f"Incomplete Odoo connection configuration for company '{company_id}'. Missing URL, DB, or default project.")

        if not conn_config.secret_arn:
            raise OdooExecutionError(f"Odoo connection for company '{company_id}' is missing secret_arn. Failing closed without environment fallback.")

        try:
            import boto3
            sm = boto3.client("secretsmanager", region_name=AWS_REGION)
            secret_val = sm.get_secret_value(SecretId=conn_config.secret_arn)
            secret_data = json.loads(secret_val.get("SecretString", "{}"))
        except Exception as e:
            logger.error("Failed to retrieve secret from Secrets Manager: %s", str(e))
            raise OdooExecutionError(f"Failed to retrieve Odoo secret for company '{company_id}'.") from e

        # Validate authoritative connection record against secret payload
        sec_url = secret_data.get("url") or secret_data.get("ODOO_URL")
        if sec_url and sec_url.rstrip("/") != conn_config.odoo_url.rstrip("/"):
            raise OdooExecutionError(
                f"Conflicting Odoo URL in Secrets Manager for company '{company_id}'. Connection record is authoritative."
            )

        sec_db = secret_data.get("database") or secret_data.get("ODOO_DB")
        if sec_db and sec_db.strip() != conn_config.odoo_db.strip():
            raise OdooExecutionError(
                f"Conflicting Odoo database in Secrets Manager for company '{company_id}'. Connection record is authoritative."
            )

        sec_proj = secret_data.get("default_project_id") or secret_data.get("DEFAULT_PROJECT_ID")
        if sec_proj is not None and int(sec_proj) != int(conn_config.default_project_id):
            raise OdooExecutionError(
                f"Conflicting default project ID in Secrets Manager for company '{company_id}'. Connection record is authoritative."
            )

        username = secret_data.get("username") or secret_data.get("ODOO_LOGIN")
        pwd = secret_data.get("api_key") or secret_data.get("password") or secret_data.get("ODOO_PASSWORD")
        if not username or not pwd:
            raise OdooExecutionError(f"Odoo credentials missing in secret payload for company '{company_id}'.")

        return Odoo17XmlRpcExecutor(
            url=conn_config.odoo_url,
            db=conn_config.odoo_db,
            username=username,
            password=pwd,
            default_project_id=conn_config.default_project_id
        )

    # --- 1. REMEMBER COMPANY INSTRUCTION ---
    def remember_company_instruction(
        self,
        instruction_text: str,
        context_hint: Optional[ActionContext] = None
    ) -> CandidateResult:
        ctx = get_current_context()
        self.auth_resolver.require_role(ctx, [RoleType.OWNER, RoleType.REVIEWER, RoleType.OPERATOR, RoleType.MEMBER])

        # Infer candidate rules using extraction pipeline
        extraction_res = self.extractor.extract_candidates(
            interaction_text=instruction_text,
            client_id=ctx.company_id,
            process_name="project"
        )

        candidate_id = f"cand_{uuid.uuid4().hex}"
        rule_text = instruction_text.strip()
        confidence = 0.95
        source_quote = instruction_text.strip()
        rule_type = RuleType.OPERATIONAL_CONSTRAINT
        severity = Severity.INFO
        enforcement_mode = EnforcementMode.ADVISORY

        if extraction_res.candidates:
            first_c = extraction_res.candidates[0]
            rule_text = first_c.rule_text
            confidence = first_c.confidence
            source_quote = first_c.source_quote
            rule_type = first_c.rule_type
            severity = first_c.severity
            enforcement_mode = first_c.enforcement_mode

        # Structured Scope & Constraint Mapping
        scope = context_hint or ActionContext(
            system="odoo",
            application="project",
            resource="project.task",
            operation="create",
            fields=["definition_of_done"]
        )

        constraint = None
        lower_text = instruction_text.lower()
        if "definition of done" in lower_text or "dod" in lower_text:
            constraint = DeterministicConstraint(
                kind=ConstraintKind.REQUIRED_NONEMPTY_LIST,
                field="definition_of_done",
                min_items=1
            )

        candidate = CandidateRule(
            candidate_id=candidate_id,
            session_id=extraction_res.session_id,
            client_id=ctx.company_id,
            process_name="project",
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
            updated_at=self._now()
        )

        # 1. Create Extraction Session (provenance anchor)
        self.repo.create_session(
            ExtractionSession(
                session_id=extraction_res.session_id,
                client_id=ctx.company_id,
                process_name="project",
                source_type=SourceType.USER_INTERACTION,
                interaction_text=instruction_text,
                model_id=getattr(self.extractor, "model_id", "bedrock-haiku-4.5") or "bedrock",
                candidates_extracted=1,
                extracted_at=self._now()
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
            )
        )

    # --- 2. LIST MEMORY CANDIDATES ---
    def list_memory_candidates(
        self,
        status: str = "pending_review"
    ) -> List[CandidateRule]:
        ctx = get_current_context()
        self.auth_resolver.require_role(ctx, [RoleType.OWNER, RoleType.REVIEWER, RoleType.OPERATOR, RoleType.AUDITOR, RoleType.MEMBER])
        rule_status = RuleStatus(status) if status in [s.value for s in RuleStatus] else RuleStatus.PENDING_REVIEW
        return self.repo.list_candidates(client_id=ctx.company_id, status=rule_status)

    # --- 3. REVIEW MEMORY CANDIDATE ---
    def review_memory_candidate(
        self,
        candidate_id: str,
        decision: Literal["approve", "edit", "reject"],
        edited_rule_text: Optional[str] = None,
        edited_scope: Optional[ActionContext] = None,
        edited_constraint: Optional[Union[DeterministicConstraint, Dict[str, Any]]] = None,
        notes: Optional[str] = None
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
            notes=notes
        )

        if decision in ("approve", "edit") and canonical_rule:
            return ReviewResult(
                status="approved",
                candidate_id=candidate_id,
                decision=DecisionType(decision),
                rule_id=canonical_rule.rule_id,
                version=canonical_rule.version,
                message=f"Candidate successfully approved into active Canonical Rule #{canonical_rule.rule_id} (v{canonical_rule.version})."
            )
        else:
            return ReviewResult(
                status="rejected",
                candidate_id=candidate_id,
                decision=DecisionType.REJECT,
                rule_id=None,
                version=None,
                message=f"Candidate '{candidate_id}' has been rejected and will not be enforced."
            )

    # --- 4. GET COMPANY CONTEXT (MEMORY PACK) ---
    def get_company_context(
        self,
        system: str = "odoo",
        application: Optional[str] = None,
        resource: Optional[str] = None,
        operation: Optional[str] = None,
        fields: Optional[List[str]] = None
    ) -> MemoryPack:
        ctx = get_current_context()
        return self.retriever.retrieve_pack(
            company_id=ctx.company_id,
            company_slug=ctx.company_slug,
            system=system,
            application=application,
            resource=resource,
            operation=operation,
            fields=fields
        )

    # --- 5. CREATE PROJECT TASK (MANAGED ODOO ACTION) ---
    def create_project_task(
        self,
        title: str,
        description: str,
        definition_of_done: Optional[List[str]] = None,
        project_id: Optional[int] = None,
        correlation_id: Optional[str] = None
    ) -> TaskCreationResult:
        ctx = get_current_context()
        self.auth_resolver.require_role(ctx, [RoleType.OWNER, RoleType.OPERATOR, RoleType.REVIEWER])

        cid = correlation_id or f"corr_{uuid.uuid4().hex}"

        # 1. Resolve connection config & authoritative target project
        conn_config = self.repo.get_odoo_connection(ctx.company_id)
        if conn_config:
            target_project_id = conn_config.default_project_id
        elif hasattr(self._injected_executor, "default_project_id") and self._injected_executor.default_project_id is not None:
            target_project_id = self._injected_executor.default_project_id
        else:
            try:
                executor_probe = self.resolve_executor_for_company(ctx.company_id)
                target_project_id = getattr(executor_probe, "default_project_id", None)
            except Exception as e:
                return TaskCreationResult(
                    status=RunStatus.FAILED,
                    run_id=f"run_{uuid.uuid4().hex[:12]}",
                    correlation_id=cid,
                    error_code="routing_failed",
                    message=f"Routing failure: {str(e)}"
                )

        if target_project_id is None:
            return TaskCreationResult(
                status=RunStatus.FAILED,
                run_id=f"run_{uuid.uuid4().hex[:12]}",
                correlation_id=cid,
                error_code="routing_failed",
                message=f"No default project configured for company '{ctx.company_id}'."
            )

        # Reject project override with 0 Odoo calls
        if project_id is not None and int(project_id) != int(target_project_id):
            return TaskCreationResult(
                status=RunStatus.NEEDS_CLARIFICATION,
                run_id=f"run_{uuid.uuid4().hex[:12]}",
                correlation_id=cid,
                error_code="project_override_forbidden",
                missing_information=["project_id"],
                message=f"Project ID override ({project_id}) is forbidden. Tasks are restricted to company configured project ({target_project_id})."
            )

        action_scope = ActionContext(
            system="odoo",
            application="project",
            resource="project.task",
            operation="create",
            fields=["name", "description", "definition_of_done", "project_id"]
        )

        connection_identity = {
            "company_id": ctx.company_id,
            "odoo_url": conn_config.odoo_url if conn_config else "mock://odoo",
            "odoo_db": conn_config.odoo_db if conn_config else "mock_db",
            "default_project_id": target_project_id
        }

        input_hash_v2 = compute_canonical_hash_v2(
            title=title,
            description=description,
            definition_of_done=definition_of_done,
            project_id=target_project_id,
            action_scope=action_scope,
            connection_identity=connection_identity
        )
        input_hash_v1 = compute_legacy_hash_v1(
            title=title,
            description=description,
            definition_of_done=definition_of_done,
            project_id=target_project_id
        )

        # 2. Idempotency and Correlation Check
        existing_run = self.repo.get_execution_run_by_correlation(
            company_id=ctx.company_id,
            correlation_id=cid
        )
        if existing_run:
            if existing_run.status == RunStatus.RECONCILIATION_REQUIRED:
                return TaskCreationResult(
                    status=RunStatus.RECONCILIATION_REQUIRED,
                    run_id=existing_run.run_id,
                    correlation_id=cid,
                    error_code="reconciliation_required",
                    odoo_task_id=existing_run.odoo_task_id,
                    odoo_task_url=existing_run.odoo_task_url,
                    message="Prior execution outcome was uncertain. Manual reconciliation is required before retrying; automatic re-creation is prohibited."
                )

            if existing_run.status == RunStatus.RUN_STARTED:
                return TaskCreationResult(
                    status=RunStatus.RUN_STARTED,
                    run_id=existing_run.run_id,
                    correlation_id=cid,
                    error_code="run_in_progress",
                    message="Task creation is already in progress for this correlation ID."
                )

            if existing_run.status in (RunStatus.CREATED, RunStatus.FAILED):
                stored_hash = existing_run.redacted_input_hash
                if existing_run.hash_algorithm_version == "v1":
                    matches_stored = (stored_hash == input_hash_v1)
                else:
                    matches_stored = (stored_hash == input_hash_v2)

                if not matches_stored:
                    return TaskCreationResult(
                        status=RunStatus.FAILED,
                        run_id=existing_run.run_id,
                        correlation_id=cid,
                        error_code="idempotency_conflict",
                        message=f"Correlation ID '{cid}' was already used with different task parameters. Reusing correlation IDs with changed inputs is forbidden."
                    )

                if existing_run.status == RunStatus.CREATED:
                    return TaskCreationResult(
                        status=RunStatus.CREATED,
                        run_id=existing_run.run_id,
                        correlation_id=cid,
                        applied_rule_ids=[r.get("rule_id") for r in existing_run.applied_rules_snapshot if isinstance(r, dict) and "rule_id" in r],
                        odoo_task_id=existing_run.odoo_task_id,
                        odoo_task_url=existing_run.odoo_task_url,
                        task_name=existing_run.result_payload.get("task_name", title.strip()),
                        message="Idempotency match: Task was already created previously. Returned cached execution result."
                    )

                if existing_run.status == RunStatus.FAILED:
                    return TaskCreationResult(
                        status=RunStatus.FAILED,
                        run_id=existing_run.run_id,
                        correlation_id=cid,
                        error_code=existing_run.error_code or "execution_failed",
                        message=existing_run.error_detail or "Prior execution attempt failed."
                    )

        # 3. Retrieve active rules and validate task readiness
        active_rules = self.repo.get_active_rules(client_id=ctx.company_id)
        validation = self.validator.validate_task_creation(
            title=title,
            description=description,
            definition_of_done=definition_of_done,
            active_rules=active_rules,
            project_id=target_project_id
        )

        run_id = existing_run.run_id if existing_run else f"run_{uuid.uuid4().hex}"
        applied_rules_snapshot = [
            {
                "rule_id": r.rule_id,
                "version": r.version,
                "rule_text": r.rule_text,
                "constraint": r.structured_constraint.model_dump() if r.structured_constraint else None
            }
            for r in validation.applied_rules
        ]
        conn_snapshot = sanitize_evidence(connection_identity)

        # 4. If Blocked -> Record validation blocked with needs_clarification
        if not validation.is_valid or validation.status == RunStatus.NEEDS_CLARIFICATION:
            run_record = ExecutionRunRecord(
                run_id=run_id,
                company_id=ctx.company_id,
                user_id=ctx.user_id,
                correlation_id=cid,
                action_scope=action_scope,
                adapter_kind="odoo17_xmlrpc",
                status=RunStatus.NEEDS_CLARIFICATION,
                redacted_input_hash=input_hash_v2,
                hash_algorithm_version="v2",
                connection_snapshot=conn_snapshot,
                applied_rules_snapshot=applied_rules_snapshot,
                error_code="validation_blocked",
                error_detail=validation.message,
                created_at=self._now()
            )
            event = ExecutionEventRecord(
                event_id=f"eev_{uuid.uuid4().hex}",
                run_id=run_id,
                event_type=ExecutionEventType.VALIDATION_BLOCKED,
                details=sanitize_evidence({"missing_fields": validation.missing_fields, "reason": validation.message}),
                created_at=self._now()
            )
            self.repo.record_validation_blocked(run_record, event)
            return TaskCreationResult(
                status=RunStatus.NEEDS_CLARIFICATION,
                run_id=run_id,
                correlation_id=cid,
                applied_rule_ids=validation.applied_rule_ids,
                missing_information=validation.missing_fields,
                error_code="validation_blocked",
                message=validation.message
            )

        # 5. Atomic Claim Run with unique execution_token
        execution_token = f"tok_{uuid.uuid4().hex}"
        claim_record = ExecutionRunRecord(
            run_id=run_id,
            company_id=ctx.company_id,
            user_id=ctx.user_id,
            correlation_id=cid,
            action_scope=action_scope,
            adapter_kind="odoo17_xmlrpc",
            status=RunStatus.RUN_STARTED,
            redacted_input_hash=input_hash_v2,
            hash_algorithm_version="v2",
            connection_snapshot=conn_snapshot,
            execution_token=execution_token,
            applied_rules_snapshot=applied_rules_snapshot,
            created_at=self._now()
        )
        start_event = ExecutionEventRecord(
            event_id=f"eev_{uuid.uuid4().hex}",
            run_id=run_id,
            event_type=ExecutionEventType.EXECUTION_STARTED,
            details=sanitize_evidence({"action": "create_project_task", "project_id": target_project_id}),
            created_at=self._now()
        )

        claimed, active_run = self.repo.claim_execution_run(claim_record, start_event)
        if not claimed:
            if active_run.status == RunStatus.RUN_STARTED:
                return TaskCreationResult(
                    status=RunStatus.RUN_STARTED,
                    run_id=active_run.run_id,
                    correlation_id=cid,
                    error_code="run_in_progress",
                    message="Task creation is already in progress for this correlation ID."
                )
            if active_run.redacted_input_hash != input_hash_v2:
                return TaskCreationResult(
                    status=RunStatus.FAILED,
                    run_id=active_run.run_id,
                    correlation_id=cid,
                    error_code="idempotency_conflict",
                    message=f"Correlation ID '{cid}' was already used with different task parameters. Reusing correlation IDs with changed inputs is forbidden."
                )
            if active_run.status == RunStatus.CREATED:
                return TaskCreationResult(
                    status=RunStatus.CREATED,
                    run_id=active_run.run_id,
                    correlation_id=cid,
                    odoo_task_id=active_run.odoo_task_id,
                    odoo_task_url=active_run.odoo_task_url,
                    message="Idempotency match: Task was already created previously. Returned cached execution result."
                )
            return TaskCreationResult(
                status=active_run.status,
                run_id=active_run.run_id,
                correlation_id=cid,
                error_code=active_run.error_code,
                message=active_run.error_detail or "Prior execution run exists."
            )

        # 6. Resolve executor and check execution boundary
        try:
            executor = self.resolve_executor_for_company(ctx.company_id)
        except Exception as e:
            fail_event = ExecutionEventRecord(
                event_id=f"eev_{uuid.uuid4().hex}",
                run_id=active_run.run_id,
                event_type=ExecutionEventType.EXECUTION_FAILED,
                details=sanitize_evidence({"error": str(e)}),
                created_at=self._now()
            )
            self.repo.transition_execution_run(
                company_id=ctx.company_id,
                run_id=active_run.run_id,
                execution_token=execution_token,
                new_status=RunStatus.FAILED,
                event=fail_event,
                error_code="routing_failed",
                error_detail=str(e)
            )
            return TaskCreationResult(
                status=RunStatus.FAILED,
                run_id=active_run.run_id,
                correlation_id=cid,
                error_code="routing_failed",
                message=f"Routing failure: {str(e)}"
            )

        is_mock = isinstance(executor, MockTaskExecutor)
        if not is_mock and not ALLOW_LIVE_ODOO_WRITES:
            blocked_event = ExecutionEventRecord(
                event_id=f"eev_{uuid.uuid4().hex}",
                run_id=active_run.run_id,
                event_type=ExecutionEventType.EXECUTION_FAILED,
                details={"reason": "ALLOW_LIVE_ODOO_WRITES is false"},
                created_at=self._now()
            )
            self.repo.transition_execution_run(
                company_id=ctx.company_id,
                run_id=active_run.run_id,
                execution_token=execution_token,
                new_status=RunStatus.FAILED,
                event=blocked_event,
                error_code="live_writes_disabled",
                error_detail="Live Odoo writes are disabled by policy (ALLOW_LIVE_ODOO_WRITES=false)."
            )
            return TaskCreationResult(
                status=RunStatus.FAILED,
                run_id=active_run.run_id,
                correlation_id=cid,
                error_code="live_writes_disabled",
                message="Live Odoo writes are disabled by policy (ALLOW_LIVE_ODOO_WRITES=false)."
            )

        # 7. Execute Phase-Aware Task Creation
        outcome = executor.create_project_task_phase_aware(
            title=title,
            description=description,
            definition_of_done=definition_of_done,
            project_id=target_project_id
        )

        base_url = conn_config.odoo_url if conn_config else (getattr(executor, "url", "http://localhost:8069"))
        base_url = base_url.rstrip("/")

        # Handle Outcomes
        if outcome.phase == ExecutionPhase.SUCCESS:
            task_id = outcome.task_id
            odoo_url = f"{base_url}/web#id={task_id}&model=project.task&view_type=form"
            event = ExecutionEventRecord(
                event_id=f"eev_{uuid.uuid4().hex}",
                run_id=active_run.run_id,
                event_type=ExecutionEventType.TASK_CREATED,
                details=sanitize_evidence({
                    "odoo_task_id": task_id,
                    "read_back_verified": True
                }),
                created_at=self._now()
            )
            try:
                ok = self.repo.transition_execution_run(
                    company_id=ctx.company_id,
                    run_id=active_run.run_id,
                    execution_token=execution_token,
                    new_status=RunStatus.CREATED,
                    event=event,
                    odoo_task_id=task_id,
                    odoo_task_url=odoo_url,
                    result_payload={"task_name": outcome.task_record.name if outcome.task_record else title.strip(), "project_id": target_project_id}
                )
                if not ok:
                    raise RuntimeError("transition_execution_run returned False")
            except Exception as persist_err:
                # State persistence failure after Odoo success -> reconciliation_required
                rec_event = ExecutionEventRecord(
                    event_id=f"eev_{uuid.uuid4().hex}",
                    run_id=active_run.run_id,
                    event_type=ExecutionEventType.RECONCILIATION_REQUIRED,
                    details=sanitize_evidence({
                        "reason": "persistence_failure_after_success",
                        "odoo_task_id": task_id,
                        "error": str(persist_err)
                    }),
                    created_at=self._now()
                )
                self.repo.transition_execution_run(
                    company_id=ctx.company_id,
                    run_id=active_run.run_id,
                    execution_token=execution_token,
                    new_status=RunStatus.RECONCILIATION_REQUIRED,
                    event=rec_event,
                    odoo_task_id=task_id,
                    odoo_task_url=odoo_url,
                    error_code="persistence_failure",
                    error_detail=f"Task #{task_id} created in Odoo but state persistence failed: {str(persist_err)}"
                )
                return TaskCreationResult(
                    status=RunStatus.RECONCILIATION_REQUIRED,
                    run_id=active_run.run_id,
                    correlation_id=cid,
                    odoo_task_id=task_id,
                    odoo_task_url=odoo_url,
                    error_code="reconciliation_required",
                    message=f"Task #{task_id} was created in Odoo but state persistence failed. Reconciliation required."
                )

            return TaskCreationResult(
                status=RunStatus.CREATED,
                run_id=active_run.run_id,
                correlation_id=cid,
                applied_rule_ids=validation.applied_rule_ids,
                odoo_task_id=task_id,
                odoo_task_url=odoo_url,
                task_name=outcome.task_record.name if outcome.task_record else title.strip(),
                message=f"Task #{task_id} successfully created and verified in Odoo Project {target_project_id}."
            )

        elif outcome.phase in (ExecutionPhase.UNCERTAIN_CREATE, ExecutionPhase.VERIFICATION_FAILED):
            odoo_url = f"{base_url}/web#id={outcome.task_id}&model=project.task&view_type=form" if outcome.task_id else None
            event = ExecutionEventRecord(
                event_id=f"eev_{uuid.uuid4().hex}",
                run_id=active_run.run_id,
                event_type=ExecutionEventType.RECONCILIATION_REQUIRED,
                details=sanitize_evidence({
                    "phase": outcome.phase.value,
                    "error_code": outcome.error_code,
                    "error_detail": outcome.error_detail,
                    "odoo_task_id": outcome.task_id
                }),
                created_at=self._now()
            )
            self.repo.transition_execution_run(
                company_id=ctx.company_id,
                run_id=active_run.run_id,
                execution_token=execution_token,
                new_status=RunStatus.RECONCILIATION_REQUIRED,
                event=event,
                odoo_task_id=outcome.task_id,
                odoo_task_url=odoo_url,
                error_code=outcome.error_code or "reconciliation_required",
                error_detail=outcome.error_detail
            )
            return TaskCreationResult(
                status=RunStatus.RECONCILIATION_REQUIRED,
                run_id=active_run.run_id,
                correlation_id=cid,
                odoo_task_id=outcome.task_id,
                odoo_task_url=odoo_url,
                error_code=outcome.error_code or "reconciliation_required",
                message=f"Task execution outcome uncertain ({outcome.phase.value}): {outcome.error_detail}. Manual reconciliation required."
            )

        else: # BEFORE_CREATE, CREATE_REJECTED
            event = ExecutionEventRecord(
                event_id=f"eev_{uuid.uuid4().hex}",
                run_id=active_run.run_id,
                event_type=ExecutionEventType.EXECUTION_FAILED,
                details=sanitize_evidence({
                    "phase": outcome.phase.value,
                    "error_code": outcome.error_code,
                    "error_detail": outcome.error_detail
                }),
                created_at=self._now()
            )
            self.repo.transition_execution_run(
                company_id=ctx.company_id,
                run_id=active_run.run_id,
                execution_token=execution_token,
                new_status=RunStatus.FAILED,
                event=event,
                error_code=outcome.error_code or "execution_failed",
                error_detail=outcome.error_detail
            )
            return TaskCreationResult(
                status=RunStatus.FAILED,
                run_id=active_run.run_id,
                correlation_id=cid,
                error_code=outcome.error_code or "execution_failed",
                message=f"Odoo task creation rejected at phase {outcome.phase.value}: {outcome.error_detail}"
            )
