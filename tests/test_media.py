import base64

import pytest

from wechat_ilink import WeChatApiError, decrypt_wechat_media


def test_decrypt_wechat_media_aes_ecb_pkcs7() -> None:
    key = b"0123456789abcdef"
    aes_key = base64.b64encode(key.hex().encode("ascii")).decode("ascii")
    plaintext = b"\xff\xd8\xffjpeg-data\xff\xd9"
    encrypted = bytes.fromhex("06780e9084a586882bfbed1c7dbd461b")

    assert decrypt_wechat_media(encrypted, aes_key, expected_size=len(plaintext)) == plaintext


def test_decrypt_wechat_media_empty_key_passthrough() -> None:
    assert decrypt_wechat_media(b"raw-bytes", "") == b"raw-bytes"


def test_decrypt_wechat_media_bad_key_raises_api_error() -> None:
    with pytest.raises(WeChatApiError):
        decrypt_wechat_media(b"\x00" * 16, "not-valid-base64!!!")


def test_decrypt_wechat_media_size_mismatch() -> None:
    key = b"0123456789abcdef"
    aes_key = base64.b64encode(key.hex().encode("ascii")).decode("ascii")
    encrypted = bytes.fromhex("06780e9084a586882bfbed1c7dbd461b")

    with pytest.raises(WeChatApiError):
        decrypt_wechat_media(encrypted, aes_key, expected_size=999)


from wechat_ilink import (
    WeChatErrorCode,
    WeChatMediaError,
    aes_ecb_padded_size,
)


def test_media_error_exposes_stable_fields() -> None:
    error = WeChatMediaError(
        WeChatErrorCode.MEDIA_TOO_LARGE,
        "read",
        "too large",
        status_code=413,
    )
    assert error.code is WeChatErrorCode.MEDIA_TOO_LARGE
    assert error.stage == "read"
    assert error.status_code == 413
    assert "media_too_large" in str(error)


@pytest.mark.parametrize(
    ("plaintext_size", "ciphertext_size"),
    [(0, 16), (1, 16), (15, 16), (16, 32), (17, 32)],
)
def test_aes_ecb_padded_size(plaintext_size: int, ciphertext_size: int) -> None:
    assert aes_ecb_padded_size(plaintext_size) == ciphertext_size


def test_aes_ecb_padded_size_rejects_negative_value() -> None:
    with pytest.raises(WeChatMediaError) as raised:
        aes_ecb_padded_size(-1)
    assert raised.value.code is WeChatErrorCode.INVALID_MEDIA_INPUT


from wechat_ilink import encrypt_wechat_media


def test_encrypt_wechat_media_aes_128_ecb_pkcs7() -> None:
    plaintext = b"\xff\xd8\xffjpeg-data\xff\xd9"
    key = b"0123456789abcdef"
    assert encrypt_wechat_media(plaintext, key) == bytes.fromhex(
        "06780e9084a586882bfbed1c7dbd461b"
    )


def test_encrypt_wechat_media_adds_full_padding_block() -> None:
    encrypted = encrypt_wechat_media(b"x" * 16, b"0123456789abcdef")
    assert len(encrypted) == 32


def test_encrypt_wechat_media_rejects_non_128_bit_key() -> None:
    with pytest.raises(WeChatMediaError) as raised:
        encrypt_wechat_media(b"data", b"short")
    assert raised.value.code is WeChatErrorCode.MEDIA_ENCRYPTION_FAILED
    assert raised.value.stage == "encrypt"
