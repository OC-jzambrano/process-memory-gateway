from unittest.mock import patch, MagicMock
import json
from src.extractor.service import BedrockExtractorService
from src.models.enums import RuleType

def test_fallback_activates_when_all_models_fail():
    """When all Bedrock models raise exceptions, the local parser must kick in."""
    svc = BedrockExtractorService()

    mock_client = MagicMock()
    mock_client.invoke_model.side_effect = Exception("ThrottlingException: Too many tokens per day")

    with patch.object(svc, '_get_client', return_value=mock_client):
        from src.extractor.prompt import build_user_prompt
        prompt = build_user_prompt("Manufacturing requires approval from the lead.", "c1")
        result = svc._invoke_bedrock(prompt, "Manufacturing requires approval from the lead.")

    assert len(result.rules) >= 1
    assert any(r.rule_type == RuleType.APPROVAL_POLICY for r in result.rules)

def test_first_successful_model_stops_chain():
    """The fallback chain must stop at the first model that succeeds."""
    svc = BedrockExtractorService()

    payload_json = json.dumps({
        "rules": [
            {
                "rule_text": "Sample cloud extracted rule.",
                "rule_type": "approval_policy",
                "severity": "critical",
                "enforcement_mode": "blocking",
                "source_quote": "Sample",
                "confidence": 0.99
            }
        ],
        "reasoning": "Extracted via mocked model."
    })

    mock_body = MagicMock()
    mock_body.read.return_value = json.dumps({
        'content': [{'text': payload_json}]
    }).encode('utf-8')

    success_response = {'body': mock_body}
    mock_client = MagicMock()
    mock_client.invoke_model.side_effect = [
        Exception("Model 1 not available"),
        success_response
    ]

    with patch.object(svc, '_get_client', return_value=mock_client):
        from src.extractor.prompt import build_user_prompt
        prompt = build_user_prompt("Dialogue text", "c1")
        result = svc._invoke_bedrock(prompt, "Dialogue text")

    assert mock_client.invoke_model.call_count == 2
    assert len(result.rules) == 1
    assert result.rules[0].rule_text == "Sample cloud extracted rule."
