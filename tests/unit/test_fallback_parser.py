from src.extractor.service import BedrockExtractorService
from src.models.enums import RuleType, Severity, EnforcementMode

def _parse(text):
    svc = BedrockExtractorService.__new__(BedrockExtractorService)
    return svc._fallback_local_extraction(text)

def test_detects_approval_policy():
    result = _parse("Manufacturing requires approval from the team leader.")
    assert any(r.rule_type == RuleType.APPROVAL_POLICY for r in result.rules)
    assert any(r.enforcement_mode == EnforcementMode.REQUIRES_APPROVAL for r in result.rules)

def test_detects_naming_convention():
    result = _parse("BOMs must include version numbers in the name.")
    assert any(r.rule_type == RuleType.NAMING_CONVENTION for r in result.rules)

def test_detects_data_validation():
    result = _parse("Do not create duplicate components if the SKU already exists.")
    assert any(r.rule_type == RuleType.DATA_VALIDATION for r in result.rules)
    assert any(r.severity == Severity.CRITICAL for r in result.rules)

def test_detects_operational_constraint():
    result = _parse("Users must only execute migration during weekend maintenance windows.")
    assert any(r.rule_type == RuleType.OPERATIONAL_CONSTRAINT for r in result.rules)

def test_chitchat_returns_empty():
    result = _parse("Hi, how are you? The weather is nice today.")
    assert len(result.rules) == 0

def test_confidence_always_valid():
    result = _parse("Every purchase order must be approved by the director.")
    assert len(result.rules) > 0
    for r in result.rules:
        assert 0.0 <= r.confidence <= 1.0
