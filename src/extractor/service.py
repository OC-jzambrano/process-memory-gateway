import uuid
import re
import logging
from typing import Optional, List, Union

from src.config import (
    LLM_PROVIDER,
    OPENAI_API_KEY,
    OPENAI_MODEL_ID,
    AWS_REGION,
    BEDROCK_MODEL_ID,
    FALLBACK_MODEL_IDS
)
from src.models.schemas import (
    CandidateRule,
    ExtractedPayload,
    ExtractionResult
)
from src.models.enums import RuleStatus, SourceType, ExtractionMode, LLMProviderType
from src.extractor.prompt import build_user_prompt
from src.extractor.providers import (
    OpenAIProvider,
    BedrockProvider,
    LocalFallbackProvider
)

logger = logging.getLogger(__name__)

class ProcessMemoryExtractorService:
    """
    Multi-Provider LLM Extraction Service.
    Supports OpenAI, AWS Bedrock, and Local Fallback with configurable cascade strategies.
    """
    def __init__(
        self,
        provider: Optional[Union[str, LLMProviderType]] = None,
        offline_mode: bool = False,
        openai_api_key: Optional[str] = None,
        openai_model_id: Optional[str] = None,
        bedrock_region: Optional[str] = None,
        bedrock_model_id: Optional[str] = None,
        # Backward-compatible kwargs
        region_name: Optional[str] = None,
        model_id: Optional[str] = None
    ):
        if isinstance(provider, LLMProviderType):
            self.provider_type = provider
        elif provider:
            self.provider_type = LLMProviderType(str(provider).lower())
        else:
            self.provider_type = LLMProviderType(str(LLM_PROVIDER).lower())

        self.offline_mode = offline_mode

        # OpenAI settings
        self.openai_api_key = openai_api_key or OPENAI_API_KEY
        self.openai_model_id = openai_model_id or OPENAI_MODEL_ID

        # Bedrock settings
        self.bedrock_region = bedrock_region or region_name or AWS_REGION
        self.bedrock_model_id = bedrock_model_id or model_id or BEDROCK_MODEL_ID
        self.model_id = self.bedrock_model_id

        # Providers
        self.fallback_provider = LocalFallbackProvider()
        self.openai_provider = OpenAIProvider(api_key=self.openai_api_key, model_id=self.openai_model_id)
        self.bedrock_provider = BedrockProvider(
            region_name=self.bedrock_region,
            model_id=self.bedrock_model_id,
            fallback_models=FALLBACK_MODEL_IDS
        )

    @staticmethod
    def _clean_json_response(text: str) -> str:
        """Strips markdown fences from JSON output."""
        text = text.strip()
        match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
        if match:
            return match.group(1).strip()
        return text

    @staticmethod
    def _fallback_local_extraction(text: str, reason: str = "Fallback heuristic parser") -> ExtractedPayload:
        """Deterministic keyword parser."""
        return LocalFallbackProvider().extract("", text, reason=reason)

    def _validate_provenance(self, source_quote: str, interaction_text: str) -> bool:
        """
        Verifies that source_quote is a genuine substring of interaction_text
        using normalized whitespace comparison.
        """
        if not source_quote or not interaction_text:
            return False
        clean_quote = " ".join(source_quote.strip().split()).lower()
        clean_text = " ".join(interaction_text.strip().split()).lower()
        return clean_quote in clean_text

    def _invoke_cascade(self, prompt: str, interaction_text: str) -> ExtractedPayload:
        """
        Executes extraction cascade according to configured provider strategy:
        - "openai":  OpenAI -> Bedrock -> Local Fallback
        - "bedrock": Bedrock -> OpenAI -> Local Fallback
        - "auto":    OpenAI (if key) -> Bedrock (if key) -> Local Fallback
        - "local":   Local Fallback
        """
        if self.offline_mode or self.provider_type == LLMProviderType.LOCAL:
            return self.fallback_provider.extract(prompt, interaction_text, reason="Configured offline/local mode")

        cascade_errors = []

        # 1. Primary: OpenAI
        if self.provider_type in (LLMProviderType.OPENAI, LLMProviderType.AUTO):
            if self.openai_api_key:
                try:
                    return self.openai_provider.extract(prompt, interaction_text)
                except Exception as e:
                    logger.warning(f"OpenAI extraction failed: {e}. Falling back to next provider in cascade.")
                    cascade_errors.append(f"OpenAI: {e}")

        # 2. Secondary / Primary: Bedrock
        if self.provider_type in (LLMProviderType.BEDROCK, LLMProviderType.OPENAI, LLMProviderType.AUTO):
            try:
                return self.bedrock_provider.extract(prompt, interaction_text)
            except Exception as e:
                logger.warning(f"Bedrock extraction failed: {e}. Falling back to next provider in cascade.")
                cascade_errors.append(f"Bedrock: {e}")

        # 3. Tertiary: Try OpenAI if Bedrock was primary and failed
        if self.provider_type == LLMProviderType.BEDROCK and self.openai_api_key:
            try:
                return self.openai_provider.extract(prompt, interaction_text)
            except Exception as e:
                logger.warning(f"Secondary OpenAI extraction failed: {e}.")
                cascade_errors.append(f"OpenAI fallback: {e}")

        # 4. Final Deterministic Fallback
        reason_str = "; ".join(cascade_errors) if cascade_errors else "All cloud providers bypassed"
        logger.info(f"Activating deterministic local fallback. Reason: {reason_str}")
        return self.fallback_provider.extract(prompt, interaction_text, reason=reason_str)

    def extract_from_text(
        self,
        interaction_text: str,
        client_id: str,
        process_name: str = "general",
        source_type: SourceType = SourceType.USER_INTERACTION
    ) -> ExtractionResult:
        """
        Extracts candidate business rules from conversational text with provenance verification.
        Returns ExtractionResult with full UUIDs, verified quotes, and explicit extraction mode.
        """
        if not interaction_text or not interaction_text.strip():
            session_id = f"sess_{uuid.uuid4().hex}"
            return ExtractionResult(
                session_id=session_id,
                client_id=client_id,
                process_name=process_name,
                candidates=[],
                extraction_mode=ExtractionMode.LOCAL_FALLBACK,
                error_detail="Empty input text"
            )

        session_id = f"sess_{uuid.uuid4().hex}"
        user_prompt = build_user_prompt(interaction_text, client_id, process_name)

        extracted_payload = self._invoke_cascade(user_prompt, interaction_text)

        candidates: List[CandidateRule] = []
        for item in extracted_payload.rules:
            # Provenance Verbatim Substring Validation
            if not self._validate_provenance(item.source_quote, interaction_text):
                logger.warning(f"Discarding candidate with unverified provenance quote: '{item.source_quote}'")
                continue

            cand_id = f"cand_{uuid.uuid4().hex}"
            candidate = CandidateRule(
                candidate_id=cand_id,
                session_id=session_id,
                client_id=client_id,
                process_name=process_name,
                rule_text=item.rule_text,
                rule_type=item.rule_type,
                severity=item.severity,
                enforcement_mode=item.enforcement_mode,
                source_quote=item.source_quote,
                confidence=round(item.confidence, 3),
                status=RuleStatus.PENDING_REVIEW
            )
            candidates.append(candidate)

        return ExtractionResult(
            session_id=session_id,
            client_id=client_id,
            process_name=process_name,
            candidates=candidates,
            extraction_mode=extracted_payload.extraction_mode,
            error_detail=extracted_payload.error_detail,
            raw_payload=extracted_payload
        )

    def extract_candidates(self, *args, **kwargs) -> ExtractionResult:
        """Alias for extract_from_text."""
        return self.extract_from_text(*args, **kwargs)

# Backward Compatibility Alias
BedrockExtractorService = ProcessMemoryExtractorService
