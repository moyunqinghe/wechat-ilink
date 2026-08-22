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
