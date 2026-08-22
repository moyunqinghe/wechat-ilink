# wechat-ilink v0.2.0 Outbound Media Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add offline-testable outbound image and ordinary-file delivery through the WeChat iLink `getuploadurl` → encrypted CDN upload → `sendmessage` protocol.

**Architecture:** Keep the public workflow on the synchronous `WeChatClient`, add focused pure media helpers in `media.py`, and share a private single-item message sender with the existing text API. Inject the business and CDN HTTP transports plus media randomness so every protocol transition can be asserted with `httpx.MockTransport` and fixed bytes.

**Tech Stack:** Python 3.11+, `httpx`, `cryptography`, `pytest`, standard-library `hashlib`, `base64`, `enum`, and binary IO protocols.

**Spec:** `docs/superpowers/specs/2026-08-22-outbound-media-v0.2.0-design.md`

## Global Constraints

- The repository remains the only source of truth and must not depend on FeiBot, StaffDeck, OpenClaw, a database, a web framework, `.env`, or host business code.
- Media APIs accept only bytes-like objects or binary streams; they never resolve or read filesystem paths.
- Both iLink and CDN HTTP use `httpx`; all automated tests use `httpx.MockTransport` and make no network requests.
- Plaintext image/file size limit is exactly `25 * 1024 * 1024` bytes; empty media is invalid.
- Outbound encryption is AES-128-ECB with PKCS#7 padding; the key is exactly 16 bytes.
- v0.2.0 supports images and ordinary files only: no video, native voice, thumbnails, captions, batch attachments, or automatic upload retry.
- Existing public APIs and return values remain backward compatible; `send_message`, `send_image`, and `send_file` return `None` on success.
- Package version and `CHANNEL_VERSION` become `0.2.0`; do not create a tag, push, or modify another repository.
- Preserve the Tencent 2026 MIT attribution for substantially adapted protocol code.

## File Structure

- `wechat_ilink/errors.py`: stable media error enum and exception, while preserving `WeChatApiError`.
- `wechat_ilink/media.py`: pure padded-size/encryption functions and private bounded bytes/stream normalization.
- `wechat_ilink/client.py`: injected CDN client/randomness, upload URL negotiation, CDN upload, shared item sending, and public image/file workflows.
- `wechat_ilink/__init__.py`: stable top-level exports for the new errors and pure media helpers.
- `tests/test_media.py`: pure encryption, input, size, and error tests.
- `tests/test_outbound_media.py`: complete mocked iLink/CDN/payload tests.
- `tests/test_client.py`: regression coverage for shared item sending and client lifecycle.
- `README.md`: public API, examples, offline-testing boundary, limitations, and corrected commands.
- `NOTICE`: Tencent protocol-reference and MIT attribution.
- `pyproject.toml`: package version `0.2.0`.

---

### Task 1: Stable media errors and AES protocol primitives

**Files:**
- Modify: `wechat_ilink/errors.py`
- Modify: `wechat_ilink/media.py`
- Modify: `wechat_ilink/__init__.py`
- Test: `tests/test_media.py`

**Interfaces:**
- Consumes: existing `MAX_CHANNEL_MEDIA_BYTES`, `WeChatApiError`, and `cryptography` dependency.
- Produces: `WeChatErrorCode`, `WeChatMediaError`, `aes_ecb_padded_size(plaintext_size: int) -> int`, and `encrypt_wechat_media(data: bytes, key: bytes) -> bytes`.

- [ ] **Step 1: Add failing public-error and padded-size tests**

Append to `tests/test_media.py`:

```python
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
```

- [ ] **Step 2: Run the new tests and verify import failure**

Run: `PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_media.py`

Expected: collection fails because the four new public names are not exported.

- [ ] **Step 3: Implement the stable error types and padded-size function**

Add to `wechat_ilink/errors.py`:

```python
from enum import StrEnum


class WeChatErrorCode(StrEnum):
    INVALID_MEDIA_INPUT = "invalid_media_input"
    MEDIA_EMPTY = "media_empty"
    MEDIA_TOO_LARGE = "media_too_large"
    MEDIA_READ_FAILED = "media_read_failed"
    MEDIA_ENCRYPTION_FAILED = "media_encryption_failed"
    UPLOAD_URL_HTTP_ERROR = "upload_url_http_error"
    UPLOAD_URL_REJECTED = "upload_url_rejected"
    UPLOAD_URL_INVALID_RESPONSE = "upload_url_invalid_response"
    CDN_UPLOAD_HTTP_ERROR = "cdn_upload_http_error"
    CDN_UPLOAD_INVALID_RESPONSE = "cdn_upload_invalid_response"


class WeChatMediaError(Exception):
    def __init__(
        self,
        code: WeChatErrorCode,
        stage: str,
        message: str,
        *,
        status_code: int | None = None,
    ):
        super().__init__(f"{code.value} stage={stage}: {message}")
        self.code = code
        self.stage = stage
        self.status_code = status_code
```

Add to `wechat_ilink/media.py`:

```python
from .errors import WeChatErrorCode, WeChatMediaError


def aes_ecb_padded_size(plaintext_size: int) -> int:
    if plaintext_size < 0:
        raise WeChatMediaError(
            WeChatErrorCode.INVALID_MEDIA_INPUT,
            "encrypt",
            "plaintext size must be non-negative",
        )
    return ((plaintext_size // 16) + 1) * 16
```

