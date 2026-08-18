import logging

from wechat_ilink import (
    extract_message_text,
    is_self_message,
    normalize_wechat_message,
)


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


BASE_URL = "https://ilinkai.weixin.qq.com"


def test_normalize_image_and_file_items() -> None:
    image = normalize_wechat_message(
        _text_message(
            item_list=[{"type": 2, "image_item": {"media_id": "image-1"}}],
        )
    )
    assert image is not None
    assert image.attachments[0].media_id == "image-1"
    assert image.attachments[0].download_params["context_token"] == "ctx_token_1"

    file = normalize_wechat_message(
        _text_message(
            item_list=[
                {
                    "type": 4,
                    "file_item": {
                        "file_name": "a.txt",
                        "len": "12",
                        "md5": "md5",
                        "media": {
                            "aes_key": "aes",
                            "encrypt_query_param": "encrypted",
                            "full_url": f"{BASE_URL}/c2c/download?encrypted_query_param=encrypted&taskid=task",
                        },
                    },
                }
            ],
        )
    )
    assert file is not None
    assert file.attachments[0].media_id.endswith("/c2c/download?encrypted_query_param=encrypted&taskid=task")
    assert file.attachments[0].filename == "a.txt"


def test_normalize_text_message_does_not_emit_attachment_warning(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="wechat_ilink.normalize"):
        assert normalize_wechat_message(_text_message()) is not None

    assert "附件诊断" not in caplog.text


def test_normalize_actual_image_media_shape() -> None:
    inbound = normalize_wechat_message(
        _text_message(
            item_list=[
                {
                    "type": 2,
                    "image_item": {
                        "aeskey": "aes",
                        "media": {
                            "aes_key": "aes",
                            "encrypt_query_param": "encrypted",
                            "full_url": f"{BASE_URL}/c2c/download?encrypted_query_param=encrypted&taskid=task",
                        },
                        "mid_size": 10,
                    },
                }
            ],
        )
    )
    assert inbound is not None
    attachment = inbound.attachments[0]
    assert attachment.kind == "image"
    assert attachment.download_params["full_url"].endswith("taskid=task")
    assert attachment.download_params["aes_key"] == "aes"
    assert attachment.download_params["declared_size"] == 10
    assert "expected_size" not in attachment.download_params


def test_normalize_drops_self_messages() -> None:
    assert is_self_message(_text_message(message_type=2), "bot_1@im.bot") is True
    assert is_self_message(_text_message(from_user_id="bot_1@im.bot"), "bot_1@im.bot") is True
    assert normalize_wechat_message(_text_message(message_type=2), ilink_bot_id="bot_1@im.bot") is None
    assert normalize_wechat_message(
        _text_message(from_user_id="bot_1@im.bot"), ilink_bot_id="bot_1@im.bot"
    ) is None


def test_normalize_drops_non_text_or_missing_context() -> None:
    image_only = _text_message(item_list=[{"type": 2, "image_item": {"url": "x"}}])
    assert normalize_wechat_message(image_only) is None
    assert normalize_wechat_message(_text_message(context_token="")) is None
    assert normalize_wechat_message(_text_message(item_list=[])) is None


def test_voice_message_text_is_extracted() -> None:
    voice_msg = _text_message(
        item_list=[
            {
                "type": 3,
                "voice_item": {
                    "media": {"encrypt_query_param": "x"},
                    "encode_type": 6,
                    "text": "我下午三点到。",
                },
            }
        ]
    )
    inbound = normalize_wechat_message(voice_msg)
    assert inbound is not None
    assert inbound.text == "我下午三点到。"


def test_voice_message_without_text_is_dropped() -> None:
    voice_msg = _text_message(item_list=[{"type": 3, "voice_item": {"encode_type": 6}}])
    assert normalize_wechat_message(voice_msg) is None


def test_text_and_voice_items_join() -> None:
    mixed = _text_message(
        item_list=[
            {"type": 1, "text_item": {"text": "先听语音"}},
            {"type": 3, "voice_item": {"text": "语音内容"}},
        ]
    )
    assert extract_message_text(mixed) == "先听语音\n语音内容"
    inbound = normalize_wechat_message(mixed)
    assert inbound is not None
    assert inbound.text == "先听语音\n语音内容"


def test_normalize_group_message() -> None:
    group_msg = _text_message(group_id="room_123456", session_id="room_123456")
    inbound = normalize_wechat_message(group_msg)
    assert inbound is not None
    assert inbound.is_group is True
    assert inbound.conv_key == "room_123456"
    assert inbound.external_conv_id == "wechat_group_room_123456"

    # 兜底:无 group_id 时,不含 # 且不同于发言人的 session_id 视为群
    fallback_group = _text_message(group_id=None, session_id="room_999")
    inbound = normalize_wechat_message(fallback_group)
    assert inbound is not None and inbound.is_group is True

    # p2p 会话 session_id 形如 "user#bot",不能误判为群
    p2p = normalize_wechat_message(_text_message())
    assert p2p is not None and p2p.is_group is False
    assert p2p.external_conv_id == "wechat_p2p_user_ab12cd34@im.wechat"


def test_event_id_fallback_order() -> None:
    by_msg_id = normalize_wechat_message(_text_message(message_id=None, msg_id="mid_1"))
    assert by_msg_id is not None and by_msg_id.event_id == "mid_1"
    by_client_id = normalize_wechat_message(_text_message(message_id=None, msg_id=None))
    assert by_client_id is not None and by_client_id.event_id == "wx-msg-1"
    assert normalize_wechat_message(
        _text_message(message_id=None, msg_id=None, client_id=None)
    ) is None
