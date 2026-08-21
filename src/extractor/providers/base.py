from abc import ABC, abstractmethod
from src.models.schemas import ExtractedPayload

class BaseLLMProvider(ABC):
    """
    Abstract Base Class for LLM Extraction Providers.
    Defines the extraction interface for OpenAI, AWS Bedrock, and Local Fallback.
    """
    @abstractmethod
    def extract(self, prompt: str, interaction_text: str) -> ExtractedPayload:
        """
        Extracts candidate business rules from conversational prompt and interaction text.
        Returns an ExtractedPayload containing inferred rules and extraction metadata.
        """
        pass
