import json
from pathlib import Path
from src.extractor.service import BedrockExtractorService

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"

def test_manufacturing_golden_output():
    dialogue = (FIXTURES / "dialogues" / "manufacturing_setup.txt").read_text(encoding="utf-8")
    svc = BedrockExtractorService.__new__(BedrockExtractorService)
    result = svc._fallback_local_extraction(dialogue)

    expected = json.loads((FIXTURES / "golden" / "manufacturing_expected.json").read_text(encoding="utf-8"))
    actual_types = sorted([r.rule_type.value for r in result.rules])
    expected_types = sorted([r["rule_type"] for r in expected["rules"]])

    assert actual_types == expected_types, f"Golden mismatch: {actual_types} != {expected_types}"

def test_accounting_golden_output():
    dialogue = (FIXTURES / "dialogues" / "accounting_policies.txt").read_text(encoding="utf-8")
    svc = BedrockExtractorService.__new__(BedrockExtractorService)
    result = svc._fallback_local_extraction(dialogue)

    expected = json.loads((FIXTURES / "golden" / "accounting_expected.json").read_text(encoding="utf-8"))
    actual_types = sorted([r.rule_type.value for r in result.rules])
    expected_types = sorted([r["rule_type"] for r in expected["rules"]])

    assert actual_types == expected_types, f"Golden mismatch: {actual_types} != {expected_types}"
