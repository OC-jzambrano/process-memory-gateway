import pytest
import uuid
from src.storage.repository import MemoryRepository
from src.extractor.service import ProcessMemoryExtractorService
from src.integrations.mock_executor import MockTaskExecutor
from src.api.service import HostedProcessMemoryService
from src.api.auth_context import set_current_context
from src.models.schemas import RequestContext, Company, User, Membership, ActionContext, DeterministicConstraint
from src.models.enums import RoleType, RunStatus, ConstraintKind, CompanyStatus, MembershipStatus

@pytest.fixture
def test_setup(tmp_path):
    db_file = tmp_path / "test_pilot.db"
    repo = MemoryRepository(db_path=db_file)
    mock_executor = MockTaskExecutor(default_project_id=142)
    service = HostedProcessMemoryService(repo=repo, executor=mock_executor)

    # Provision Company 1: demo_company
    repo.upsert_company(Company(company_id="demo_company", company_slug="demo_company", name="Demo Company", status=CompanyStatus.ACTIVE))
    repo.upsert_user(User(user_id="demo_owner", email="owner@example.com", name="Demo Owner", status="active"))
    repo.upsert_membership(Membership(membership_id="mem_1", company_id="demo_company", user_id="demo_owner", role=RoleType.OWNER, status=MembershipStatus.ACTIVE))

    # Provision Company 2: other_company
    repo.upsert_company(Company(company_id="other_company", company_slug="other_company", name="Other Company Inc", status=CompanyStatus.ACTIVE))
    repo.upsert_user(User(user_id="other_user", email="user@other.com", name="Other User", status="active"))
    repo.upsert_membership(Membership(membership_id="mem_2", company_id="other_company", user_id="other_user", role=RoleType.OWNER, status=MembershipStatus.ACTIVE))

    return service, repo, mock_executor

def test_full_pilot_demonstration_lifecycle(test_setup):
    service, repo, mock_executor = test_setup

    # =========================================================================
    # STEP 1: Empty company starts with zero rules
    # =========================================================================
    set_current_context(RequestContext(
        company_id="demo_company",
        company_slug="demo_company",
        user_id="demo_owner",
        email="owner@example.com",
        role=RoleType.OWNER
    ))
    context_pack = service.get_company_context(system="odoo", application="project", resource="project.task", operation="create")
    assert len(context_pack.rules) == 0, "Company must start with zero rules"

    # =========================================================================
    # STEP 2: Baseline without rule -> Task creation allowed without DoD
    # =========================================================================
    baseline_res = service.create_project_task(
        title="[PM-PILOT] Baseline without rule",
        description="Confirm that a company with no approved rule can create a normal task.",
        definition_of_done=None,
        correlation_id="corr_baseline_01"
    )
    assert baseline_res.status == RunStatus.CREATED
    assert baseline_res.odoo_task_id is not None
    assert len(mock_executor.tasks) == 1

    # =========================================================================
    # STEP 3: Capture the instruction -> Staged as pending_review
    # =========================================================================
    stage_res = service.remember_company_instruction(
        instruction_text="From now on, every task created in the Odoo Project application must include at least one non-empty Definition of Done item."
    )
    assert stage_res.status == "staged"
    candidate_id = stage_res.candidate_id
    assert stage_res.constraint is not None
    assert stage_res.constraint.kind == ConstraintKind.REQUIRED_NONEMPTY_LIST

    # =========================================================================
    # STEP 4: Prove pending memory does NOT enforce
    # =========================================================================
    active_rules = repo.get_active_rules("demo_company")
    assert len(active_rules) == 0, "Pending candidate must not be active"

    # =========================================================================
    # STEP 5: Approve candidate through chat
    # =========================================================================
    review_res = service.review_memory_candidate(
        candidate_id=candidate_id,
        decision="approve"
    )
    assert review_res.status == "approved"
    assert review_res.rule_id is not None
    assert review_res.version == 1

    # Verify rule is now in canonical active memory
    active_rules = repo.get_active_rules("demo_company")
    assert len(active_rules) == 1
    assert active_rules[0].structured_constraint.kind == ConstraintKind.REQUIRED_NONEMPTY_LIST

    # =========================================================================
    # STEP 6: Fresh Chat / Attempt task creation without DoD -> BLOCKED
    # =========================================================================
    task_count_before = len(mock_executor.tasks)
    blocked_res = service.create_project_task(
        title="[PM-PILOT] Memory across sessions",
        description="Verify that approved company memory survives a new agent session.",
        definition_of_done=None,
        correlation_id="corr_pilot_session_01"
    )
    assert blocked_res.status == RunStatus.NEEDS_CLARIFICATION
    assert "definition_of_done" in blocked_res.missing_information
    assert len(blocked_res.applied_rule_ids) >= 1
    assert len(mock_executor.tasks) == task_count_before, "Zero Odoo calls must occur when blocked!"

    # =========================================================================
    # STEP 7: Correct and create the task with DoD
    # =========================================================================
    success_res = service.create_project_task(
        title="[PM-PILOT] Memory across sessions",
        description="Verify that approved company memory survives a new agent session.",
        definition_of_done=[
            "The task is visible in Odoo project 142.",
            "The task description contains this Definition of Done.",
            "Process Memory returns the created Odoo task ID."
        ],
        correlation_id="corr_pilot_session_01"
    )
    assert success_res.status == RunStatus.CREATED
    assert success_res.odoo_task_id is not None
    created_task_id = success_res.odoo_task_id
    created_task = mock_executor.get_project_task(created_task_id)
    assert created_task is not None
    assert "Definition of Done" in created_task.description
    assert "The task is visible in Odoo project 142." in created_task.description

    # =========================================================================
    # STEP 8: Test Duplicate / Idempotency Protection
    # =========================================================================
    task_count_after = len(mock_executor.tasks)
    retry_res = service.create_project_task(
        title="[PM-PILOT] Memory across sessions",
        description="Verify that approved company memory survives a new agent session.",
        definition_of_done=["The task is visible in Odoo project 142."],
        correlation_id="corr_pilot_session_01"  # Same correlation ID
    )
    assert retry_res.status == RunStatus.CREATED
    assert retry_res.odoo_task_id == created_task_id
    assert len(mock_executor.tasks) == task_count_after, "Reusing correlation ID must not create duplicate Odoo tasks!"

    # =========================================================================
    # STEP 9: Multi-Tenant Isolation (Second Company is Unaffected)
    # =========================================================================
    set_current_context(RequestContext(
        company_id="other_company",
        company_slug="other_company",
        user_id="other_user",
        email="user@other.com",
        role=RoleType.OWNER
    ))
    other_pack = service.get_company_context(system="odoo", application="project", resource="project.task", operation="create")
    assert len(other_pack.rules) == 0, "Second company must NOT receive Company 1's rules"

    other_task_res = service.create_project_task(
        title="[PM-PILOT] Company 2 Task without DoD",
        description="Company 2 has no DoD rule so task should succeed.",
        definition_of_done=None,
        correlation_id="corr_co2_01"
    )
    assert other_task_res.status == RunStatus.CREATED
    assert other_task_res.odoo_task_id is not None
