import pytest

from src.api.auth_context import RequestContext, set_current_context
from src.api.service import HostedProcessMemoryService
from src.models.enums import CompanyStatus, MembershipStatus, RoleType
from src.models.schemas import ActionContext, Company, Membership, User
from src.storage.repository import MemoryRepository


@pytest.fixture
def clean_service(tmp_path):
    repo = MemoryRepository(db_path=tmp_path / "no_dod.db")
    service = HostedProcessMemoryService(repo=repo)

    cid = "test_co_general"
    repo.upsert_company(
        Company(
            company_id=cid,
            company_slug=cid,
            name="General Co",
            status=CompanyStatus.ACTIVE,
        )
    )
    repo.upsert_user(
        User(
            user_id="u_owner",
            email="owner@test.com",
            name="Owner",
            status="active",
        )
    )
    repo.upsert_membership(
        Membership(
            membership_id="mem_1",
            company_id=cid,
            user_id="u_owner",
            role=RoleType.OWNER,
            status=MembershipStatus.ACTIVE,
        )
    )

    set_current_context(
        RequestContext(
            company_id=cid,
            company_slug=cid,
            user_id="u_owner",
            email="owner@test.com",
            role=RoleType.OWNER,
        )
    )
    yield service, repo
    set_current_context(None)


def test_no_keyword_dod_mapping_for_general_instructions(clean_service):
    """
    Arbitrary business policies must NOT have hardcoded 'definition_of_done' constraints
    automatically attached just because the instruction mentions criteria or verification.
    """
    service, _repo = clean_service

    # Instruction 1: General invoice approval policy
    res1 = service.remember_company_instruction(
        instruction_text="All vendor bills exceeding $5,000 must be approved by the Finance Director.",
        context_hint=ActionContext(system="odoo", resource="account.move", operation="create"),
    )
    assert res1.status == "staged"
    assert res1.candidate_id is not None
    # No forced definition_of_done field
    if res1.constraint:
        assert res1.constraint.field != "definition_of_done"

    # Instruction 2: Manufacturing versioning rule
    res2 = service.remember_company_instruction(
        instruction_text="Bills of Materials (BOM) must follow semantic versioning like v1.0.",
        context_hint=ActionContext(system="odoo", resource="mrp.bom", operation="create"),
    )
    assert res2.status == "staged"
    if res2.constraint:
        assert res2.constraint.field != "definition_of_done"

    # Verify both candidates are in repository awaiting review
    candidates = service.list_memory_candidates()
    assert len(candidates) == 2
    assert all(c.status.value == "pending_review" for c in candidates)