Export the four public names from `wechat_ilink/__init__.py` and `__all__`.

- [ ] **Step 4: Run the focused tests and verify padded-size tests pass**

Run: `PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_media.py`

Expected: PASS.

- [ ] **Step 5: Add failing deterministic encryption tests**

Append to `tests/test_media.py`:

```python
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
```

- [ ] **Step 6: Run the deterministic encryption tests and verify failure**

Run: `PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_media.py -k 'encrypt_wechat_media'`

Expected: FAIL because `encrypt_wechat_media` is missing or does not yet encrypt.

- [ ] **Step 7: Implement minimal AES-128-ECB encryption**

Add to `wechat_ilink/media.py`, reusing its existing cryptography imports:

```python
def encrypt_wechat_media(data: bytes, key: bytes) -> bytes:
    if len(key) != 16:
        raise WeChatMediaError(
            WeChatErrorCode.MEDIA_ENCRYPTION_FAILED,
            "encrypt",
            "AES-128 key must be exactly 16 bytes",
        )
    try:
        padder = padding.PKCS7(algorithms.AES.block_size).padder()
        padded = padder.update(data) + padder.finalize()
        encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        return encryptor.update(padded) + encryptor.finalize()
    except (TypeError, ValueError) as exc:
        raise WeChatMediaError(
            WeChatErrorCode.MEDIA_ENCRYPTION_FAILED,
            "encrypt",
            "media encryption failed",
        ) from exc
```

- [ ] **Step 8: Run all media tests**

Run: `PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_media.py`

Expected: PASS.

- [ ] **Step 9: Commit the protocol primitives**

```bash
git add wechat_ilink/errors.py wechat_ilink/media.py wechat_ilink/__init__.py tests/test_media.py
git commit -m "feat: add outbound media crypto primitives"
```

---

### Task 2: Bounded bytes and binary-stream input

**Files:**
- Modify: `wechat_ilink/media.py`
- Test: `tests/test_media.py`

**Interfaces:**
- Consumes: `MAX_CHANNEL_MEDIA_BYTES`, `WeChatErrorCode`, and `WeChatMediaError` from Task 1.
- Produces: private `_read_media_bytes(data: object) -> bytes`, later consumed by `WeChatClient.send_image` and `send_file`.

- [ ] **Step 1: Add failing bytes-like and stream tests**

Import the private helper directly for focused internal testing and append:

```python
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
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_media.py -k 'read_media_bytes'`

Expected: collection or tests FAIL because `_read_media_bytes` is missing.

- [ ] **Step 3: Implement bounded normalization**

Add to `wechat_ilink/media.py`:

```python
_MEDIA_READ_CHUNK_BYTES = 64 * 1024


def _read_media_bytes(data: object) -> bytes:
    if isinstance(data, (bytes, bytearray, memoryview)):
        result = bytes(data)
        if not result:
            raise WeChatMediaError(WeChatErrorCode.MEDIA_EMPTY, "read", "media is empty")
        if len(result) > MAX_CHANNEL_MEDIA_BYTES:
            raise WeChatMediaError(
                WeChatErrorCode.MEDIA_TOO_LARGE,
                "read",
                f"media exceeds {MAX_CHANNEL_MEDIA_BYTES} bytes",
            )
        return result

    read = getattr(data, "read", None)
    if not callable(read):
        raise WeChatMediaError(
            WeChatErrorCode.INVALID_MEDIA_INPUT,
            "read",
            "expected bytes-like object or binary stream",
        )
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            chunk = read(min(_MEDIA_READ_CHUNK_BYTES, MAX_CHANNEL_MEDIA_BYTES + 1 - total))
            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                raise TypeError("binary stream read() must return bytes-like data")
            if not chunk:
                break
            normalized = bytes(chunk)
            total += len(normalized)
            if total > MAX_CHANNEL_MEDIA_BYTES:
                raise WeChatMediaError(
                    WeChatErrorCode.MEDIA_TOO_LARGE,
                    "read",
                    f"media exceeds {MAX_CHANNEL_MEDIA_BYTES} bytes",
                )
            chunks.append(normalized)
    except WeChatMediaError:
        raise
    except Exception as exc:
        raise WeChatMediaError(
            WeChatErrorCode.MEDIA_READ_FAILED,
            "read",
            "binary stream read failed",
        ) from exc
    result = b"".join(chunks)
    if not result:
        raise WeChatMediaError(WeChatErrorCode.MEDIA_EMPTY, "read", "media is empty")
    return result
```

- [ ] **Step 4: Run accepted-input tests**

Run: `PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_media.py -k 'read_media_bytes'`

Expected: PASS.

- [ ] **Step 5: Add failing invalid-input and boundary tests**

Append:

```python
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
```

- [ ] **Step 6: Run boundary/error tests and fix only observed mismatches**

Run: `PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_media.py`

Expected: PASS; text streams map to `MEDIA_READ_FAILED`, and exactly 25 MiB succeeds.

- [ ] **Step 7: Commit bounded media input**

```bash
git add wechat_ilink/media.py tests/test_media.py
git commit -m "feat: add bounded outbound media input"
```

