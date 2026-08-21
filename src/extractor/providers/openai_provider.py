import json
import logging
import re
from typing import Optional
from src.config import OPENAI_API_KEY, OPENAI_MODEL_ID
from src.models.schemas import ExtractedPayload
from src.models.enums import ExtractionMode
from src.extractor.prompt import SYSTEM_EXTRACTION_PROMPT
from src.extractor.providers.base import BaseLLMProvider

logger = logging.getLogger(__name__)

class OpenAIProvider(BaseLLMProvider):
    """
    Direct OpenAI API Provider for high-throughput rule extraction (e.g. gpt-4o-mini, gpt-4o).
    """
    def __init__(
        self,
        api_key: Optional[str] = None,
        model_id: Optional[str] = None
    ):
        self.api_key = api_key or OPENAI_API_KEY
        self.model_id = model_id or OPENAI_MODEL_ID
        self._client = None

    def _get_client(self):
        if self._client is None:
            if not self.api_key:
                raise ValueError("OpenAI API key is not configured. Set OPENAI_API_KEY in .env.")
            try:
                from openai import OpenAI
                self._client = OpenAI(api_key=self.api_key)
            except ImportError:
                raise ImportError("OpenAI package not installed. Run: pip install openai>=1.0.0")
        return self._client

    def _clean_json_response(self, text: str) -> str:
        text = text.strip()
        match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
        if match:
            return match.group(1).strip()
        return text

    def extract(self, prompt: str, interaction_text: str) -> ExtractedPayload:
        """
        Executes Chat Completion via OpenAI API with JSON output mode.
        """
        client = self._get_client()
        logger.info(f"Invoking OpenAI model '{self.model_id}'...")

        response = client.chat.completions.create(
            model=self.model_id,
            messages=[
                {"role": "system", "content": SYSTEM_EXTRACTION_PROMPT},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.0
        )

        raw_text = response.choices[0].message.content
        cleaned_json = self._clean_json_response(raw_text)
        parsed_dict = json.loads(cleaned_json)

        payload = ExtractedPayload(**parsed_dict)
        payload.extraction_mode = ExtractionMode.OPENAI_LLM
        return payload
