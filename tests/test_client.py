import base64
import json

import httpx

from wechat_ilink import WeChatClient, normalize_wechat_message, random_wechat_uin
from wechat_ilink.client import CHANNEL_VERSION

BASE_URL = "https://ilinkai.weixin.qq.com"


def _client(handler) -> WeChatClient:
    return WeChatClient(BASE_URL, "bot_token_x", transport=httpx.MockTransport(handler))


def _text_message(**overrides) -> dict:
    msg = {
        "seq": 429,
        "message_id": 9812451782375,
        "from_user_id": "user_ab12cd34@im.wechat",
        "to_user_id": "bot_1@im.bot",
        "client_id": "wx-msg-1",
        "session_id": "user_ab12cd34@im.wechat#bot_1@im.bot",
        "message_type": 1,
        "message_state": 2,
        "context_token": "ctx_token_1",
        "item_list": [{"type": 1, "text_item": {"text": "你好"}}],
    }
    msg.update(overrides)
    return msg


def test_random_wechat_uin_format() -> None:
    first, second = random_wechat_uin(), random_wechat_uin()
    assert first != second
    decoded = base64.b64decode(first).decode("utf-8")
    assert decoded.isdigit()
    assert 0 <= int(decoded) <= 2**32 - 1


def test_get_updates_request_and_parse() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["auth_type"] = request.headers.get("AuthorizationType")
        captured["uin"] = request.headers.get("X-WECHAT-UIN")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "ret": 0,
                "msgs": [_text_message()],
                "get_updates_buf": "next_cursor",
                "longpolling_timeout_ms": 35000,
            },
        )

    client = _client(handler)
    resp = client.get_updates("cur_cursor")

    assert captured["url"] == f"{BASE_URL}/ilink/bot/getupdates"
    assert captured["authorization"] == "Bearer bot_token_x"
    assert captured["auth_type"] == "ilink_bot_token"
    assert captured["uin"]
    assert captured["body"]["get_updates_buf"] == "cur_cursor"
    assert captured["body"]["base_info"]["channel_version"]
    assert resp["get_updates_buf"] == "next_cursor"

    inbound = normalize_wechat_message(resp["msgs"][0], ilink_bot_id="bot_1@im.bot")
    assert inbound is not None
    assert inbound.event_id == "9812451782375"
    assert inbound.text == "你好"
    assert inbound.context_token == "ctx_token_1"
    assert inbound.is_group is False
    assert inbound.external_conv_id == "wechat_p2p_user_ab12cd34@im.wechat"


def test_download_media_request() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"image-bytes", headers={"content-type": "image/jpeg"})

    data = _client(handler).download_media("ctx-1", "media-1")
    assert data == b"image-bytes"
    assert captured["url"] == f"{BASE_URL}/ilink/bot/downloadmedia"
    assert captured["body"]["context_token"] == "ctx-1"
    assert captured["body"]["media_id"] == "media-1"


def test_send_message_payload() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    client = _client(handler)
    client.send_message("user_1@im.wechat", "ctx_token_1", "回复文本")

    assert captured["url"] == f"{BASE_URL}/ilink/bot/sendmessage"
    msg = captured["body"]["msg"]
    assert msg["to_user_id"] == "user_1@im.wechat"
    assert msg["context_token"] == "ctx_token_1"
    assert msg["message_type"] == 2
    assert msg["message_state"] == 2
    assert msg["client_id"]
    assert msg["item_list"] == [{"type": 1, "text_item": {"text": "回复文本"}}]
    assert captured["body"]["base_info"]["channel_version"]


def test_send_message_raises_on_errcode() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ret": -2, "errmsg": "参数错误"})

    client = _client(handler)
    try:
        client.send_message("user_1", "ctx", "hi")
        raised = False
    except Exception as exc:
        raised = True
        assert "-2" in str(exc)
    assert raised


def test_qrcode_endpoints() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        captured[path] = request
        if path == "/ilink/bot/get_bot_qrcode":
            assert request.method == "POST"
            assert request.url.params["bot_type"] == "3"
            assert json.loads(request.content) == {"local_token_list": ["old_token_1"]}
            return httpx.Response(200, json={"qrcode": "qrc_1", "qrcode_img_content": "https://weixin.qq.com/x/abc"})
        if path == "/ilink/bot/get_qrcode_status":
            assert request.method == "GET"
            assert request.headers.get("iLink-App-ClientVersion") == "1"
            return httpx.Response(
                200,
                json={
                    "status": "confirmed",
                    "bot_token": "tok",
                    "ilink_bot_id": "bot@im.bot",
                    "baseurl": BASE_URL,
                },
            )
        return httpx.Response(404)

    client = _client(handler)
    qrcode = client.get_bot_qrcode(local_token_list=["old_token_1"])
    assert qrcode["qrcode"] == "qrc_1"
    status = client.get_qrcode_status("qrc_1", verify_code="8823")
    assert status["status"] == "confirmed"
    assert status["ilink_bot_id"] == "bot@im.bot"
    assert captured["/ilink/bot/get_qrcode_status"].url.params["qrcode"] == "qrc_1"
    assert captured["/ilink/bot/get_qrcode_status"].url.params["verify_code"] == "8823"


def test_get_bot_qrcode_defaults_to_empty_token_list() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"qrcode": "qrc_1"})

    client = _client(handler)
    client.get_bot_qrcode()
    assert captured["body"] == {"local_token_list": []}


def test_get_qrcode_status_without_verify_code() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"status": "wait"})

    client = _client(handler)
    assert client.get_qrcode_status("qrc_1")["status"] == "wait"
    assert "verify_code" not in captured["params"]


def test_get_config_and_send_typing_payloads() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured[request.url.path] = json.loads(request.content)
        if request.url.path == "/ilink/bot/getconfig":
            return httpx.Response(200, json={"ret": 0, "typing_ticket": "ticket_1"})
        return httpx.Response(200, json={"ret": 0})

    client = _client(handler)
    data = client.get_config("user_1@im.wechat", "ctx_1")
    assert data["typing_ticket"] == "ticket_1"
    assert captured["/ilink/bot/getconfig"] == {
        "ilink_user_id": "user_1@im.wechat",
        "context_token": "ctx_1",
        "base_info": {"channel_version": CHANNEL_VERSION},
    }

    client.send_typing("user_1@im.wechat", "ticket_1", 1)
    assert captured["/ilink/bot/sendtyping"] == {
        "ilink_user_id": "user_1@im.wechat",
        "typing_ticket": "ticket_1",
        "status": 1,
        "base_info": {"channel_version": CHANNEL_VERSION},
    }


def test_session_expired_errcode_constant() -> None:
    from wechat_ilink import SESSION_EXPIRED_ERRCODE

    assert SESSION_EXPIRED_ERRCODE == -14


def test_client_accepts_separate_cdn_mock_transport() -> None:
    business = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    cdn = httpx.MockTransport(lambda request: httpx.Response(200))
    client = WeChatClient(BASE_URL, "token", transport=business, cdn_transport=cdn)
    assert client._cdn_client is not client._client
    client.close()
