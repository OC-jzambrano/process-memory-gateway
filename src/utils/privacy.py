import re
from typing import Tuple, Any, Optional, Set, Dict, List

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


SENSITIVE_KEY_PATTERNS = {
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "secret_arn",
    "credential",
    "private_key",
    "access_key",
    "authorization"
}

def sanitize_evidence(data: Any, known_secrets: Optional[Set[str]] = None) -> Any:
    """
    Recursively sanitizes evidence data prior to persistence or logging.
    - Replaces values for keys matching sensitive words (password, api_key, secret, etc.)
    - Replaces occurrences of known secrets in strings
    - Redacts regex patterns (emails, credit cards, api keys)
    """
    if data is None:
        return None

    # Handle Pydantic models
    if hasattr(data, "model_dump"):
        data = data.model_dump()
    elif hasattr(data, "dict"):
        data = data.dict()

    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            if any(p in k_lower for p in SENSITIVE_KEY_PATTERNS):
                sanitized[k] = "[REDACTED_SECRET]"
            else:
                sanitized[k] = sanitize_evidence(v, known_secrets=known_secrets)
        return sanitized

    if isinstance(data, (list, tuple)):
        return [sanitize_evidence(item, known_secrets=known_secrets) for item in data]

    if isinstance(data, set):
        return {sanitize_evidence(item, known_secrets=known_secrets) for item in data}

    if isinstance(data, str):
        text = data
        if known_secrets:
            # Sort known secrets by length descending to match longest substrings first
            valid_secrets = sorted([s for s in known_secrets if s and len(s) >= 2], key=len, reverse=True)
            for s in valid_secrets:
                text = text.replace(s, "[REDACTED_KNOWN_SECRET]")
        redacted, _ = redact_sensitive_text(text)
        return redacted

    return data
