from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet


def derive_fernet_key(secret: str) -> bytes:
    """Derive a urlsafe-base64 Fernet key from an arbitrary secret string (SHA-256).

    Bit-compatible with the StaffDeck scheme: the key is
    ``b64(sha256(secret))``, so pass ``CHANNEL_SECRET`` (or the
    ``f"{APP_SECRET}:channel"`` dev fallback) to interoperate with tokens
    encrypted by the main application.
    """
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_secret(plaintext: str, key: bytes) -> str:
    """Encrypt a channel credential (e.g. bot_token) with an explicit Fernet key."""
    return Fernet(key).encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str, key: bytes) -> str:
    """Decrypt a channel credential encrypted by :func:`encrypt_secret`."""
    return Fernet(key).decrypt(token.encode("utf-8")).decode("utf-8")