---

### Task 3: Upload URL negotiation and injectable CDN transport

**Files:**
- Modify: `wechat_ilink/client.py`
- Create: `tests/test_outbound_media.py`
- Test: `tests/test_client.py`

**Interfaces:**
- Consumes: `WeChatErrorCode`, `WeChatMediaError`, `aes_ecb_padded_size`, and existing business headers/base info.
- Produces: constructor keyword arguments `cdn_transport` and `random_bytes`; private `_get_upload_url(...) -> str`; correct close semantics.

- [ ] **Step 1: Add failing constructor/lifecycle and `getuploadurl` tests**

Create `tests/test_outbound_media.py` with imports, a fixed source, and the first protocol test:

```python
import hashlib
import json

import httpx
import pytest

from wechat_ilink import WeChatApiError, WeChatErrorCode, WeChatMediaError
from wechat_ilink.client import CHANNEL_VERSION, WeChatClient

BASE_URL = "https://ilinkai.weixin.qq.com"
CDN_URL = "https://novac2c.cdn.weixin.qq.com/c2c/upload?taskid=t1"
FIXED_FILEKEY = bytes.fromhex("00112233445566778899aabbccddeeff")
FIXED_AES_KEY = bytes.fromhex("ffeeddccbbaa99887766554433221100")


class FixedRandom:
    def __init__(self) -> None:
        self.values = iter([FIXED_FILEKEY, FIXED_AES_KEY])

    def __call__(self, size: int) -> bytes:
        assert size == 16
        return next(self.values)


def test_get_upload_url_sends_exact_image_metadata() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ret": 0, "upload_full_url": CDN_URL})

    client = WeChatClient(
        BASE_URL,
        "bot-token",
        transport=httpx.MockTransport(handler),
        random_bytes=FixedRandom(),
    )
    plaintext = b"image-data"
    upload_url = client._get_upload_url(
        to_user_id="user@im.wechat",
        media_type=1,
        plaintext=plaintext,
        ciphertext_size=16,
        filekey=FIXED_FILEKEY.hex(),
        aeskey=FIXED_AES_KEY.hex(),
    )

    assert upload_url == CDN_URL
    request = captured["request"]
    assert isinstance(request, httpx.Request)
    assert request.url.path == "/ilink/bot/getuploadurl"
    assert request.headers["Authorization"] == "Bearer bot-token"
    assert captured["body"] == {
        "filekey": FIXED_FILEKEY.hex(),
        "media_type": 1,
        "to_user_id": "user@im.wechat",
        "rawsize": len(plaintext),
        "rawfilemd5": hashlib.md5(plaintext).hexdigest(),
        "filesize": 16,
        "no_need_thumb": True,
        "aeskey": FIXED_AES_KEY.hex(),
        "base_info": {"channel_version": CHANNEL_VERSION},
    }
```

Add to `tests/test_client.py`:

```python
def test_client_accepts_separate_cdn_mock_transport() -> None:
    business = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    cdn = httpx.MockTransport(lambda request: httpx.Response(200))
    client = WeChatClient(BASE_URL, "token", transport=business, cdn_transport=cdn)
    assert client._cdn_client is not client._client
    client.close()
```

- [ ] **Step 2: Run the new tests and verify signature/helper failure**

Run: `PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_outbound_media.py tests/test_client.py -k 'upload_url or separate_cdn'`

Expected: FAIL because the constructor keywords, `_cdn_client`, and `_get_upload_url` do not exist.

- [ ] **Step 3: Extend client construction and implement `_get_upload_url`**

In `wechat_ilink/client.py`:

```python
import hashlib
from collections.abc import Callable

from .errors import WeChatErrorCode, WeChatMediaError


def __init__(
    self,
    base_url: str,
    bot_token: str = "",
    *,
    transport: httpx.BaseTransport | None = None,
    cdn_transport: httpx.BaseTransport | None = None,
    random_bytes: Callable[[int], bytes] = os.urandom,
):
    self.base_url = base_url.rstrip("/")
    self.bot_token = bot_token
    self._random_bytes = random_bytes
    self._client = httpx.Client(transport=transport)
    self._cdn_client = (
        self._client
        if cdn_transport is None
        else httpx.Client(transport=cdn_transport)
    )
```

Implement the private method:

