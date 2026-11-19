"""API key generation, hashing and scope checks.

Token format: ``hd_<prefix>_<secret>``. The prefix is stored in clear for lookup; the secret is
only ever stored as ``sha256(salt + secret)`` with a per-key random salt.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

TOKEN_PREFIX = "hd_"  # noqa: S105 (a literal prefix, not a credential)

SCOPES: tuple[str, ...] = (
    "admin",
    "agents:read",
    "agents:write",
    "audit:read",
    "customers:read",
    "customers:write",
    "kb:read",
    "kb:write",
    "macros:read",
    "macros:write",
    "orders:read",
    "orders:write",
    "refunds:read",
    "refunds:write",
    "reports:read",
    "sla:read",
    "sla:write",
    "tickets:read",
    "tickets:write",
    "webhooks:read",
    "webhooks:write",
)


@dataclass(frozen=True)
class NewKeyMaterial:
    token: str
    prefix: str
    salt: str
    key_hash: str


def hash_secret(salt: str, secret: str) -> str:
    return hashlib.sha256(f"{salt}{secret}".encode()).hexdigest()


def generate_key() -> NewKeyMaterial:
    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    salt = secrets.token_hex(16)
    return NewKeyMaterial(
        token=f"{TOKEN_PREFIX}{prefix}_{secret}",
        prefix=prefix,
        salt=salt,
        key_hash=hash_secret(salt, secret),
    )


def split_token(token: str) -> tuple[str, str] | None:
    """Return ``(prefix, secret)`` or ``None`` if the token is malformed."""
    if not token.startswith(TOKEN_PREFIX):
        return None
    prefix, sep, secret = token[len(TOKEN_PREFIX) :].partition("_")
    if not sep or not prefix or not secret:
        return None
    return prefix, secret


def verify_secret(salt: str, secret: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_secret(salt, secret), expected_hash)


def scopes_allow(granted: frozenset[str], required: str) -> bool:
    return "admin" in granted or required in granted
