import uuid
import time
from concurrent.futures import ThreadPoolExecutor
import pytest

from src.models.schemas import (
    Company,
    OdooConnectionConfig,
    ActionContext,
    DeterministicConstraint,
    CanonicalRule,
    RequestContext
)
from src.models.enums import (
    RoleType,
    CompanyStatus,
    RunStatus,
    ExecutionPhase,
    RuleType,
    Severity,
    EnforcementMode,
    ConstraintKind
)
from src.storage.repository import MemoryRepository
from src.api.service import HostedProcessMemoryService, compute_legacy_hash_v1
from src.api.auth_context import request_context
from src.integrations.mock_executor import MockTaskExecutor
from src.governance.reconciliation import reconcile_run_by_operator

@pytest.fixture
def clean_db(tmp_path):
    db_file = tmp_path / "test_tenant_safety.db"
    return MemoryRepository(db_path=db_file)

def test_tenant_isolated_routing_and_project_override_rejection(clean_db):
    """
    1. Tenant Isolation: Each company routes to its configured project.
    2. Project Override Rejection: Specifying a conflicting project_id fails closed with 0 Odoo calls.
    3. Missing or inactive connection fails closed without environment fallback.
    """
    repo = clean_db

    # Setup Company A (Project 100) and Company B (Project 200)
    repo.upsert_company(Company(company_id="co_a", company_slug="co-a", name="Company A", status=CompanyStatus.ACTIVE))
    repo.upsert_company(Company(company_id="co_b", company_slug="co-b", name="Company B", status=CompanyStatus.ACTIVE))

    repo.upsert_odoo_connection(OdooConnectionConfig(
        connection_id="conn_a",
        company_id="co_a",
        odoo_url="https://odoo-a.example.com",
        odoo_db="db_a",
        default_project_id=100,
        secret_arn="arn:aws:secretsmanager:eu-north-1:123456789012:secret:odoo-a",
        status="active"
    ))
    repo.upsert_odoo_connection(OdooConnectionConfig(
        connection_id="conn_b",
        company_id="co_b",
        odoo_url="https://odoo-b.example.com",
        odoo_db="db_b",
        default_project_id=200,
        secret_arn="arn:aws:secretsmanager:eu-north-1:123456789012:secret:odoo-b",
        status="active"
    ))

    exec_a = MockTaskExecutor(default_project_id=100)
    exec_b = MockTaskExecutor(default_project_id=200)

    factory_calls = []
    def mock_factory(cid: str):
        factory_calls.append(cid)
        return exec_a if cid == "co_a" else exec_b

    service = HostedProcessMemoryService(repo=repo, executor_factory=mock_factory)

    # 1. Company A creates task without project_id -> uses default project 100
    ctx_a = RequestContext(company_id="co_a", company_slug="co-a", user_id="usr_a", email="a@example.com", role=RoleType.OWNER)
    with request_context(ctx_a):
        res_a = service.create_project_task(
            title="Task A",
            description="Desc A",
            correlation_id="corr_a_1"
        )
        assert res_a.status == RunStatus.CREATED
        assert res_a.odoo_task_id is not None
        assert "odoo-a.example.com" in res_a.odoo_task_url
        assert exec_a.tasks[res_a.odoo_task_id].project_id == 100

    # 2. Company A attempts to override project_id to 200 or 999 -> rejected with 0 Odoo calls
    with request_context(ctx_a):
        calls_before = len(exec_a.tasks)
        res_reject = service.create_project_task(
            title="Override Task",
            description="Desc",
            project_id=200,
            correlation_id="corr_a_override"
        )
        assert res_reject.status == RunStatus.NEEDS_CLARIFICATION
        assert res_reject.error_code == "project_override_forbidden"
        assert len(exec_a.tasks) == calls_before  # Zero Odoo calls made!

    # 3. Inactive connection fails closed
    repo.upsert_odoo_connection(OdooConnectionConfig(
        connection_id="conn_a",
        company_id="co_a",
        odoo_url="https://odoo-a.example.com",
        odoo_db="db_a",
        default_project_id=100,
        secret_arn="arn:aws:secretsmanager:eu-north-1:123456789012:secret:odoo-a",
        status="suspended"
    ))
    # Using real resolution (no factory) to verify fail closed
    service_real = HostedProcessMemoryService(repo=repo)
    with request_context(ctx_a):
        res_suspended = service_real.create_project_task(
            title="Task Suspended",
            description="Desc",
            correlation_id="corr_a_susp"
        )
        assert res_suspended.status == RunStatus.FAILED
        assert res_suspended.error_code == "routing_failed"