```python
def _get_upload_url(
    self,
    *,
    to_user_id: str,
    media_type: int,
    plaintext: bytes,
    ciphertext_size: int,
    filekey: str,
    aeskey: str,
) -> str:
    try:
        response = self._client.post(
            f"{self.base_url}/ilink/bot/getuploadurl",
            headers=self._business_headers(),
            json={
                "filekey": filekey,
                "media_type": media_type,
                "to_user_id": to_user_id,
                "rawsize": len(plaintext),
                "rawfilemd5": hashlib.md5(plaintext).hexdigest(),
                "filesize": ciphertext_size,
                "no_need_thumb": True,
                "aeskey": aeskey,
                "base_info": self._base_info(),
            },
            timeout=20.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        raise WeChatMediaError(
            WeChatErrorCode.UPLOAD_URL_HTTP_ERROR,
            "getuploadurl",
            "getuploadurl request failed",
            status_code=status,
        ) from exc
    try:
        data = response.json()
    except ValueError as exc:
        raise WeChatMediaError(
            WeChatErrorCode.UPLOAD_URL_INVALID_RESPONSE,
            "getuploadurl",
            "getuploadurl returned invalid JSON",
        ) from exc
    if not isinstance(data, dict):
        raise WeChatMediaError(
            WeChatErrorCode.UPLOAD_URL_INVALID_RESPONSE,
            "getuploadurl",
            "getuploadurl response must be an object",
        )
    errcode = int(data.get("errcode") or data.get("ret") or 0)
    if errcode:
        raise WeChatApiError(errcode, str(data.get("errmsg") or ""))
    upload_full_url = str(data.get("upload_full_url") or "").strip()
    if upload_full_url:
        return upload_full_url
    upload_param = str(data.get("upload_param") or "").strip()
    if upload_param:
        return _build_cdn_upload_url(upload_param, filekey)
    raise WeChatMediaError(
        WeChatErrorCode.UPLOAD_URL_INVALID_RESPONSE,
        "getuploadurl",
        "response missing upload_full_url and upload_param",
    )
```

Define the official fallback builder beside constants, using `urlencode` to avoid string interpolation errors:

```python
from urllib.parse import urlencode

WECHAT_CDN_UPLOAD_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c/upload"


def _build_cdn_upload_url(upload_param: str, filekey: str) -> str:
    return f"{WECHAT_CDN_UPLOAD_BASE_URL}?{urlencode({'encrypted_query_param': upload_param, 'filekey': filekey})}"
```

Update `close()` so it closes `_cdn_client` first only when it is not `_client`, then closes `_client`.

- [ ] **Step 4: Run the first negotiation/lifecycle tests**

Run: `PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_outbound_media.py tests/test_client.py -k 'upload_url or separate_cdn'`

Expected: PASS.

- [ ] **Step 5: Add failing fallback and malformed-response tests**

Append parameterized helpers/tests to `tests/test_outbound_media.py`:

```python
def make_client(handler) -> WeChatClient:
    return WeChatClient(BASE_URL, "bot-token", transport=httpx.MockTransport(handler))


def upload_url_call(client: WeChatClient) -> str:
    return client._get_upload_url(
        to_user_id="user@im.wechat",
        media_type=3,
        plaintext=b"file",
        ciphertext_size=16,
        filekey=FIXED_FILEKEY.hex(),
        aeskey=FIXED_AES_KEY.hex(),
    )


def test_get_upload_url_builds_official_fallback_url() -> None:
    client = make_client(
        lambda request: httpx.Response(200, json={"upload_param": "a+b/c="})
    )
    url = httpx.URL(upload_url_call(client))
    assert url.host == "novac2c.cdn.weixin.qq.com"
    assert url.params["encrypted_query_param"] == "a+b/c="
    assert url.params["filekey"] == FIXED_FILEKEY.hex()


@pytest.mark.parametrize(
    ("response", "error_type", "code"),
    [
        (httpx.Response(503), WeChatMediaError, WeChatErrorCode.UPLOAD_URL_HTTP_ERROR),
        (httpx.Response(200, content=b"not-json"), WeChatMediaError, WeChatErrorCode.UPLOAD_URL_INVALID_RESPONSE),
        (httpx.Response(200, json=[]), WeChatMediaError, WeChatErrorCode.UPLOAD_URL_INVALID_RESPONSE),
        (httpx.Response(200, json={}), WeChatMediaError, WeChatErrorCode.UPLOAD_URL_INVALID_RESPONSE),
        (httpx.Response(200, json={"ret": -2, "errmsg": "bad"}), WeChatApiError, None),
    ],
)
def test_get_upload_url_rejects_error_responses(response, error_type, code) -> None:
    client = make_client(lambda request: response)
    with pytest.raises(error_type) as raised:
        upload_url_call(client)
    if code is not None:
        assert raised.value.code is code
```

- [ ] **Step 6: Run negotiation error tests**

Run: `PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_outbound_media.py -k 'get_upload_url'`

Expected: PASS.

- [ ] **Step 7: Run all existing client tests for constructor compatibility**

Run: `PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_client.py`

Expected: PASS with existing callers still supplying only `transport`.

- [ ] **Step 8: Commit upload negotiation**

```bash
git add wechat_ilink/client.py tests/test_client.py tests/test_outbound_media.py
git commit -m "feat: negotiate outbound media uploads"
```

---

### Task 4: Secure encrypted CDN upload

**Files:**
- Modify: `wechat_ilink/client.py`
- Modify: `tests/test_outbound_media.py`

**Interfaces:**
- Consumes: injected `_cdn_client`, `validate_wechat_host`, `WeChatMediaError`, and ciphertext bytes.
- Produces: private `_upload_media_to_cdn(upload_url: str, ciphertext: bytes) -> str` returning `x-encrypted-param`.

- [ ] **Step 1: Add failing successful-upload test**

Append:

