import pytest
from src.governance.task_validator import TaskValidator
from src.models.schemas import CanonicalRule, DeterministicConstraint, ActionContext
from src.models.enums import RunStatus, ConstraintKind, RuleType, Severity, EnforcementMode

@pytest.fixture
def validator():
    return TaskValidator()

def test_new_company_with_no_rules_allows_task(validator):
    """An empty company with zero rules allows task creation without DoD."""
    res = validator.validate_task_creation(
        title="[PM-PILOT] Baseline without rule",
        description="Testing task creation on empty company",
        definition_of_done=None,
        active_rules=[]
    )
    assert res.is_valid is True
    assert res.status == RunStatus.CREATED
    assert len(res.missing_fields) == 0
    assert len(res.applied_rules) == 0

def test_unstructured_advisory_rule_does_not_block_task(validator):
    """An advisory natural language rule without deterministic constraint cannot block execution."""
    advisory_rule = CanonicalRule(
        rule_id="rule_advisory_01",
        client_id="test_co",
        rule_text="Tasks should generally be short and clean.",
        rule_type=RuleType.BUSINESS_PREFERENCE,
        severity=Severity.INFO,
        enforcement_mode=EnforcementMode.ADVISORY,
        version=1,
        approved_by="owner"
    )
    res = validator.validate_task_creation(
        title="[PM-PILOT] Task with advisory rule",
        description="Normal description",
        definition_of_done=None,
        active_rules=[advisory_rule]
    )
    assert res.is_valid is True
    assert res.status == RunStatus.CREATED
    assert len(res.missing_fields) == 0

def test_approved_dod_rule_blocks_task_without_dod(validator):
    """An approved canonical rule with required_nonempty_list constraint blocks tasks missing DoD."""
    dod_rule = CanonicalRule(
        rule_id="rule_dod_01",
        client_id="test_co",
        rule_text="Every created task must include a Definition of Done.",
        rule_type=RuleType.OPERATIONAL_CONSTRAINT,
        severity=Severity.CRITICAL,
        enforcement_mode=EnforcementMode.BLOCKING,
        version=1,
        structured_scope=ActionContext(system="odoo", application="project", resource="project.task", operation="create"),
        structured_constraint=DeterministicConstraint(kind=ConstraintKind.REQUIRED_NONEMPTY_LIST, field="definition_of_done", min_items=1),
        approved_by="owner"
    )

    # Missing DoD
    res = validator.validate_task_creation(
        title="[PM-PILOT] Task missing DoD",
        description="Trying to create without DoD",
        definition_of_done=None,
        active_rules=[dod_rule]
    )
    assert res.is_valid is False
    assert res.status == RunStatus.NEEDS_CLARIFICATION
    assert "definition_of_done" in res.missing_fields
    assert "rule_dod_01" in res.applied_rule_ids

    # Empty list DoD
    res_empty = validator.validate_task_creation(
        title="[PM-PILOT] Task empty DoD",
        description="Trying to create with empty list",
        definition_of_done=[],
        active_rules=[dod_rule]
    )
    assert res_empty.is_valid is False
    assert res_empty.status == RunStatus.NEEDS_CLARIFICATION

    # Blank string DoD
    res_blank = validator.validate_task_creation(
        title="[PM-PILOT] Task blank DoD",
        description="Trying to create with whitespace only",
        definition_of_done=["   "],
        active_rules=[dod_rule]
    )
    assert res_blank.is_valid is False
    assert res_blank.status == RunStatus.NEEDS_CLARIFICATION

def test_task_with_valid_dod_passes_validation(validator):
    """When a valid DoD list is provided, validation succeeds."""
    dod_rule = CanonicalRule(
        rule_id="rule_dod_01",
        client_id="test_co",
        rule_text="Every created task must include a Definition of Done.",
        rule_type=RuleType.OPERATIONAL_CONSTRAINT,
        severity=Severity.CRITICAL,
        enforcement_mode=EnforcementMode.BLOCKING,
        version=1,
        structured_scope=ActionContext(system="odoo", application="project", resource="project.task", operation="create"),
        structured_constraint=DeterministicConstraint(kind=ConstraintKind.REQUIRED_NONEMPTY_LIST, field="definition_of_done", min_items=1),
        approved_by="owner"
    )

    res = validator.validate_task_creation(
        title="[PM-PILOT] Compliant Task",
        description="Fully specified task",
        definition_of_done=["Task is visible in Odoo", "Description verified"],
        active_rules=[dod_rule]
    )
    assert res.is_valid is True
    assert res.status == RunStatus.CREATED
    assert len(res.missing_fields) == 0
    assert "rule_dod_01" in res.applied_rule_ids
