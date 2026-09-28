import re

from src.extractor.providers.base import BaseLLMProvider
from src.models.enums import EnforcementMode, ExtractionMode, RuleType, Severity
from src.models.schemas import ExtractedPayload, ExtractedRuleItem


class LocalFallbackProvider(BaseLLMProvider):
    """
    Deterministic rule extraction parser used when cloud LLM providers are unavailable or offline.
    Supports English, Spanish, and multilingual business dialogue.
    Assigns capped confidence scores (max 0.75) to reflect heuristic nature.
    """

    def __init__(self, default_reason: str = "Deterministic heuristic parser"):
        self.default_reason = default_reason

    @staticmethod
    def _split_compound_requirement(sentence: str) -> list[tuple[str, str]]:
        """Split coordinated, independently enforceable predicates when clear.

        The source quotes remain verbatim substrings, while the second normalized
        rule inherits the subject and modal verb so it is independently readable.
        """
        pattern = re.compile(
            r"^(?P<prefix>.+?\b(?:must|should|debe|deben|tiene que|tienen que)\b)"
            r"\s+(?P<first>.+?)(?:,?\s+)(?:and|y)\s+"
            r"(?P<second>(?:be|estar|ser|remain|permanecer|redactarse|incluir)\b.+)$",
            re.IGNORECASE,
        )
        match = pattern.match(sentence.strip())
        if not match:
            return [(sentence, sentence)]

        prefix = match.group("prefix").strip()
        first_source = f"{prefix} {match.group('first').strip()}"
        second_source = match.group("second").strip()
        return [
            (first_source, first_source),
            (second_source, f"{prefix} {second_source}"),
        ]

    def extract(
        self, prompt: str, interaction_text: str, reason: str = ""
    ) -> ExtractedPayload:
        rules: list[ExtractedRuleItem] = []
        sentences = [
            s.strip() for s in re.split(r"[.\n]", interaction_text) if s.strip()
        ]

        for sentence in sentences:
            for s, rule_text in self._split_compound_requirement(sentence):
                s_lower = rule_text.lower()
                rule_text = (
                    f"{rule_text.strip()}."
                    if not rule_text.endswith(".")
                    else rule_text.strip()
                )

                # 1. Approval policies (English & Spanish)
                if any(
                    k in s_lower
                    for k in [
                        "approval",
                        "approved",
                        "leader",
                        "lead",
                        "sign-off",
                        "permission",
                        "aprobacion",
                        "aprobación",
                        "aprobado",
                        "lider",
                        "líder",
                        "autorizacion",
                        "autorización",
                        "permiso",
                    ]
                ):
                    rules.append(
                        ExtractedRuleItem(
                            rule_text=rule_text,
                            rule_type=RuleType.APPROVAL_POLICY,
                            severity=Severity.CRITICAL,
                            enforcement_mode=EnforcementMode.REQUIRES_APPROVAL,
                            source_quote=s,
                            confidence=0.75,
                        )
                    )
                # 2. Naming conventions (English & Spanish)
                elif any(
                    k in s_lower
                    for k in [
                        "version",
                        "versión",
                        "naming",
                        "prefix",
                        "suffix",
                        "format",
                        "name",
                        "nomenclatura",
                        "formato",
                        "prefijo",
                        "sufijo",
                        "nombre",
                        "codigo",
                        "código",
                    ]
                ):
                    rules.append(
                        ExtractedRuleItem(
                            rule_text=rule_text,
                            rule_type=RuleType.NAMING_CONVENTION,
                            severity=Severity.WARNING,
                            enforcement_mode=EnforcementMode.ADVISORY,
                            source_quote=s,
                            confidence=0.72,
                        )
                    )
                # 3. Data validation / duplicate rules (English & Spanish)
                elif any(
                    k in s_lower
                    for k in [
                        "duplicate",
                        "duplicado",
                        "sku",
                        "unique",
                        "único",
                        "validate",
                        "validar",
                        "validation",
                        "exist",
                        "existe",
                    ]
                ):
                    rules.append(
                        ExtractedRuleItem(
                            rule_text=rule_text,
                            rule_type=RuleType.DATA_VALIDATION,
                            severity=Severity.CRITICAL,
                            enforcement_mode=EnforcementMode.BLOCKING,
                            source_quote=s,
                            confidence=0.74,
                        )
                    )
                # 4. General operational constraints (English & Spanish)
                elif any(
                    k in s_lower
                    for k in [
                        "must",
                        "only",
                        "never",
                        "cannot",
                        "do not",
                        "required",
                        "debe",
                        "solo",
                        "solamente",
                        "nunca",
                        "jamas",
                        "jamás",
                        "no se puede",
                        "obligatorio",
                        "requerido",
                    ]
                ):
                    rules.append(
                        ExtractedRuleItem(
                            rule_text=rule_text,
                            rule_type=RuleType.OPERATIONAL_CONSTRAINT,
                            severity=Severity.WARNING,
                            enforcement_mode=EnforcementMode.BLOCKING,
                            source_quote=s,
                            confidence=0.70,
                        )
                    )

        effective_reason = reason or self.default_reason
        return ExtractedPayload(
            rules=rules,
            reasoning=f"Extracted via heuristic parser (mode: fallback - {effective_reason})",
            extraction_mode=ExtractionMode.LOCAL_FALLBACK,
            error_detail=effective_reason,
        )