```python
def test_upload_media_to_cdn_posts_ciphertext_and_returns_param() -> None:
    captured: dict[str, object] = {}

    def business_handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("business API must not be called")

    def cdn_handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, headers={"X-Encrypted-Param": "download-param"})

    client = WeChatClient(
        BASE_URL,
        "secret-token",
        transport=httpx.MockTransport(business_handler),
        cdn_transport=httpx.MockTransport(cdn_handler),
    )
    result = client._upload_media_to_cdn(CDN_URL, b"ciphertext")

    assert result == "download-param"
    request = captured["request"]
    assert isinstance(request, httpx.Request)
    assert request.method == "POST"
    assert request.content == b"ciphertext"
    assert request.headers["Content-Type"] == "application/octet-stream"
    assert request.headers["Content-Length"] == str(len(b"ciphertext"))
    assert "Authorization" not in request.headers
```

- [ ] **Step 2: Run the focused test and verify helper failure**

Run: `PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_outbound_media.py::test_upload_media_to_cdn_posts_ciphertext_and_returns_param`

Expected: FAIL because `_upload_media_to_cdn` does not exist.

- [ ] **Step 3: Implement validation and one-shot CDN POST**

Add to `wechat_ilink/client.py`:

```python
from urllib.parse import urlparse

from .security import validate_wechat_host


def _upload_media_to_cdn(self, upload_url: str, ciphertext: bytes) -> str:
    try:
        parsed = urlparse(upload_url)
        trusted = parsed.scheme == "https" and validate_wechat_host(parsed.hostname or "")
    except ValueError:
        trusted = False
    if not trusted:
        raise WeChatMediaError(
            WeChatErrorCode.UPLOAD_URL_REJECTED,
            "cdn_upload",
            "CDN upload URL is not trusted",
        )
    try:
        response = self._cdn_client.post(
            upload_url,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(ciphertext)),
            },
            content=ciphertext,
            timeout=30.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        raise WeChatMediaError(
            WeChatErrorCode.CDN_UPLOAD_HTTP_ERROR,
            "cdn_upload",
            "CDN upload failed",
            status_code=status,
        ) from exc
    encrypted_param = response.headers.get("x-encrypted-param", "").strip()
    if not encrypted_param:
        raise WeChatMediaError(
            WeChatErrorCode.CDN_UPLOAD_INVALID_RESPONSE,
            "cdn_upload",
            "CDN response missing x-encrypted-param",
        )
    return encrypted_param
```

- [ ] **Step 4: Run the successful CDN test**

Run: `PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_outbound_media.py::test_upload_media_to_cdn_posts_ciphertext_and_returns_param`

Expected: PASS.

- [ ] **Step 5: Add failing URL and response error tests**

Append:

```python
@pytest.mark.parametrize(
    "url",
    [
        "http://novac2c.cdn.weixin.qq.com/c2c/upload",
        "https://evil.example/upload",
        "https://weixin.qq.com.evil.example/upload",
        "not-a-url",
    ],
)
def test_upload_media_to_cdn_rejects_untrusted_url_before_http(url: str) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, headers={"x-encrypted-param": "x"})

    client = WeChatClient(BASE_URL, cdn_transport=httpx.MockTransport(handler))
    with pytest.raises(WeChatMediaError) as raised:
        client._upload_media_to_cdn(url, b"ciphertext")
    assert raised.value.code is WeChatErrorCode.UPLOAD_URL_REJECTED
    assert calls == 0


@pytest.mark.parametrize(
    ("response", "code", "status"),
    [
        (httpx.Response(403), WeChatErrorCode.CDN_UPLOAD_HTTP_ERROR, 403),
        (httpx.Response(500), WeChatErrorCode.CDN_UPLOAD_HTTP_ERROR, 500),
        (httpx.Response(200), WeChatErrorCode.CDN_UPLOAD_INVALID_RESPONSE, None),
        (httpx.Response(200, headers={"x-encrypted-param": " "}), WeChatErrorCode.CDN_UPLOAD_INVALID_RESPONSE, None),
    ],
)
def test_upload_media_to_cdn_rejects_bad_response(response, code, status) -> None:
    client = WeChatClient(
        BASE_URL,
        cdn_transport=httpx.MockTransport(lambda request: response),
    )
    with pytest.raises(WeChatMediaError) as raised:
        client._upload_media_to_cdn(CDN_URL, b"ciphertext")
    assert raised.value.code is code
    assert raised.value.status_code == status
```

- [ ] **Step 6: Run all CDN tests**

Run: `PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_outbound_media.py -k 'cdn'`

Expected: PASS and rejected URLs produce zero transport calls.

- [ ] **Step 7: Commit secure CDN upload**

```bash
git add wechat_ilink/client.py tests/test_outbound_media.py
git commit -m "feat: upload encrypted media to wechat cdn"
```

---

### Task 5: Shared item sender and end-to-end image/file APIs

**Files:**
- Modify: `wechat_ilink/client.py`
- Modify: `tests/test_outbound_media.py`
- Modify: `tests/test_client.py`

**Interfaces:**
- Consumes: `_read_media_bytes`, `encrypt_wechat_media`, `_get_upload_url`, `_upload_media_to_cdn`, `_random_bytes`, and existing `sendmessage` envelope.
- Produces: public `send_image(...) -> None`, `send_file(...) -> None`, and private `_send_item(...) -> None`.

- [ ] **Step 1: Add a failing end-to-end image test**

Append a recorder that handles all three calls:

