from src.models.schemas import CanonicalRule


def calculate_specificity_score(rule: CanonicalRule) -> int:
    """
    Computes a deterministic specificity score for a canonical rule:
    fields: +16
    operation: +8
    resource: +4
    application: +2
    system: +1
    """
    score = 0
    scope = rule.structured_scope
    if scope:
        if scope.fields and len(scope.fields) > 0:
            score += 16
        if scope.operation:
            score += 8
        if scope.resource:
            score += 4
        if scope.application:
            score += 2
        if scope.system:
            score += 1
    return score


def matches_scope(
    rule: CanonicalRule,
    system: str = "odoo",
    application: str | None = None,
    resource: str | None = None,
    operation: str | None = None,
    fields: list[str] | None = None,
    process_name: str | None = None,
) -> bool:
    """
    Determines whether a canonical rule applies to a given scope context.

    Matching rules:
    - Explicit system/application/resource/operation mismatches exclude rules completely.
    - Unspecified rule dimensions allow broader applicability.
    - If a rule specifies a dimension, but the query lacks that dimension, the rule is excluded.
    - Field-scoped rules require at least one overlapping field when a field filter is supplied.
    - Process name mismatches (e.g. rule has process_name='sales' and target is 'project') exclude the rule.
    """
    # 1. Process name check
    target_process = application or process_name
    if (
        rule.process_name
        and rule.process_name.lower() not in ("general", "default")
        and target_process
        and rule.process_name.lower() != target_process.lower()
    ):
        return False

    scope = rule.structured_scope
    if not scope:
        # Unstructured / company-wide general rule
        return True

    # 2. System check
    if scope.system and (not system or scope.system.lower() != system.lower()):
        return False

    # 3. Application check
    if scope.application:
        if not application:
            return False
        if scope.application.lower() != application.lower():
            return False

    # 4. Resource check
    if scope.resource:
        if not resource:
            return False
        if scope.resource.lower() != resource.lower():
            return False

    # 5. Operation check
    if scope.operation:
        if not operation:
            return False
        if scope.operation.lower() != operation.lower():
            return False

    # 6. Fields check: if filter supplied and rule has fields, must overlap
    if scope.fields and len(scope.fields) > 0 and fields and len(fields) > 0:
        rule_field_set = {f.lower().strip() for f in scope.fields}
        query_field_set = {f.lower().strip() for f in fields}
        if not (rule_field_set & query_field_set):
            return False

    return True


def filter_and_order_rules(
    rules: list[CanonicalRule],
    system: str = "odoo",
    application: str | None = None,
    resource: str | None = None,
    operation: str | None = None,
    fields: list[str] | None = None,
    process_name: str | None = None,
) -> list[CanonicalRule]:
    """
    Filters rules by scope and orders them deterministically:
    1. Specificity score DESC
    2. Version DESC
    3. Rule ID ASC
    """
    matching = [
        r
        for r in rules
        if matches_scope(
            r,
            system=system,
            application=application,
            resource=resource,
            operation=operation,
            fields=fields,
            process_name=process_name,
        )
    ]

    # Deduplicate keeping the latest version per rule_id
    latest_by_id = {}
    for r in matching:
        if r.rule_id not in latest_by_id or r.version > latest_by_id[r.rule_id].version:
            latest_by_id[r.rule_id] = r

    deduped = list(latest_by_id.values())

    # Sort: specificity DESC, version DESC, rule_id ASC
    deduped.sort(key=lambda r: (-calculate_specificity_score(r), -r.version, r.rule_id))

    return deduped
