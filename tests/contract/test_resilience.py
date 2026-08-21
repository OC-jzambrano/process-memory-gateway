from unittest.mock import patch
from src.extractor.service import ProcessMemoryExtractorService
from src.models.enums import RuleType, ExtractionMode, LLMProviderType
from src.models.schemas import ExtractedPayload, ExtractedRuleItem

def test_cascade_falls_back_to_local_when_all_fail():
    """When both OpenAI and Bedrock fail, local fallback is returned."""
    svc = ProcessMemoryExtractorService(provider=LLMProviderType.AUTO, openai_api_key="sk-mock-key")

    with patch.object(svc.openai_provider, 'extract', side_effect=Exception("OpenAI quota exceeded")):
        with patch.object(svc.bedrock_provider, 'extract', side_effect=Exception("Bedrock throttled")):
            result = svc._invoke_cascade("Prompt", "Manufacturing requires approval from the lead.")

    assert len(result.rules) >= 1
    assert result.extraction_mode == ExtractionMode.LOCAL_FALLBACK
    assert any(r.rule_type == RuleType.APPROVAL_POLICY for r in result.rules)

def test_openai_success_stops_cascade():
    """When OpenAI succeeds, it returns immediately without contacting Bedrock."""
    svc = ProcessMemoryExtractorService(provider=LLMProviderType.OPENAI, openai_api_key="sk-test-mock-key")

    mock_payload = ExtractedPayload(
        rules=[
            ExtractedRuleItem(
                rule_text="OpenAI extracted rule.",
                rule_type=RuleType.NAMING_CONVENTION,
                source_quote="BOMs must include version numbers",
                confidence=0.98
            )
        ],
        extraction_mode=ExtractionMode.OPENAI_LLM
    )

    with patch.object(svc.openai_provider, 'extract', return_value=mock_payload) as mock_openai:
        with patch.object(svc.bedrock_provider, 'extract') as mock_bedrock:
            result = svc._invoke_cascade("Prompt", "BOMs must include version numbers.")

    assert mock_openai.call_count == 1
    assert mock_bedrock.call_count == 0
    assert result.extraction_mode == ExtractionMode.OPENAI_LLM
    assert result.rules[0].rule_text == "OpenAI extracted rule."

def test_bedrock_fallback_when_openai_fails():
    """When OpenAI fails in cascade, Bedrock is called and succeeds."""
    svc = ProcessMemoryExtractorService(provider=LLMProviderType.OPENAI, openai_api_key="sk-test-mock-key")

    mock_bedrock_payload = ExtractedPayload(
        rules=[
            ExtractedRuleItem(
                rule_text="Bedrock extracted rule.",
                rule_type=RuleType.DATA_VALIDATION,
                source_quote="SKU uniqueness is required",
                confidence=0.96
            )
        ],
        extraction_mode=ExtractionMode.BEDROCK_LLM
    )

    with patch.object(svc.openai_provider, 'extract', side_effect=Exception("OpenAI rate limit")):
        with patch.object(svc.bedrock_provider, 'extract', return_value=mock_bedrock_payload) as mock_bedrock:
            result = svc._invoke_cascade("Prompt", "SKU uniqueness is required.")

    assert mock_bedrock.call_count == 1
    assert result.extraction_mode == ExtractionMode.BEDROCK_LLM
    assert result.rules[0].rule_text == "Bedrock extracted rule."
