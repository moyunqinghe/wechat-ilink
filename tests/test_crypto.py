import base64
import hashlib

from wechat_ilink import decrypt_secret, derive_fernet_key, encrypt_secret


def test_derive_fernet_key_matches_sha256_scheme() -> None:
    key = derive_fernet_key("my-channel-secret")
    assert key == base64.urlsafe_b64encode(hashlib.sha256(b"my-channel-secret").digest())


def test_encrypt_decrypt_roundtrip() -> None:
    key = derive_fernet_key("my-channel-secret")
    token = encrypt_secret("real_bot_token", key)
    assert token != "real_bot_token"
    assert decrypt_secret(token, key) == "real_bot_token"


def test_cross_secret_incompatible() -> None:
    token = encrypt_secret("real_bot_token", derive_fernet_key("secret-a"))
    try:
        decrypt_secret(token, derive_fernet_key("secret-b"))
        raised = False
    except Exception:
        raised = True
    assert raised
