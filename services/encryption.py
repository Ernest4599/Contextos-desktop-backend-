"""
Field-level encryption for sensitive stored text (AIOS memory content).
Uses Fernet (AES-128 in CBC mode with HMAC authentication) from the
`cryptography` library. The key lives in the ENCRYPTION_KEY env var on
Render - same pattern as JWT_SECRET.

This is encryption at rest, not zero-knowledge: the backend holds the
key and can decrypt, since the app needs to read memories back to
function (Overview, Quick Prompt, duplicate detection, etc). It
protects against someone browsing the raw database (dashboard access,
backup leaks, a stolen DB file) - it does not protect against someone
with backend/env-var access.
"""
from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

_ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "")
_fernet: Fernet | None = None

if _ENCRYPTION_KEY:
    try:
        _fernet = Fernet(_ENCRYPTION_KEY.encode("utf-8"))
    except (ValueError, TypeError) as e:
        print(f"[ENCRYPTION] Invalid ENCRYPTION_KEY, encryption disabled: {e}")
        _fernet = None
else:
    print("[ENCRYPTION] ENCRYPTION_KEY is not configured - memory content will be stored in plaintext")


def encrypt_text(plaintext: str) -> str:
    """Encrypts text for storage. Returns plaintext unchanged if no key is configured."""
    if _fernet is None:
        return plaintext
    return _fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_text(stored_value: str) -> str:
    """
    Decrypts a stored value. If it doesn't look like ciphertext (e.g. it
    was written before encryption was enabled, or the key is missing),
    returns it as-is rather than erroring - so old plaintext rows still
    display correctly during the transition.
    """
    if _fernet is None:
        return stored_value
    try:
        return _fernet.decrypt(stored_value.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return stored_value
