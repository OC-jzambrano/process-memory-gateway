import re
from typing import Tuple

# Common regex patterns for PII and sensitive data
EMAIL_PATTERN = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
CREDIT_CARD_PATTERN = re.compile(r'\b(?:\d{4}[-\s]?){3}\d{4}\b')
API_KEY_PATTERN = re.compile(r'\b(?:AKIA[0-9A-Z]{16}|[0-9a-zA-Z_-]{32,64})\b')
PHONE_PATTERN = re.compile(r'\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b')

def redact_sensitive_text(text: str) -> Tuple[str, int]:
    """
    Redacts sensitive PII (emails, cards, keys, phones) from text prior to cloud transmission.
    Returns (redacted_text, count_of_redactions).
    """
    if not text:
        return "", 0

    redactions = 0

    def _replace_email(m):
        nonlocal redactions
        redactions += 1
        return "[REDACTED_EMAIL]"

    def _replace_card(m):
        nonlocal redactions
        redactions += 1
        return "[REDACTED_CARD]"

    def _replace_key(m):
        nonlocal redactions
        redactions += 1
        return "[REDACTED_SECRET_KEY]"

    redacted = EMAIL_PATTERN.sub(_replace_email, text)
    redacted = CREDIT_CARD_PATTERN.sub(_replace_card, redacted)
    redacted = API_KEY_PATTERN.sub(_replace_key, redacted)

    return redacted, redactions