```python
from base64 import b64encode

from wechat_ilink import encrypt_wechat_media


def test_send_image_runs_complete_protocol_and_sends_exact_payload() -> None:
    calls: list[httpx.Request] = []
    plaintext = b"image-data"
    ciphertext = encrypt_wechat_media(plaintext, FIXED_AES_KEY)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/ilink/bot/getuploadurl":
            return httpx.Response(200, json={"upload_full_url": CDN_URL})
        if request.url.host == "novac2c.cdn.weixin.qq.com":
            assert request.content == ciphertext
            return httpx.Response(200, headers={"x-encrypted-param": "download-image"})
        if request.url.path == "/ilink/bot/sendmessage":
            return httpx.Response(200, json={"ret": 0})
        return httpx.Response(404)

    client = WeChatClient(
        BASE_URL,
        "bot-token",
        transport=httpx.MockTransport(handler),
        random_bytes=FixedRandom(),
    )
    assert client.send_image(
        "user@im.wechat",
        "ctx",
        plaintext,
        client_id="image-client-id",
    ) is None

    assert [request.url.path for request in calls] == [
        "/ilink/bot/getuploadurl",
        "/c2c/upload",
        "/ilink/bot/sendmessage",
    ]
    upload_body = json.loads(calls[0].content)
    assert upload_body["media_type"] == 1
    send_body = json.loads(calls[2].content)
    assert send_body["msg"]["client_id"] == "image-client-id"
    assert send_body["msg"]["context_token"] == "ctx"
    assert send_body["msg"]["item_list"] == [
        {
            "type": 2,
            "image_item": {
                "media": {
                    "encrypt_query_param": "download-image",
                    "aes_key": b64encode(FIXED_AES_KEY).decode("ascii"),
                    "encrypt_type": 1,
                },
                "mid_size": len(ciphertext),
            },
        }
    ]
```

- [ ] **Step 2: Run the image test and verify API failure**

Run: `PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_outbound_media.py::test_send_image_runs_complete_protocol_and_sends_exact_payload`

Expected: FAIL because `send_image` is missing.

- [ ] **Step 3: Extract `_send_item` and implement the shared media preparation path**

In `wechat_ilink/client.py`, import `_read_media_bytes` and `encrypt_wechat_media`. Extract the existing `send_message` request into:

```python
def _send_item(
    self,
    to_user_id: str,
    context_token: str,
    item: dict[str, Any],
    client_id: str = "",
) -> None:
    payload = {
        "msg": {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": client_id or f"staffdeck:{int(time.time() * 1000)}:{uuid4().hex[:8]}",
            "message_type": 2,
            "message_state": 2,
            "context_token": context_token,
            "item_list": [item],
        },
        "base_info": self._base_info(),
    }
    response = self._client.post(
        f"{self.base_url}/ilink/bot/sendmessage",
        headers=self._business_headers(),
        json=payload,
        timeout=20.0,
    )
    response.raise_for_status()
    data = response.json() if response.content else {}
    if isinstance(data, dict):
        errcode = data.get("errcode") or data.get("ret") or 0
        if errcode:
            raise WeChatApiError(int(errcode), str(data.get("errmsg") or ""))
```

Make `send_message` call:

```python
self._send_item(
    to_user_id,
    context_token,
    {"type": 1, "text_item": {"text": text}},
    client_id,
)
```

Add a preparation helper that validates injected randomness:

```python
def _prepare_outbound_media(self, data: object) -> tuple[bytes, bytes, str, bytes]:
    plaintext = _read_media_bytes(data)
    filekey_bytes = self._random_bytes(16)
    aes_key = self._random_bytes(16)
    if len(filekey_bytes) != 16 or len(aes_key) != 16:
        raise WeChatMediaError(
            WeChatErrorCode.MEDIA_ENCRYPTION_FAILED,
            "encrypt",
            "random source must return exactly 16 bytes",
        )
    ciphertext = encrypt_wechat_media(plaintext, aes_key)
    return plaintext, ciphertext, filekey_bytes.hex(), aes_key
```

- [ ] **Step 4: Implement `send_image` minimally**

```python
def send_image(
    self,
    to_user_id: str,
    context_token: str,
    data: object,
    *,
    client_id: str = "",
) -> None:
    plaintext, ciphertext, filekey, aes_key = self._prepare_outbound_media(data)
    upload_url = self._get_upload_url(
        to_user_id=to_user_id,
        media_type=1,
        plaintext=plaintext,
        ciphertext_size=len(ciphertext),
        filekey=filekey,
        aeskey=aes_key.hex(),
    )
    download_param = self._upload_media_to_cdn(upload_url, ciphertext)
    self._send_item(
        to_user_id,
        context_token,
        {
            "type": 2,
            "image_item": {
                "media": {
                    "encrypt_query_param": download_param,
                    "aes_key": base64.b64encode(aes_key).decode("ascii"),
                    "encrypt_type": 1,
                },
                "mid_size": len(ciphertext),
            },
        },
        client_id,
    )
```

- [ ] **Step 5: Run image and existing text payload tests**

Run: `PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_outbound_media.py::test_send_image_runs_complete_protocol_and_sends_exact_payload tests/test_client.py::test_send_message_payload tests/test_client.py::test_send_message_raises_on_errcode`

Expected: PASS; the text payload remains byte-for-byte structurally equivalent.