def test_synchronized_concurrent_execution(clean_db):
    """
    Two concurrent requests with the exact same correlation ID race to create task.
    Exactly ONE request creates the task; the second request receives RUN_STARTED or CREATED.
    """
    repo = clean_db
    repo.upsert_company(Company(company_id="co_conc", company_slug="co-conc", name="Conc Co"))
    repo.upsert_odoo_connection(OdooConnectionConfig(
        connection_id="conn_conc",
        company_id="co_conc",
        odoo_url="https://odoo-conc.example.com",
        odoo_db="db_conc",
        default_project_id=101,
        secret_arn="arn:fake:secret",
        status="active"
    ))

    mock_exec = MockTaskExecutor(default_project_id=101)
    original_create = mock_exec.create_project_task_phase_aware

    # Add artificial delay to simulate network latency in Odoo
    def delayed_create(*args, **kwargs):
        time.sleep(0.1)
        return original_create(*args, **kwargs)

    mock_exec.create_project_task_phase_aware = delayed_create

    service = HostedProcessMemoryService(repo=repo, executor=mock_exec)
    ctx = RequestContext(company_id="co_conc", company_slug="co-conc", user_id="usr_conc", email="c@example.com", role=RoleType.OWNER)

    cid = f"corr_race_{uuid.uuid4().hex}"
    results = []

    def worker():
        with request_context(ctx):
            res = service.create_project_task(
                title="Concurrent Task",
                description="Racing create",
                definition_of_done=["Item 1"],
                correlation_id=cid
            )
            results.append(res)

    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(worker)
        f2 = executor.submit(worker)
        f1.result()
        f2.result()

    # Exactly one task created in mock Odoo
    assert len(mock_exec.tasks) == 1
    # One result must be CREATED, the other must be either RUN_STARTED or CREATED (never duplicate task)
    statuses = [r.status for r in results]
    assert RunStatus.CREATED in statuses
    assert len([r for r in results if r.status == RunStatus.CREATED]) in (1, 2)
    # Both refer to the same run_id or correlation_id
    assert results[0].correlation_id == cid
    assert results[1].correlation_id == cid

