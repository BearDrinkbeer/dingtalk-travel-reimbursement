from __future__ import annotations

import hashlib
import hmac
import secrets


def random_token() -> str:
    """Return at least 256 bits of URL-safe entropy."""

    return secrets.token_urlsafe(32)


def token_hash(token: str, secret: str) -> str:
    key = secret.encode("utf-8") if secret else b"development-only-session-key"
    return hmac.new(key, token.encode("utf-8"), hashlib.sha256).hexdigest()


def tokens_match(candidate: str, expected_hash: str, secret: str) -> bool:
    return hmac.compare_digest(token_hash(candidate, secret), expected_hash)