- [ ] **Step 6: Add failing end-to-end file test and filename validation tests**

Append:

```python
def test_send_file_uses_file_media_type_plaintext_length_and_filename() -> None:
    bodies: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ilink/bot/getuploadurl":
            bodies["upload"] = json.loads(request.content)
            return httpx.Response(200, json={"upload_full_url": CDN_URL})
        if request.url.host == "novac2c.cdn.weixin.qq.com":
            return httpx.Response(200, headers={"x-encrypted-param": "download-file"})
        bodies["send"] = json.loads(request.content)
        return httpx.Response(200, json={})

    client = WeChatClient(
        BASE_URL,
        "bot-token",
        transport=httpx.MockTransport(handler),
        random_bytes=FixedRandom(),
    )
    client.send_file(
        "user@im.wechat",
        "ctx",
        b"file-data",
        " report.pdf ",
        client_id="file-client-id",
    )

    assert bodies["upload"]["media_type"] == 3
    item = bodies["send"]["msg"]["item_list"][0]
    assert item["type"] == 4
    assert item["file_item"]["file_name"] == "report.pdf"
    assert item["file_item"]["len"] == str(len(b"file-data"))
    assert item["file_item"]["media"]["encrypt_query_param"] == "download-file"


@pytest.mark.parametrize("filename", ["", "   ", 123, None])
def test_send_file_rejects_invalid_filename_before_http(filename) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    client = WeChatClient(BASE_URL, transport=httpx.MockTransport(handler))
    with pytest.raises(WeChatMediaError) as raised:
        client.send_file("user", "ctx", b"data", filename)
    assert raised.value.code is WeChatErrorCode.INVALID_MEDIA_INPUT
    assert calls == 0
```

- [ ] **Step 7: Run file tests and verify API failure**

Run: `PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_outbound_media.py -k 'send_file'`

Expected: FAIL because `send_file` is missing.

- [ ] **Step 8: Implement `send_file`**

```python
def send_file(
    self,
    to_user_id: str,
    context_token: str,
    data: object,
    filename: str,
    *,
    client_id: str = "",
) -> None:
    if not isinstance(filename, str) or not filename.strip():
        raise WeChatMediaError(
            WeChatErrorCode.INVALID_MEDIA_INPUT,
            "read",
            "filename must be a non-empty string",
        )
    normalized_filename = filename.strip()
    plaintext, ciphertext, filekey, aes_key = self._prepare_outbound_media(data)
    upload_url = self._get_upload_url(
        to_user_id=to_user_id,
        media_type=3,
        plaintext=plaintext,
        ciphertext_size=len(ciphertext),
        filekey=filekey,
        aeskey=aes_key.hex(),
    )
    download_param = self._upload_media_to_cdn(upload_url, ciphertext)
    self._send_item(
        to_user_id,
        context_token,
        {
            "type": 4,
            "file_item": {
                "media": {
                    "encrypt_query_param": download_param,
                    "aes_key": base64.b64encode(aes_key).decode("ascii"),
                    "encrypt_type": 1,
                },
                "file_name": normalized_filename,
                "len": str(len(plaintext)),
            },
        },
        client_id,
    )
```

- [ ] **Step 9: Add and run no-HTTP-on-input-error plus random-source tests**

Append:

```python
def test_send_image_rejects_oversize_before_http() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    client = WeChatClient(BASE_URL, transport=httpx.MockTransport(handler))
    with pytest.raises(WeChatMediaError) as raised:
        client.send_image("user", "ctx", b"x" * (25 * 1024 * 1024 + 1))
    assert raised.value.code is WeChatErrorCode.MEDIA_TOO_LARGE
    assert calls == 0


def test_send_image_rejects_wrong_random_length_before_http() -> None:
    client = WeChatClient(BASE_URL, random_bytes=lambda size: b"short")
    with pytest.raises(WeChatMediaError) as raised:
        client.send_image("user", "ctx", b"image")
    assert raised.value.code is WeChatErrorCode.MEDIA_ENCRYPTION_FAILED
```

Run: `PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_outbound_media.py`

Expected: PASS.

- [ ] **Step 10: Run all client and media regression tests**

Run: `PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_client.py tests/test_media.py tests/test_outbound_media.py`

Expected: PASS.

- [ ] **Step 11: Commit public media workflows**

```bash
git add wechat_ilink/client.py tests/test_client.py tests/test_outbound_media.py
git commit -m "feat: send outbound images and files"
```

---

### Task 6: Version, README, and Tencent MIT attribution

**Files:**
- Modify: `pyproject.toml`
- Modify: `wechat_ilink/client.py`
- Modify: `README.md`
- Create: `NOTICE`
- Modify: `tests/test_client.py`

**Interfaces:**
- Consumes: all public interfaces completed in Tasks 1–5.
- Produces: documented v0.2.0 API and consistent wire/package version.

- [ ] **Step 1: Add a failing exact-version regression test**

Change `tests/test_client.py::test_session_expired_errcode_constant` or add beside it:

```python
def test_channel_version_is_v020() -> None:
    assert CHANNEL_VERSION == "0.2.0"
```

- [ ] **Step 2: Run the version test and verify current failure**

Run: `PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_client.py::test_channel_version_is_v020`

