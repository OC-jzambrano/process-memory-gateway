import pytest
from src.extractor.service import BedrockExtractorService
from src.models.enums import RuleType

@pytest.mark.ai
def test_benchmark_f1_score():
    svc = BedrockExtractorService()
    dialogue = (
        "In this company, Manufacturing is only installed with approval from the Operations Lead. "
        "BOMs must include version numbers. Do not create duplicate components if the SKU already exists."
    )
    result = svc.extract_from_text(dialogue, "bench_client")
    expected = {RuleType.APPROVAL_POLICY, RuleType.NAMING_CONVENTION, RuleType.DATA_VALIDATION}
    extracted = {c.rule_type for c in result.candidates}

    tp = len(expected & extracted)
    precision = tp / len(extracted) if extracted else 0
    recall = tp / len(expected)
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0

    assert f1 >= 0.80, f"F1 = {f1:.2f}, below threshold 0.80"
