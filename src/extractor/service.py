import json
import uuid
import re
import logging
from typing import Optional, List
import boto3

from src.config import (
    AWS_REGION,
    AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY,
    BEDROCK_MODEL_ID,
    FALLBACK_MODEL_IDS
)
from src.models.schemas import (
    CandidateRule,
    ExtractedPayload,
    ExtractedRuleItem,
    ExtractionResult
)
from src.models.enums import RuleStatus, SourceType, RuleType, Severity, EnforcementMode, ExtractionMode
from src.extractor.prompt import SYSTEM_EXTRACTION_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)

class BedrockExtractorService:
    def __init__(
        self,
        region_name: str = AWS_REGION,
        model_id: Optional[str] = None,
        offline_mode: bool = False
    ):
        self.region_name = region_name
        self.model_id = model_id or BEDROCK_MODEL_ID
        self.fallback_models = FALLBACK_MODEL_IDS
        self.offline_mode = offline_mode
        self._client = None

    def _get_client(self):
        if self._client is None:
            self._client = boto3.client(
                'bedrock-runtime',
                region_name=self.region_name,
                aws_access_key_id=AWS_ACCESS_KEY_ID,
                aws_secret_access_key=AWS_SECRET_ACCESS_KEY
            )
        return self._client

    def _clean_json_response(self, text: str) -> str:
        text = text.strip()
        match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
        if match:
            return match.group(1).strip()
        return text

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

    def _fallback_local_extraction(self, text: str, reason: str = "Fallback heuristic parser") -> ExtractedPayload:
        """
        Deterministic rule extraction parser used when Bedrock is awaiting token quota or offline.
        Assigns capped confidence scores (max 0.75) to reflect heuristic nature.
        """
        rules: List[ExtractedRuleItem] = []
        sentences = [s.strip() for s in re.split(r'[.\n]', text) if s.strip()]

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

        return ExtractedPayload(
            rules=rules,
            reasoning=f"Extracted via heuristic parser (mode: fallback - {reason})",
            extraction_mode=ExtractionMode.LOCAL_FALLBACK,
            error_detail=reason
        )

    def _invoke_bedrock(self, prompt: str, interaction_text: str) -> ExtractedPayload:
        """
        Attempts model invocation cascade via configured Bedrock models.
        If offline_mode is True, skips network call and directly uses deterministic fallback.
        """
        if self.offline_mode:
            return self._fallback_local_extraction(interaction_text, reason="Offline mode configured")

        client = self._get_client()
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 2048,
            "temperature": 0.0,
            "system": SYSTEM_EXTRACTION_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        })

        models_to_try = [self.model_id] + [m for m in self.fallback_models if m != self.model_id]
        last_error = None

        for m_id in models_to_try:
            try:
                response = client.invoke_model(
                    modelId=m_id,
                    body=body
                )
                response_body = json.loads(response['body'].read().decode('utf-8'))
                raw_text = response_body['content'][0]['text']
                cleaned_json = self._clean_json_response(raw_text)
                parsed_dict = json.loads(cleaned_json)
                self.model_id = m_id
                
                payload = ExtractedPayload(**parsed_dict)
                payload.extraction_mode = ExtractionMode.BEDROCK_LLM
                return payload
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Bedrock model '{m_id}' failed: {e}. Trying next model in cascade.")
                continue

        # Explicit fallback with logged diagnosis
        logger.info(f"All Bedrock models unavailable. Activating local deterministic fallback. Reason: {last_error}")
        return self._fallback_local_extraction(interaction_text, reason=str(last_error))

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

        extracted_payload = self._invoke_bedrock(user_prompt, interaction_text)

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
