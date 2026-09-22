import pytest

from src.governance.memory_retriever import MemoryRetriever
from src.models.enums import CompanyStatus, EnforcementMode, RuleType, Severity
from src.models.schemas import ActionContext, Company
from src.storage.repository import MemoryRepository


@pytest.fixture
def repo(tmp_path):
    db_file = tmp_path / "test_retrieval.db"
    r = MemoryRepository(db_path=db_file)
    r.upsert_company(
        Company(
            company_id="co_retrieval",
            company_slug="co_retrieval",
            name="Retrieval Test Co",
            status=CompanyStatus.ACTIVE,
        )
    )
    return r


def test_scoped_retrieval_excludes_unrelated_modules(repo):
    """Retrieving task creation memory excludes unrelated Sales, Inventory, and MRP rules."""
    retriever = MemoryRetriever(repo=repo)

    # 1. Add Task Creation Rule
    from src.models.enums import RuleStatus, SourceType
    from src.models.schemas import CandidateRule, ExtractionSession

    repo.create_session(
        ExtractionSession(
            session_id="sess_1",
            client_id="co_retrieval",
            process_name="project",
            source_type=SourceType.USER_INTERACTION,
            interaction_text="Setup rules for project",
            model_id="test",
        )
    )
    repo.save_candidates(
        [
            CandidateRule(
                candidate_id="cand_t1",
                session_id="sess_1",
                client_id="co_retrieval",
                rule_text="DoD required for all project tasks.",
                rule_type=RuleType.OPERATIONAL_CONSTRAINT,
                severity=Severity.INFO,
                enforcement_mode=EnforcementMode.ADVISORY,
                source_quote="DoD required",
                confidence=0.95,
                status=RuleStatus.PENDING_REVIEW,
                structured_scope=ActionContext(
                    system="odoo",
                    application="project",
                    resource="work.item",
                    operation="create",
                ),
            )
        ]
    )
    repo.review_candidate(
        candidate_id="cand_t1",
        decision="approve",
        reviewer="owner",
        client_id="co_retrieval",
    )
    # 2. Directly save candidate for MRP and approve it
    from src.models.enums import RuleStatus
    from src.models.schemas import CandidateRule

    repo.save_candidates(
        [
            CandidateRule(
                candidate_id="cand_mrp",
                session_id="sess_1",
                client_id="co_retrieval",
                rule_text="MRP module install requires Operations Lead approval.",
                rule_type=RuleType.APPROVAL_POLICY,
                severity=Severity.CRITICAL,
                enforcement_mode=EnforcementMode.REQUIRES_APPROVAL,
                source_quote="Operations Lead approval",
                confidence=0.95,
                status=RuleStatus.PENDING_REVIEW,
                structured_scope=ActionContext(
                    system="odoo",
                    application="mrp",
                    resource="ir.module.module",
                    operation="create",
                ),
            ),
            CandidateRule(
                candidate_id="cand_sales",
                session_id="sess_1",
                client_id="co_retrieval",
                rule_text="Discounts over 15% require CFO sign-off.",
                rule_type=RuleType.APPROVAL_POLICY,
                severity=Severity.WARNING,
                enforcement_mode=EnforcementMode.REQUIRES_APPROVAL,
                source_quote="CFO sign-off",
                confidence=0.9,
                status=RuleStatus.PENDING_REVIEW,
                structured_scope=ActionContext(
                    system="odoo",
                    application="sale",
                    resource="sale.order",
                    operation="write",
                ),
            ),
            CandidateRule(
                candidate_id="cand_task",
                session_id="sess_1",
                client_id="co_retrieval",
                rule_text="Tasks must include measurable acceptance criteria.",
                rule_type=RuleType.OPERATIONAL_CONSTRAINT,
                severity=Severity.INFO,
                enforcement_mode=EnforcementMode.ADVISORY,
                source_quote="acceptance criteria",
                confidence=0.92,
                status=RuleStatus.PENDING_REVIEW,
                structured_scope=ActionContext(
                    system="odoo",
                    application="project",
                    resource="work.item",
                    operation="create",
                ),
            ),
        ]
    )

    repo.review_candidate(
        "cand_mrp", decision="approve", reviewer="owner", client_id="co_retrieval"
    )
    repo.review_candidate(
        "cand_sales", decision="approve", reviewer="owner", client_id="co_retrieval"
    )
    repo.review_candidate(
        "cand_task", decision="approve", reviewer="owner", client_id="co_retrieval"
    )

    # 3. Retrieve Memory Pack specifically for work.item:create
    pack = retriever.retrieve_pack(
        company_id="co_retrieval",
        company_slug="co_retrieval",
        system="odoo",
        application="project",
        resource="work.item",
        operation="create",
    )

    assert len(pack.rules) >= 1
    # Verify retrieved rules are work.item related
    rule_texts = [r.rule_text for r in pack.rules]
    assert any("acceptance criteria" in t.lower() for t in rule_texts)
    # Verify MRP and Sales are not exact scope matches
    exact_scoped = [
        r for r in pack.rules if r.scope and r.scope.resource == "work.item"
    ]
    assert len(exact_scoped) >= 1


