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


from io import BytesIO, StringIO

from wechat_ilink.media import MAX_CHANNEL_MEDIA_BYTES, _read_media_bytes


@pytest.mark.parametrize(
    "value",
    [b"abc", bytearray(b"abc"), memoryview(b"abc")],
)
def test_read_media_bytes_accepts_bytes_like(value: object) -> None:
    assert _read_media_bytes(value) == b"abc"


def test_read_media_bytes_reads_from_current_position_without_closing() -> None:
    stream = BytesIO(b"prefix-payload")
    stream.seek(7)
    assert _read_media_bytes(stream) == b"payload"
    assert not stream.closed
    assert stream.tell() == len(b"prefix-payload")


class ChunkedStream:
    def __init__(self) -> None:
        self.parts = iter([b"ab", b"cd", b""])

    def read(self, size: int = -1) -> bytes:
        assert size > 0
        return next(self.parts)


def test_read_media_bytes_accepts_chunked_binary_stream() -> None:
    assert _read_media_bytes(ChunkedStream()) == b"abcd"


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (b"", WeChatErrorCode.MEDIA_EMPTY),
        (object(), WeChatErrorCode.INVALID_MEDIA_INPUT),
        (StringIO("text"), WeChatErrorCode.MEDIA_READ_FAILED),
    ],
)
def test_read_media_bytes_rejects_invalid_input(
    value: object,
    code: WeChatErrorCode,
) -> None:
    with pytest.raises(WeChatMediaError) as raised:
        _read_media_bytes(value)
    assert raised.value.code is code


class FailingStream:
    def read(self, size: int = -1) -> bytes:
        raise OSError("boom")


def test_read_media_bytes_preserves_read_failure_as_cause() -> None:
    with pytest.raises(WeChatMediaError) as raised:
        _read_media_bytes(FailingStream())
    assert raised.value.code is WeChatErrorCode.MEDIA_READ_FAILED
    assert isinstance(raised.value.__cause__, OSError)


def test_read_media_bytes_accepts_exact_limit() -> None:
    assert len(_read_media_bytes(b"x" * MAX_CHANNEL_MEDIA_BYTES)) == MAX_CHANNEL_MEDIA_BYTES


def test_read_media_bytes_rejects_one_byte_over_limit() -> None:
    with pytest.raises(WeChatMediaError) as raised:
        _read_media_bytes(b"x" * (MAX_CHANNEL_MEDIA_BYTES + 1))
    assert raised.value.code is WeChatErrorCode.MEDIA_TOO_LARGE
