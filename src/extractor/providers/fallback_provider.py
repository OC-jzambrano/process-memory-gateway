import re
from typing import List
from src.models.schemas import ExtractedPayload, ExtractedRuleItem
from src.models.enums import RuleType, Severity, EnforcementMode, ExtractionMode
from src.extractor.providers.base import BaseLLMProvider

class LocalFallbackProvider(BaseLLMProvider):
    """
    Deterministic rule extraction parser used when cloud LLM providers are unavailable or offline.
    Assigns capped confidence scores (max 0.75) to reflect heuristic nature.
    """
    def __init__(self, default_reason: str = "Deterministic heuristic parser"):
        self.default_reason = default_reason

    def extract(self, prompt: str, interaction_text: str, reason: str = "") -> ExtractedPayload:
        rules: List[ExtractedRuleItem] = []
        sentences = [s.strip() for s in re.split(r'[.\n]', interaction_text) if s.strip()]

        for s in sentences:
            s_lower = s.lower()
            rule_text = f"{s.strip()}." if not s.endswith('.') else s.strip()

            # 1. Approval policies
            if any(k in s_lower for k in ["approval", "approved", "leader", "lead", "sign-off", "permission"]):
                rules.append(ExtractedRuleItem(
                    rule_text=rule_text,
                    rule_type=RuleType.APPROVAL_POLICY,
                    severity=Severity.CRITICAL,
                    enforcement_mode=EnforcementMode.REQUIRES_APPROVAL,
                    source_quote=s,
                    confidence=0.75
                ))
            # 2. Naming conventions
            elif any(k in s_lower for k in ["version", "naming", "prefix", "suffix", "format", "name"]):
                rules.append(ExtractedRuleItem(
                    rule_text=rule_text,
                    rule_type=RuleType.NAMING_CONVENTION,
                    severity=Severity.WARNING,
                    enforcement_mode=EnforcementMode.ADVISORY,
                    source_quote=s,
                    confidence=0.72
                ))
            # 3. Data validation / duplicate rules
            elif any(k in s_lower for k in ["duplicate", "sku", "unique", "validate", "validation", "exist"]):
                rules.append(ExtractedRuleItem(
                    rule_text=rule_text,
                    rule_type=RuleType.DATA_VALIDATION,
                    severity=Severity.CRITICAL,
                    enforcement_mode=EnforcementMode.BLOCKING,
                    source_quote=s,
                    confidence=0.74
                ))
            # 4. General operational constraints
            elif any(k in s_lower for k in ["must", "only", "never", "cannot", "do not", "required"]):
                rules.append(ExtractedRuleItem(
                    rule_text=rule_text,
                    rule_type=RuleType.OPERATIONAL_CONSTRAINT,
                    severity=Severity.WARNING,
                    enforcement_mode=EnforcementMode.BLOCKING,
                    source_quote=s,
                    confidence=0.70
                ))

        effective_reason = reason or self.default_reason
        return ExtractedPayload(
            rules=rules,
            reasoning=f"Extracted via heuristic parser (mode: fallback - {effective_reason})",
            extraction_mode=ExtractionMode.LOCAL_FALLBACK,
            error_detail=effective_reason
        )
