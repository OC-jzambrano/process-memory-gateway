from src.utils.privacy import redact_sensitive_text

def test_redact_email():
    text = "Please send the invoice to accountant@enterprise.com for processing."
    redacted, count = redact_sensitive_text(text)
    assert count == 1
    assert "accountant@enterprise.com" not in redacted
    assert "[REDACTED_EMAIL]" in redacted

def test_redact_credit_card():
    text = "Payment details card: 4532-1488-9234-1234 on file."
    redacted, count = redact_sensitive_text(text)
    assert count == 1
    assert "4532-1488-9234-1234" not in redacted
    assert "[REDACTED_CARD]" in redacted

def test_redact_aws_api_key():
    text = "Use key AKIAIOSFODNN7EXAMPLE for migration script."
    redacted, count = redact_sensitive_text(text)
    assert count == 1
    assert "AKIAIOSFODNN7EXAMPLE" not in redacted
    assert "[REDACTED_SECRET_KEY]" in redacted

def test_clean_text_unchanged():
    text = "Manufacturing module requires operations lead approval."
    redacted, count = redact_sensitive_text(text)
    assert count == 0
    assert redacted == text
