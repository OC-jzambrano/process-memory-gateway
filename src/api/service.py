import uuid
import hashlib
from typing import Optional, List, Dict, Any, Literal, Union
from datetime import datetime, timezone

from src.storage.base_repository import BaseRepository
from src.extractor.service import ProcessMemoryExtractorService
from src.governance.memory_retriever import MemoryRetriever
from src.governance.task_validator import TaskValidator
from src.integrations.base_executor import TaskExecutor
from src.integrations.odoo17_xmlrpc import Odoo17XmlRpcExecutor, OdooAccessDeniedError, OdooExecutionError
from src.integrations.mock_executor import MockTaskExecutor
from src.api.auth_context import get_current_context, AuthContextResolver
from src.models.schemas import (
    RequestContext,
    ActionContext,
    DeterministicConstraint,
    ExtractionSession,
    CandidateRule,
    CanonicalRule,
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
    ExecutionEventType
)

class HostedProcessMemoryService:
    """
    Unified Application Service powering both Hosted Streamable HTTP MCP and local stdio MCP.
    Public methods enforce authenticated request context with zero caller-controlled identity arguments.
    """

    def __init__(
        self,
        repo: BaseRepository,
        extractor: Optional[ProcessMemoryExtractorService] = None,
        executor: Optional[TaskExecutor] = None
    ):
        self.repo = repo
        self.extractor = extractor or ProcessMemoryExtractorService()
        self.retriever = MemoryRetriever(repo=self.repo)
        self.validator = TaskValidator()
        self.auth_resolver = AuthContextResolver(repo=self.repo)
        self.executor = executor or Odoo17XmlRpcExecutor()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

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
        target_project_id = project_id or 142

        # 1. Idempotency Check: (company_id, correlation_id)
        existing_run = self.repo.get_execution_run_by_correlation(
            company_id=ctx.company_id,
            correlation_id=cid
        )
        if existing_run and existing_run.status == RunStatus.CREATED:
            return TaskCreationResult(
                status=RunStatus.CREATED,
                run_id=existing_run.run_id,
                correlation_id=cid,
                applied_rule_ids=[r.get("rule_id") for r in existing_run.applied_rules_snapshot if "rule_id" in r],
                odoo_task_id=existing_run.odoo_task_id,
                odoo_task_url=existing_run.odoo_task_url,
                task_name=existing_run.result_payload.get("task_name", title),
                message="Idempotency match: Task was already created previously. Returned cached execution result."
            )

        # 2. Retrieve exact active rules for project.task:create
        active_rules = self.repo.get_active_rules(client_id=ctx.company_id)
        task_creation_rules = [
            r for r in active_rules
            if not r.structured_scope or (
                (not r.structured_scope.resource or r.structured_scope.resource == "project.task") and
                (not r.structured_scope.operation or r.structured_scope.operation == "create")
            )
        ]

        # 3. Deterministic Task Readiness Validation
        validation = self.validator.validate_task_creation(
            title=title,
            description=description,
            definition_of_done=definition_of_done,
            active_rules=task_creation_rules,
            project_id=target_project_id
        )

        run_id = existing_run.run_id if existing_run else f"run_{uuid.uuid4().hex}"
        action_scope = ActionContext(
            system="odoo",
            application="project",
            resource="project.task",
            operation="create",
            fields=["name", "description", "definition_of_done", "project_id"]
        )
        input_hash = hashlib.sha256(f"{title}:{description}:{definition_of_done}".encode()).hexdigest()

        applied_rules_snapshot = [
            {
                "rule_id": r.rule_id,
                "version": r.version,
                "rule_text": r.rule_text,
                "constraint": r.structured_constraint.model_dump() if r.structured_constraint else None
            }
            for r in validation.applied_rules
        ]

        # 4. If Blocked / Missing DoD -> Return needs_clarification with 0 Odoo calls
        if not validation.is_valid or validation.status == RunStatus.NEEDS_CLARIFICATION:
            run_record = ExecutionRunRecord(
                run_id=run_id,
                company_id=ctx.company_id,
                user_id=ctx.user_id,
                correlation_id=cid,
                action_scope=action_scope,
                adapter_kind="odoo17_xmlrpc",
                status=RunStatus.NEEDS_CLARIFICATION,
                redacted_input_hash=input_hash,
                applied_rules_snapshot=applied_rules_snapshot,
                error_detail=validation.message,
                created_at=self._now()
            )
            if existing_run:
                self.repo.update_execution_run(run_record)
            else:
                self.repo.create_execution_run(run_record)

            self.repo.add_execution_event(
                ExecutionEventRecord(
                    event_id=f"eev_{uuid.uuid4().hex}",
                    run_id=run_id,
                    event_type=ExecutionEventType.VALIDATION_FAILED,
                    details={"missing_fields": validation.missing_fields, "reason": validation.message},
                    created_at=self._now()
                )
            )

            return TaskCreationResult(
                status=RunStatus.NEEDS_CLARIFICATION,
                run_id=run_id,
                correlation_id=cid,
                applied_rule_ids=validation.applied_rule_ids,
                missing_information=validation.missing_fields,
                message=validation.message
            )

        # 5. Execute Managed Task Creation in Odoo with Read-Back
        try:
            task_record = self.executor.create_project_task(
                title=title,
                description=description,
                definition_of_done=definition_of_done,
                project_id=target_project_id
            )

            odoo_url = f"https://community.odooconcept.com/web#id={task_record.id}&model=project.task&view_type=form"

            run_record = ExecutionRunRecord(
                run_id=run_id,
                company_id=ctx.company_id,
                user_id=ctx.user_id,
                correlation_id=cid,
                action_scope=action_scope,
                adapter_kind="odoo17_xmlrpc",
                status=RunStatus.CREATED,
                redacted_input_hash=input_hash,
                applied_rules_snapshot=applied_rules_snapshot,
                odoo_task_id=task_record.id,
                odoo_task_url=odoo_url,
                result_payload={"task_name": task_record.name, "project_id": task_record.project_id},
                created_at=self._now()
            )

            if existing_run:
                self.repo.update_execution_run(run_record)
            else:
                self.repo.create_execution_run(run_record)

            self.repo.add_execution_event(
                ExecutionEventRecord(
                    event_id=f"eev_{uuid.uuid4().hex}",
                    run_id=run_id,
                    event_type=ExecutionEventType.TASK_CREATED,
                    details={"odoo_task_id": task_record.id, "read_back_verified": True},
                    created_at=self._now()
                )
            )

            return TaskCreationResult(
                status=RunStatus.CREATED,
                run_id=run_id,
                correlation_id=cid,
                applied_rule_ids=validation.applied_rule_ids,
                missing_information=[],
                odoo_task_id=task_record.id,
                odoo_task_url=odoo_url,
                task_name=task_record.name,
                message=f"Task #{task_record.id} successfully created and verified in Odoo Project {task_record.project_id}."
            )

        except OdooAccessDeniedError as e:
            run_record = ExecutionRunRecord(
                run_id=run_id,
                company_id=ctx.company_id,
                user_id=ctx.user_id,
                correlation_id=cid,
                action_scope=action_scope,
                adapter_kind="odoo17_xmlrpc",
                status=RunStatus.FAILED,
                redacted_input_hash=input_hash,
                applied_rules_snapshot=applied_rules_snapshot,
                error_detail=str(e),
                created_at=self._now()
            )
            self.repo.create_execution_run(run_record)
            return TaskCreationResult(
                status=RunStatus.FAILED,
                run_id=run_id,
                correlation_id=cid,
                message=f"Odoo authorization failure: {str(e)}"
            )

        except (TimeoutError, Exception) as e:
            is_timeout = isinstance(e, TimeoutError) or "timed out" in str(e).lower()
            status = RunStatus.RECONCILIATION_REQUIRED if is_timeout else RunStatus.FAILED
            run_record = ExecutionRunRecord(
                run_id=run_id,
                company_id=ctx.company_id,
                user_id=ctx.user_id,
                correlation_id=cid,
                action_scope=action_scope,
                adapter_kind="odoo17_xmlrpc",
                status=status,
                redacted_input_hash=input_hash,
                applied_rules_snapshot=applied_rules_snapshot,
                error_detail=str(e),
                created_at=self._now()
            )
            self.repo.create_execution_run(run_record)
            return TaskCreationResult(
                status=status,
                run_id=run_id,
                correlation_id=cid,
                message=f"Odoo task execution error: {str(e)}"
            )
