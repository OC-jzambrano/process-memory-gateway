from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from src.models.schemas import CanonicalRule, DeterministicConstraint, ActionContext
from src.models.enums import RunStatus, ConstraintKind, EnforcementMode

class TaskValidationResult(BaseModel):
    is_valid: bool
    status: RunStatus = RunStatus.CREATED
    missing_fields: List[str] = Field(default_factory=list)
    applied_rules: List[CanonicalRule] = Field(default_factory=list)
    applied_rule_ids: List[str] = Field(default_factory=list)
    message: str

class TaskValidator:
    """
    Evaluates task creation readiness against active canonical company memory.
    
    Principles:
    1. Validator capability exists in code, but no company rule is seeded by default.
    2. A candidate rule in pending_review has zero operational effect.
    3. Only an approved canonical rule with a deterministic constraint activates enforcement.
    4. Unstructured natural language rules remain advisory and cannot block execution.
    5. Returns RunStatus.NEEDS_CLARIFICATION with zero Odoo calls when required data is missing.
    """

    def validate_task_creation(
        self,
        title: str,
        description: str,
        definition_of_done: Optional[List[str]],
        active_rules: List[CanonicalRule],
        project_id: Optional[int] = None
    ) -> TaskValidationResult:
        if not title or not title.strip():
            return TaskValidationResult(
                is_valid=False,
                status=RunStatus.NEEDS_CLARIFICATION,
                missing_fields=["title"],
                message="Task title is required."
            )

        applied_rules: List[CanonicalRule] = []
        applied_rule_ids: List[str] = []
        missing_fields: List[str] = []

        for rule in active_rules:
            constraint = rule.structured_constraint
            if not constraint:
                # Advisory rule: does not block
                continue

            if constraint.kind == ConstraintKind.REQUIRED_NONEMPTY_LIST:
                target_field = constraint.field or "definition_of_done"
                min_items = constraint.min_items or 1

                if target_field == "definition_of_done":
                    # Check definition_of_done content
                    valid_items = [
                        item.strip() for item in (definition_of_done or [])
                        if isinstance(item, str) and item.strip()
                    ]

                    applied_rules.append(rule)
                    applied_rule_ids.append(rule.rule_id)

                    if len(valid_items) < min_items:
                        missing_fields.append("definition_of_done")

        if missing_fields:
            rule_refs = ", ".join([f"#{rid}" for rid in applied_rule_ids])
            return TaskValidationResult(
                is_valid=False,
                status=RunStatus.NEEDS_CLARIFICATION,
                missing_fields=missing_fields,
                applied_rules=applied_rules,
                applied_rule_ids=applied_rule_ids,
                message=(
                    f"Task creation blocked: Company instruction {rule_refs} requires at least "
                    f"1 non-empty Definition of Done item. Please provide 'definition_of_done'."
                )
            )

        return TaskValidationResult(
            is_valid=True,
            status=RunStatus.CREATED,
            missing_fields=[],
            applied_rules=applied_rules,
            applied_rule_ids=applied_rule_ids,
            message="Task readiness validation passed."
        )
