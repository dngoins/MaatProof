"""Canonical serialization, hashing, and HMAC helpers for Proof-of-Deploy."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Mapping


def canonical_dumps(value: Any) -> str:
    """Return deterministic JSON for hashing and signing."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_hash(value: Any) -> str:
    """Return a SHA-256 hex digest over canonical JSON."""
    return hashlib.sha256(canonical_dumps(value).encode("utf-8")).hexdigest()


def hmac_sign(secret_key: bytes, value: Any) -> str:
    """Return an HMAC-SHA256 signature over canonical JSON."""
    if not isinstance(secret_key, bytes):
        raise TypeError("secret_key must be bytes")
    if not secret_key:
        raise ValueError("secret_key must not be empty")
    return hmac.new(
        secret_key,
        canonical_dumps(value).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def hmac_verify(secret_key: bytes, value: Any, signature: str) -> bool:
    """Verify an HMAC-SHA256 signature over canonical JSON."""
    expected = hmac_sign(secret_key, value)
    return hmac.compare_digest(expected, signature)


def without_keys(value: Mapping[str, Any], *keys: str) -> dict:
    """Return a copy of *value* without the named keys."""
    return {k: v for k, v in value.items() if k not in set(keys)}
