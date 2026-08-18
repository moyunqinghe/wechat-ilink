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