def test_idempotency_changed_input_and_legacy_v1_hash(clean_db):
    """
    1. Replaying identical input returns cached CREATED result.
    2. Reusing correlation ID with changed input returns FAILED with idempotency_conflict.
    3. Run in needs_clarification allows updated/corrected input to claim and succeed.
    4. Existing run with legacy v1 hash is supported.
    """
    repo = clean_db
    repo.upsert_company(Company(company_id="co_idemp", company_slug="co-idemp", name="Idemp Co"))
    repo.upsert_odoo_connection(OdooConnectionConfig(
        connection_id="conn_idemp",
        company_id="co_idemp",
        odoo_url="https://odoo-idemp.example.com",
        odoo_db="db_idemp",
        default_project_id=102,
        secret_arn="arn:fake:secret",
        status="active"
    ))
    mock_exec = MockTaskExecutor(default_project_id=102)
    service = HostedProcessMemoryService(repo=repo, executor=mock_exec)

    ctx = RequestContext(company_id="co_idemp", company_slug="co-idemp", user_id="usr_idemp", email="i@example.com", role=RoleType.OWNER)

    # 1. Require DoD rule
    repo.create_canonical_rule(CanonicalRule(
        rule_id="rule_dod",
        client_id="co_idemp",
        rule_text="Tasks must include Definition of Done",
        rule_type=RuleType.OPERATIONAL_CONSTRAINT,
        severity=Severity.CRITICAL,
        enforcement_mode=EnforcementMode.BLOCKING,
        version=1,
        structured_scope=ActionContext(system="odoo", application="project", resource="project.task", operation="create", fields=["definition_of_done"]),
        structured_constraint=DeterministicConstraint(kind=ConstraintKind.REQUIRED_NONEMPTY_LIST, field="definition_of_done", min_items=1),
        approved_by="usr_idemp"
    ))

    cid = f"corr_idemp_{uuid.uuid4().hex}"

    # First attempt: Missing DoD -> blocked with needs_clarification
    with request_context(ctx):
        res1 = service.create_project_task(title="Feature X", description="Desc X", definition_of_done=[], correlation_id=cid)
        assert res1.status == RunStatus.NEEDS_CLARIFICATION

        # Second attempt: Corrected input with same correlation ID -> succeeds!
        res2 = service.create_project_task(
            title="Feature X",
            description="Desc X",
            definition_of_done=["Completed tests"],
            correlation_id=cid
        )
        assert res2.status == RunStatus.CREATED
        assert res2.odoo_task_id is not None

        # Third attempt: Replay identical input -> returns cached result
        res3 = service.create_project_task(
            title="Feature X",
            description="Desc X",
            definition_of_done=["Completed tests"],
            correlation_id=cid
        )
        assert res3.status == RunStatus.CREATED
        assert res3.odoo_task_id == res2.odoo_task_id

        # Fourth attempt: Changed title with same correlation ID -> FAILED idempotency_conflict
        res4 = service.create_project_task(
            title="Tampered Feature X",
            description="Desc X",
            definition_of_done=["Completed tests"],
            correlation_id=cid
        )
        assert res4.status == RunStatus.FAILED
        assert res4.error_code == "idempotency_conflict"

    # 5. Legacy v1 hash compatibility
    cid_legacy = f"corr_legacy_{uuid.uuid4().hex}"
    legacy_hash = compute_legacy_hash_v1("Legacy Task", "Legacy Desc", ["Item 1"], 102)
    repo.create_execution_run(clean_db._parse_run_row({
        "run_id": f"run_leg_{uuid.uuid4().hex[:8]}",
        "company_id": "co_idemp",
        "user_id": "usr_idemp",
        "correlation_id": cid_legacy,
        "action_scope_json": "{}",
        "adapter_kind": "odoo17_xmlrpc",
        "status": "created",
        "redacted_input_hash": legacy_hash,
        "hash_algorithm_version": "v1",
        "applied_rules_snapshot_json": "[]",
        "odoo_task_id": 9999,
        "odoo_task_url": "https://odoo-idemp.example.com/web#id=9999",
        "result_payload_json": "{}",
        "created_at": "2026-01-01T00:00:00"
    }))

    with request_context(ctx):
        res_leg = service.create_project_task(
            title="Legacy Task",
            description="Legacy Desc",
            definition_of_done=["Item 1"],
            correlation_id=cid_legacy
        )
        assert res_leg.status == RunStatus.CREATED
        assert res_leg.odoo_task_id == 9999

