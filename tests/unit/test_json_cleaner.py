from src.extractor.service import BedrockExtractorService

def _make_svc():
    return BedrockExtractorService.__new__(BedrockExtractorService)

def test_strips_markdown_json_fence():
    svc = _make_svc()
    assert svc._clean_json_response('```json\n{"rules": []}\n```') == '{"rules": []}'

def test_strips_plain_fence():
    svc = _make_svc()
    assert svc._clean_json_response('```\n{"rules": []}\n```') == '{"rules": []}'

def test_passes_raw_json_through():
    svc = _make_svc()
    raw = '{"rules": [{"rule_text": "test"}]}'
    assert svc._clean_json_response(raw) == raw

def test_strips_surrounding_whitespace():
    svc = _make_svc()
    assert svc._clean_json_response('  \n  {"rules": []}  \n  ') == '{"rules": []}'

def test_handles_multiline_json_inside_fences():
    svc = _make_svc()
    raw = """```json
{
  "rules": [
    {
      "rule_text": "Rule 1",
      "rule_type": "approval_policy"
    }
  ]
}
```"""
    cleaned = svc._clean_json_response(raw)
    assert cleaned.startswith("{") and cleaned.endswith("}")
    assert '"rule_text": "Rule 1"' in cleaned
