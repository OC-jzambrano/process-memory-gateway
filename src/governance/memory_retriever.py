from typing import Optional, List
from src.storage.base_repository import BaseRepository
from src.models.schemas import MemoryPack, MemoryPackRuleItem, CanonicalRule
from src.governance.scope_matcher import filter_and_order_rules

class MemoryRetriever:
    """
    Implements compact, scoped memory retrieval ordered by specificity:
    1. Active rules for the authenticated company.
    2. Shared scope matching across system, application, resource, operation, fields.
    3. Deterministically ordered by specificity score, version, rule_id.
    4. Compact result capped within configured token budget (oversized rules omitted).
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

        # 2. Filter and order using deterministic scope matcher
        ordered_rules = filter_and_order_rules(
            rules=all_rules,
            system=system,
            application=application,
            resource=resource,
            operation=operation,
            fields=fields
        )

        final_rule_items: List[MemoryPackRuleItem] = []
        approx_tokens = 0
        omitted_count = 0

        for r in ordered_rules:
            rule_tokens = len(r.rule_text) // 4 + 20
            # Strict token budget: do NOT include oversized rules, even if first rule
            if approx_tokens + rule_tokens > token_budget:
                omitted_count += 1
                continue

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

        if not final_rule_items and omitted_count == 0:
            msg = "No active company instructions found for this scope."
        elif omitted_count > 0:
            msg = f"Retrieved {len(final_rule_items)} scoped company instructions ({omitted_count} omitted due to context budget)."
        else:
            msg = f"Retrieved {len(final_rule_items)} scoped company instructions."

        return MemoryPack(
            company_slug=company_slug,
            system=system,
            application=application,
            resource=resource,
            operation=operation,
            rules=final_rule_items,
            token_budget_used=approx_tokens,
            message=msg
        )