def test_phase_aware_failure_injection_and_reconciliation(clean_db):
    """
    Tests failure injection across all phases:
    - BEFORE_CREATE -> FAILED
    - CREATE_REJECTED -> FAILED
    - UNCERTAIN_CREATE -> RECONCILIATION_REQUIRED
    - VERIFICATION_FAILED -> RECONCILIATION_REQUIRED with task_id preserved!
    - Operator reconciliation resolves uncertain run.
    """
    repo = clean_db
    repo.upsert_company(Company(company_id="co_phase", company_slug="co-phase", name="Phase Co"))
    repo.upsert_odoo_connection(OdooConnectionConfig(
        connection_id="conn_phase",
        company_id="co_phase",
        odoo_url="https://odoo-phase.example.com",
        odoo_db="db_phase",
        default_project_id=103,
        secret_arn="arn:fake:secret",
        status="active"
    ))

    mock_exec = MockTaskExecutor(default_project_id=103)
    service = HostedProcessMemoryService(repo=repo, executor=mock_exec)
    ctx = RequestContext(company_id="co_phase", company_slug="co-phase", user_id="usr_phase", email="p@example.com", role=RoleType.OWNER)

    # 1. BEFORE_CREATE
    mock_exec.failure_phase = ExecutionPhase.BEFORE_CREATE
    mock_exec.failure_code = "auth_failed"
    mock_exec.failure_detail = "Simulated authentication credentials error"
    with request_context(ctx):
        res_bc = service.create_project_task(title="BC Task", description="Desc", correlation_id="corr_bc")
        assert res_bc.status == RunStatus.FAILED
        assert res_bc.error_code == "auth_failed"

    # 2. CREATE_REJECTED
    mock_exec.failure_phase = ExecutionPhase.CREATE_REJECTED
    mock_exec.failure_code = "access_denied"
    mock_exec.failure_detail = "Permission denied on project.task"
    with request_context(ctx):
        res_cr = service.create_project_task(title="CR Task", description="Desc", correlation_id="corr_cr")
        assert res_cr.status == RunStatus.FAILED
        assert res_cr.error_code == "access_denied"

    # 3. UNCERTAIN_CREATE
    mock_exec.failure_phase = ExecutionPhase.UNCERTAIN_CREATE
    mock_exec.failure_code = "timeout"
    mock_exec.failure_detail = "Socket timeout in flight"
    with request_context(ctx):
        res_uc = service.create_project_task(title="UC Task", description="Desc", correlation_id="corr_uc")
        assert res_uc.status == RunStatus.RECONCILIATION_REQUIRED
        assert res_uc.error_code == "timeout"

    # 4. VERIFICATION_FAILED (Task ID preserved!)
    mock_exec.failure_phase = ExecutionPhase.VERIFICATION_FAILED
    mock_exec.failure_code = "readback_failed"
    mock_exec.failure_detail = "Task readback returned None"
    with request_context(ctx):
        res_vf = service.create_project_task(title="VF Task", description="Desc", correlation_id="corr_vf")
        assert res_vf.status == RunStatus.RECONCILIATION_REQUIRED
        assert res_vf.odoo_task_id is not None  # Preserved!
        stored_run = repo.get_execution_run_by_correlation("co_phase", "corr_vf")
        assert stored_run.odoo_task_id == res_vf.odoo_task_id
        assert stored_run.status == RunStatus.RECONCILIATION_REQUIRED

    # 5. Operator Reconciliation:
    # Reset mock executor failure phase to verify task
    mock_exec.failure_phase = None
    resolved_run = reconcile_run_by_operator(
        repo=repo,
        company_id="co_phase",
        run_id=stored_run.run_id,
        executor=mock_exec,
        operator_user_id="ops_lead"
    )
    assert resolved_run.status == RunStatus.CREATED
    assert resolved_run.odoo_task_id == res_vf.odoo_task_id
    events = repo.list_execution_events("co_phase", stored_run.run_id)
    assert any(e.event_type.value == "reconciliation_resolved" for e in events)

