import json
import logging
import re
from typing import Optional, List
import boto3

from src.config import (
    AWS_REGION,
    AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY,
    BEDROCK_MODEL_ID,
    FALLBACK_MODEL_IDS
)
from src.models.schemas import ExtractedPayload
from src.models.enums import ExtractionMode
from src.extractor.prompt import SYSTEM_EXTRACTION_PROMPT
from src.extractor.providers.base import BaseLLMProvider

logger = logging.getLogger(__name__)

class BedrockProvider(BaseLLMProvider):
    """
    AWS Bedrock Provider executing Anthropic Claude & Amazon Nova models with fallback cascade.
    """
    def __init__(
        self,
        region_name: str = AWS_REGION,
        model_id: Optional[str] = None,
        fallback_models: Optional[List[str]] = None
    ):
        self.region_name = region_name
        self.model_id = model_id or BEDROCK_MODEL_ID
        self.fallback_models = fallback_models or FALLBACK_MODEL_IDS
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

    def extract(self, prompt: str, interaction_text: str) -> ExtractedPayload:
        """
        Attempts model invocation across the configured Bedrock model chain.
        """
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
                last_error = e
                logger.warning(f"Bedrock model '{m_id}' failed: {e}. Trying next model in chain.")
                continue

        raise RuntimeError(f"All Bedrock models in cascade failed. Last error: {last_error}")
