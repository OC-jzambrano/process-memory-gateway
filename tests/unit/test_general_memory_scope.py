from src.extractor.service import ProcessMemoryExtractorService
from src.models.enums import RuleType
from src.models.schemas import ActionContext, ExtractedPayload, ExtractedRuleItem


def test_unspecified_scope_does_not_invent_business_context():
    assert ActionContext().model_dump() == {
        "system": None, "application": None, "resource": None,
        "operation": None, "fields": [],
    }
    assert ActionContext(system="crm", application="contacts").resource is None


def test_extraction_preserves_model_inferred_scope(monkeypatch):
    service = ProcessMemoryExtractorService(offline_mode=True)
    text = "Always include the account reference when updating a contact."
    scope = ActionContext(system="crm", application="contacts", operation="update")
    payload = ExtractedPayload(rules=[ExtractedRuleItem(
        rule_text=text, source_quote=text, confidence=0.9,
        rule_type=RuleType.OPERATIONAL_CONSTRAINT, structured_scope=scope,
    )])
    monkeypatch.setattr(service, "_invoke_cascade", lambda *args: payload)
    result = service.extract_candidates(text, client_id="company")
    assert result.candidates[0].structured_scope == scope
