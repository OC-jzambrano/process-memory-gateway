from typing import Optional, List
from src.storage.base_repository import BaseRepository
from src.models.schemas import MemoryPack, MemoryPackRuleItem, ActionContext, CanonicalRule
from src.models.enums import RuleStatus, EnforcementMode

class MemoryRetriever:
    """
    Implements compact, scoped memory retrieval ordered by specificity:
    1. Active rules for the authenticated company.
    2. Exact system + resource + operation matches.
    3. Matching field-scoped rules (e.g. definition_of_done).
    4. Resource-wide, application-wide, and company-wide advisory instructions.
    5. Latest active version only.
    6. Compact result capped within configured token budget.
    """

    def __init__(self, repo: BaseRepository):
        self.repo = repo

    def retrieve_pack(
        self,
        company_id: str,
        company_slug: str,
        system: str = "odoo",
        application: Optional[str] = None,
        resource: Optional[str] = None,
        operation: Optional[str] = None,
        fields: Optional[List[str]] = None,
        token_budget: int = 1500
    ) -> MemoryPack:
        fields = fields or []
        # 1. Fetch all active canonical rules for company
        all_rules = self.repo.get_active_rules(client_id=company_id)
        if not all_rules:
            return MemoryPack(
                company_slug=company_slug,
                system=system,
                application=application,
                resource=resource,
                operation=operation,
                rules=[],
                token_budget_used=0,
                message="No active company instructions found for this scope."
            )

        exact_matches: List[CanonicalRule] = []
        field_matches: List[CanonicalRule] = []
        advisory_matches: List[CanonicalRule] = []

        for rule in all_rules:
            scope = rule.structured_scope
            if not scope:
                # General company-wide rule
                advisory_matches.append(rule)
                continue

            # System check
            if scope.system and scope.system.lower() != system.lower():
                continue

            is_exact = True
            if application and scope.application and scope.application.lower() != application.lower():
                is_exact = False
            if resource and scope.resource and scope.resource.lower() != resource.lower():
                is_exact = False
            if operation and scope.operation and scope.operation.lower() != operation.lower():
                is_exact = False

            if is_exact and (scope.resource or scope.operation):
                # Check field overlap
                if scope.fields and fields:
                    if any(f.lower() in [sf.lower() for sf in scope.fields] for f in fields):
                        field_matches.append(rule)
                    else:
                        exact_matches.append(rule)
                else:
                    exact_matches.append(rule)
            else:
                advisory_matches.append(rule)

        # Prioritized sequence: exact -> field -> advisory
        selected_rules = exact_matches + field_matches + advisory_matches
        
        # Deduplicate while preserving order and latest version
        seen_rule_ids = set()
        final_rule_items: List[MemoryPackRuleItem] = []
        approx_tokens = 0

        for r in selected_rules:
            if r.rule_id in seen_rule_ids:
                continue
            seen_rule_ids.add(r.rule_id)

            # Estimate token weight (~ 1 token per 4 characters)
            rule_tokens = len(r.rule_text) // 4 + 20
            if approx_tokens + rule_tokens > token_budget and len(final_rule_items) > 0:
                break

            approx_tokens += rule_tokens
            final_rule_items.append(
                MemoryPackRuleItem(
                    rule_id=r.rule_id,
                    version=r.version,
                    rule_text=r.rule_text,
                    rule_type=r.rule_type,
                    enforcement_mode=r.enforcement_mode,
                    scope=r.structured_scope,
                    constraint=r.structured_constraint
                )
            )

        return MemoryPack(
            company_slug=company_slug,
            system=system,
            application=application,
            resource=resource,
            operation=operation,
            rules=final_rule_items,
            token_budget_used=approx_tokens,
            message=f"Retrieved {len(final_rule_items)} scoped company instructions."
        )
