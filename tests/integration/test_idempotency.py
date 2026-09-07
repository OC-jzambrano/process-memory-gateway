import uuid

import pytest

from src.api.auth_context import RequestContext, set_current_context
from src.api.service import HostedProcessMemoryService
from src.integrations.mock_executor import MockTaskExecutor
from src.models.enums import CompanyStatus, MembershipStatus, RoleType, RunStatus
from src.models.schemas import Company, Membership, User
from src.storage.repository import MemoryRepository


@pytest.fixture
def service_setup(tmp_path):
    db_path = tmp_path / "idempotency_test.db"
    repo = MemoryRepository(db_path=db_path)
    mock_executor = MockTaskExecutor(default_project_id=142)
    service = HostedProcessMemoryService(repo=repo, executor=mock_executor)

    repo.upsert_company(
        Company(
            company_id="test_co",
            company_slug="test_co",
            name="Test Co",
            status=CompanyStatus.ACTIVE,
        )
    )
    repo.upsert_user(
        User(
            user_id="user_owner", email="owner@test.com", name="Owner", status="active"
        )
    )
    repo.upsert_membership(
        Membership(
            membership_id="mem_1",
            company_id="test_co",
            user_id="user_owner",
            role=RoleType.OWNER,
            status=MembershipStatus.ACTIVE,
        )
    )

    set_current_context(
        RequestContext(
            company_id="test_co",
            company_slug="test_co",
            user_id="user_owner",
            email="owner@test.com",
            role=RoleType.OWNER,
        )
    )

    yield service, repo, mock_executor
    set_current_context(None)


def test_reusing_correlation_id_with_modified_input_is_rejected(service_setup):
    service, _repo, mock_executor = service_setup
    cid = f"corr_{uuid.uuid4().hex}"

    # 1. First task creation
    res1 = service.create_project_task(
        title="Initial Task Title",
        description="Original description",
        definition_of_done=["Item 1"],
        correlation_id=cid,
    )
    assert res1.status == RunStatus.CREATED
    assert len(mock_executor.tasks) == 1

    # 2. Attempt to reuse correlation ID with DIFFERENT title/description
    res2 = service.create_project_task(
        title="Modified Task Title (DIFFERENT)",
        description="Original description",
        definition_of_done=["Item 1"],
        correlation_id=cid,
    )
    assert res2.status == RunStatus.FAILED
    assert "different task parameters" in res2.message
    # Verify zero additional Odoo calls were made
    assert len(mock_executor.tasks) == 1


def test_reconciliation_required_blocks_automatic_retry(service_setup):
    service, _repo, mock_executor = service_setup
    cid = f"corr_timeout_{uuid.uuid4().hex}"

    # Configure executor to simulate timeout/uncertain failure
    def timeout_create(*args, **kwargs):
        raise TimeoutError(
            "Odoo gateway connection timed out while awaiting task ID response."
        )

    mock_executor.create_project_task = timeout_create

    res1 = service.create_project_task(
        title="Timeout Task",
        description="Will timeout",
        definition_of_done=["Item 1"],
        correlation_id=cid,
    )
    assert res1.status == RunStatus.RECONCILIATION_REQUIRED
    assert "timed out" in res1.message

    # Attempt second create with same correlation ID
    res2 = service.create_project_task(
        title="Timeout Task",
        description="Will timeout",
        definition_of_done=["Item 1"],
        correlation_id=cid,
    )
    assert res2.status == RunStatus.RECONCILIATION_REQUIRED
    assert "Manual reconciliation is required" in res2.message