Expected: FAIL because the current value is `1.0.0`.

- [ ] **Step 3: Align package and channel versions**

Change:

```toml
# pyproject.toml
version = "0.2.0"
```

and:

```python
# wechat_ilink/client.py
CHANNEL_VERSION = "0.2.0"
```

- [ ] **Step 4: Run version and request-payload tests**

Run: `PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_client.py::test_channel_version_is_v020 tests/test_client.py::test_send_message_payload tests/test_outbound_media.py::test_get_upload_url_sends_exact_image_metadata`

Expected: PASS and both text/media requests carry `0.2.0`.

- [ ] **Step 5: Add the attribution notice**

Create `NOTICE` containing:

```text
wechat-ilink third-party notices

The outbound WeChat iLink media protocol implementation is based in part on
Tencent/openclaw-weixin:
https://github.com/Tencent/openclaw-weixin

Tencent is pleased to support the open source community by making
openclaw-weixin available.

Copyright (C) 2026 Tencent. All rights reserved.
Tencent/openclaw-weixin is licensed under the MIT License.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 6: Rewrite README public media documentation**

Update the opening coverage sentence to include outbound images/files. Correct installation examples to `pip install /path/to/wechat-ilink`, editable install likewise, and the test command to:

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider
```

Add this minimal usage section:

```python
from io import BytesIO

with WeChatClient(BASE_URL, bot_token) as client:
    client.send_image(to_user_id, context_token, image_bytes)
    client.send_file(
        to_user_id,
        context_token,
        BytesIO(report_bytes),
        "report.pdf",
        client_id="delivery-42",
    )
```

Document explicitly:

- `data` accepts bytes-like objects or a binary stream from its current position.
- The library never opens `filename` as a path and never closes caller-owned streams.
- Empty media and plaintext over 25 MiB fail before HTTP.
- `WeChatMediaError.code` is stable; iLink `ret/errcode` remains `WeChatApiError.errcode`.
- Automated tests do not require QR login, a WeChat account, token, or network.
- Live verification is a separate opt-in manual activity.
- v0.2.0 does not support video, native voice, thumbnails, captions, or batches.
- The outbound protocol was adapted from Tencent/openclaw-weixin under MIT; point readers to `NOTICE`.

- [ ] **Step 7: Check documentation and metadata mechanically**

Run:

```bash
rg -n '1\.0\.0|channels/wechat|StaffDeck|FeiBot' README.md pyproject.toml wechat_ilink NOTICE
```

Expected: no stale version/path or host-specific public documentation. A historical interoperability comment in `crypto.py` may remain only if it describes the existing Fernet wire compatibility and does not create a runtime dependency.

Run:

```bash
python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])'
```

Expected: exactly `0.2.0`.

- [ ] **Step 8: Run the complete offline suite**

Run: `PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider`

Expected: all tests PASS; no test performs a real network request because every outbound HTTP test supplies `MockTransport`.

- [ ] **Step 9: Commit version, docs, and attribution**

```bash
git add pyproject.toml wechat_ilink/client.py README.md NOTICE tests/test_client.py
git commit -m "docs: prepare outbound media v0.2.0"
```

---

### Task 7: Final verification and scope audit

**Files:**
- Inspect: all files changed in Tasks 1–6
- Test: complete `tests/` suite

**Interfaces:**
- Consumes: the complete v0.2.0 implementation.
- Produces: evidence that the accepted specification is implemented without unrelated changes.

- [ ] **Step 1: Inspect final change scope**

Run:

```bash
git status --short
git diff --stat 052ab28..HEAD
git diff --check 052ab28..HEAD
```

Expected: only the files listed by this plan changed; `git diff --check` prints nothing.

- [ ] **Step 2: Verify every media HTTP test injects a transport**

Run:

```bash
rg -n 'WeChatClient\(' tests/test_outbound_media.py
rg -n 'MockTransport' tests/test_outbound_media.py
```

Inspect each client that reaches `_get_upload_url`, `_upload_media_to_cdn`, `send_image`, or `send_file`; each must have the relevant mocked transport. Pure validation tests that fail before HTTP may omit it.

- [ ] **Step 3: Run the complete offline test suite twice**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider
```

Expected: both runs PASS with the same test count, demonstrating deterministic isolation.

- [ ] **Step 4: Verify package import and public surface**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -c 'from wechat_ilink import WeChatClient, WeChatErrorCode, WeChatMediaError, aes_ecb_padded_size, encrypt_wechat_media; assert aes_ecb_padded_size(16) == 32; print("public API ok")'
```

Expected: `public API ok`.

- [ ] **Step 5: Inspect commits and confirm prohibited actions were not taken**

Run:

```bash
git log --oneline --decorate 052ab28..HEAD
git tag --points-at HEAD
git status --short --branch
```

Expected: focused local commits, no tag at `HEAD`, no push-side effects, and no changes outside `wechat-ilink`.

- [ ] **Step 6: Handle any verification defect through its owning task**

If Steps 1–5 reveal a defect, return to the task that owns that behavior, add the concrete regression test specified by that task's test file, reproduce the failure, apply the minimal correction, rerun Task 7 from Step 1, and commit only the owning task's listed files. If no correction is needed, do not create an empty commit.