def test_create_scope_includes_field_rules_for_missing_required_fields(repo):
    """Create-scoped field rules are retrieved even when caller omitted that field."""
    retriever = MemoryRetriever(repo=repo)

    from src.models.enums import RuleStatus, SourceType
    from src.models.schemas import CandidateRule, ExtractionSession

    repo.create_session(
        ExtractionSession(
            session_id="sess_required_field",
            client_id="co_retrieval",
            process_name="project",
            source_type=SourceType.USER_INTERACTION,
            interaction_text="Setup required task fields",
            model_id="test",
        )
    )
    repo.save_candidates(
        [
            CandidateRule(
                candidate_id="cand_issue_type",
                session_id="sess_required_field",
                client_id="co_retrieval",
                rule_text="All Odoo project tasks must set Project Issue Type to Task.",
                rule_type=RuleType.OPERATIONAL_CONSTRAINT,
                severity=Severity.INFO,
                enforcement_mode=EnforcementMode.BLOCKING,
                source_quote="Project Issue Type to Task",
                confidence=0.95,
                status=RuleStatus.PENDING_REVIEW,
                structured_scope=ActionContext(
                    system="odoo",
                    application="project",
                    resource="project.task",
                    operation="create",
                    fields=["project_issue_type_id"],
                ),
            )
        ]
    )
    reviewed = repo.review_candidate(
        "cand_issue_type",
        decision="approve",
        reviewer="owner",
        client_id="co_retrieval",
    )

    pack = retriever.retrieve_pack(
        company_id="co_retrieval",
        company_slug="co_retrieval",
        system="odoo",
        application="project",
        resource="project.task",
        operation="create",
        fields=["name", "description", "user_ids"],
    )

    assert reviewed is not None
    assert [r.rule_id for r in pack.rules] == [reviewed.rule_id]


def test_create_scope_does_not_include_broad_field_only_rules(repo):
    """The create-field bypass only applies to exact create scopes."""
    retriever = MemoryRetriever(repo=repo)

    from src.models.enums import RuleStatus, SourceType
    from src.models.schemas import CandidateRule, ExtractionSession

    repo.create_session(
        ExtractionSession(
            session_id="sess_broad_field",
            client_id="co_retrieval",
            process_name="general",
            source_type=SourceType.USER_INTERACTION,
            interaction_text="Setup broad field rule",
            model_id="test",
        )
    )
    repo.save_candidates(
        [
            CandidateRule(
                candidate_id="cand_margin",
                session_id="sess_broad_field",
                client_id="co_retrieval",
                rule_text="Margin changes require approval.",
                rule_type=RuleType.OPERATIONAL_CONSTRAINT,
                severity=Severity.INFO,
                enforcement_mode=EnforcementMode.BLOCKING,
                source_quote="Margin changes require approval",
                confidence=0.95,
                status=RuleStatus.PENDING_REVIEW,
                structured_scope=ActionContext(fields=["margin"]),
            )
        ]
    )
    repo.review_candidate(
        "cand_margin",
        decision="approve",
        reviewer="owner",
        client_id="co_retrieval",
    )

    pack = retriever.retrieve_pack(
        company_id="co_retrieval",
        company_slug="co_retrieval",
        system="odoo",
        application="project",
        resource="project.task",
        operation="create",
        fields=["name", "description"],
    )

    assert pack.rules == []