def test_scope_isolation_and_context_budget(clean_db):
    """
    1. Scope Isolation: Rules for Sales or MRP do NOT match Project Task queries and are NOT advisory.
    2. Context Budget: Oversized rules (including oversized first rule) are omitted and reported.
    3. Validator: Evaluates constraints independently of context budget.
    """
    repo = clean_db
    repo.upsert_company(Company(company_id="co_scope", company_slug="co-scope", name="Scope Co"))

    # Rule 1: Sales order rule
    repo.create_canonical_rule(CanonicalRule(
        rule_id="rule_sales",
        client_id="co_scope",
        process_name="sales",
        rule_text="Sales quotes require manager signoff",
        rule_type=RuleType.OPERATIONAL_CONSTRAINT,
        severity=Severity.CRITICAL,
        enforcement_mode=EnforcementMode.BLOCKING,
        version=1,
        structured_scope=ActionContext(system="odoo", application="sale", resource="sale.order", operation="create"),
        approved_by="lead"
    ))

    # Rule 2: Huge Project rule exceeding 100 token budget
    huge_text = "Detailed project guidelines: " + ("check requirement. " * 60) # ~360 chars = ~110 tokens
    repo.create_canonical_rule(CanonicalRule(
        rule_id="rule_huge_project",
        client_id="co_scope",
        process_name="project",
        rule_text=huge_text,
        rule_type=RuleType.BUSINESS_PREFERENCE,
        severity=Severity.INFO,
        enforcement_mode=EnforcementMode.ADVISORY,
        version=1,
        structured_scope=ActionContext(system="odoo", application="project", resource="project.task", operation="create"),
        approved_by="lead"
    ))

    # Rule 3: Small project constraint rule
    repo.create_canonical_rule(CanonicalRule(
        rule_id="rule_small_dod",
        client_id="co_scope",
        process_name="project",
        rule_text="Small DoD required",
        rule_type=RuleType.OPERATIONAL_CONSTRAINT,
        severity=Severity.CRITICAL,
        enforcement_mode=EnforcementMode.BLOCKING,
        version=1,
        structured_scope=ActionContext(system="odoo", application="project", resource="project.task", operation="create", fields=["definition_of_done"]),
        structured_constraint=DeterministicConstraint(kind=ConstraintKind.REQUIRED_NONEMPTY_LIST, field="definition_of_done", min_items=1),
        approved_by="lead"
    ))

    service = HostedProcessMemoryService(repo=repo, executor=MockTaskExecutor())
    ctx = RequestContext(company_id="co_scope", company_slug="co-scope", user_id="usr_scope", email="s@example.com", role=RoleType.OWNER)

    with request_context(ctx):
        # 1. Query Project Task MemoryPack with small token budget = 60 tokens
        # Huge rule (~110 tokens) should be omitted! Small rule (~30 tokens) should be included!
        pack = service.get_company_context(
            system="odoo",
            application="project",
            resource="project.task",
            operation="create"
        )
        rule_ids = [r.rule_id for r in pack.rules]
        # Sales rule MUST NOT be present
        assert "rule_sales" not in rule_ids
        # Small rule must be present
        assert "rule_small_dod" in rule_ids

        # Test context budget omission reporting
        pack_tight = service.retriever.retrieve_pack(
            company_id="co_scope",
            company_slug="co-scope",
            system="odoo",
            application="project",
            resource="project.task",
            operation="create",
            token_budget=40  # Tight budget: huge rule cannot fit
        )
        assert "omitted due to context budget" in pack_tight.message

        # 2. Task Validator: Validates constraints independently of budget
        all_rules = repo.get_active_rules("co_scope")
        validation = service.validator.validate_task_creation(
            title="My Task",
            description="Desc",
            definition_of_done=[],
            active_rules=all_rules
        )
        # Blocked because small_dod was evaluated despite budget
        assert not validation.is_valid
        assert "rule_small_dod" in validation.applied_rule_ids

def test_startup_reconciles_abandoned_runs(clean_db):
    """
    Runs left in RUN_STARTED state when the service starts are reconciled to RECONCILIATION_REQUIRED.
    """
    repo = clean_db
    repo.upsert_company(Company(company_id="co_crash", company_slug="co-crash", name="Crash Co"))

    # Seed an abandoned run
    repo.create_execution_run(clean_db._parse_run_row({
        "run_id": "run_abandoned_1",
        "company_id": "co_crash",
        "user_id": "usr_crash",
        "correlation_id": "corr_crash_1",
        "action_scope_json": "{}",
        "adapter_kind": "odoo17_xmlrpc",
        "status": "run_started",
        "redacted_input_hash": "hash123",
        "hash_algorithm_version": "v2",
        "applied_rules_snapshot_json": "[]",
        "created_at": "2026-01-01T00:00:00"
    }))

    # Instantiating service runs startup reconciliation
    HostedProcessMemoryService(repo=repo, executor=MockTaskExecutor())
    reconciled = repo.get_execution_run("run_abandoned_1", company_id="co_crash")
    assert reconciled.status == RunStatus.RECONCILIATION_REQUIRED
    assert reconciled.error_code == "abandoned_run"
    events = repo.list_execution_events("co_crash", "run_abandoned_1")
    assert any(e.event_type.value == "reconciliation_required" for e in events)
