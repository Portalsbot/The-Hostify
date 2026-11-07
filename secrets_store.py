"""Authenticated encryption for customer bot environment variables at rest."""
from __future__ import annotations

import json
import os
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

_KEY = os.getenv("DATA_ENCRYPTION_KEY", "").strip().encode("ascii")
_CIPHER = None
if _KEY:
    try:
        _CIPHER = Fernet(_KEY)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("DATA_ENCRYPTION_KEY is invalid. Generate a Fernet key and restart.") from exc


def encryption_enabled() -> bool:
    return _CIPHER is not None


def encrypt_env_vars(values: dict[str, str] | None) -> dict[str, str]:
    clean = values or {}
    if not clean:
        return {}
    if _CIPHER is None:
        raise RuntimeError(
            "DATA_ENCRYPTION_KEY is required before storing bot environment variables. "
            "Generate a key with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    payload = json.dumps(clean, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return {"_fernet_v1": _CIPHER.encrypt(payload).decode("ascii")}


def decrypt_env_vars(stored: Any) -> dict[str, str]:
    if not stored:
        return {}
    if not isinstance(stored, dict):
        raise RuntimeError("Stored environment variables have an unsupported format.")
    token = stored.get("_fernet_v1")
    if token is None:
        # Legacy plaintext records are migrated during database startup.
        return {str(k): str(v) for k, v in stored.items()}
    if _CIPHER is None:
        raise RuntimeError("DATA_ENCRYPTION_KEY is required to decrypt stored bot credentials.")
    try:
        decoded = _CIPHER.decrypt(str(token).encode("ascii"))
        result = json.loads(decoded.decode("utf-8"))
    except (InvalidToken, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Could not decrypt bot environment variables; check DATA_ENCRYPTION_KEY.") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Decrypted bot environment variables are malformed.")
    return {str(k): str(v) for k, v in result.items()}
