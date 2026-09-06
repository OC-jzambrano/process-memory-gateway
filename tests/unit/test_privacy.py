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


from src.utils.privacy import sanitize_evidence

def test_sanitize_evidence_sensitive_keys():
    data = {
        "title": "Clean Title",
        "api_key": "raw_odoo_key_12345",
        "nested": {
            "password": "super_secret_pw",
            "secret_arn": "arn:aws:secretsmanager:eu-north-1:123456789012:secret:odoo-secret",
            "normal_field": "hello world"
        }
    }
    sanitized = sanitize_evidence(data)
    assert sanitized["title"] == "Clean Title"
    assert sanitized["api_key"] == "[REDACTED_SECRET]"
    assert sanitized["nested"]["password"] == "[REDACTED_SECRET]"
    assert sanitized["nested"]["secret_arn"] == "[REDACTED_SECRET]"
    assert sanitized["nested"]["normal_field"] == "hello world"

def test_sanitize_evidence_known_secrets_and_pii():
    secret_value = "my-super-secret-token"
    data = {
        "description": f"Connecting with {secret_value} and email user@example.com",
        "items": [
            f"Bearer {secret_value}",
            "Valid item text"
        ]
    }
    sanitized = sanitize_evidence(data, known_secrets={secret_value})
    assert secret_value not in str(sanitized)
    assert "[REDACTED_KNOWN_SECRET]" in sanitized["description"]
    assert "[REDACTED_EMAIL]" in sanitized["description"]
    assert "[REDACTED_KNOWN_SECRET]" in sanitized["items"][0]
    assert sanitized["items"][1] == "Valid item text"
