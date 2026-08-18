from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class InboundAttachment:
    """Normalized inbound attachment descriptor (in-memory only).

    Filled during normalize; ``download_params`` carries everything needed to
    fetch the raw bytes later (context_token / full_url / aes_key / sizes).
    """

    media_id: str
    kind: str  # "image" | "file"
    filename: str = ""
    content_type: str = ""
    size: int = 0
    download_params: dict[str, Any] = field(default_factory=dict)


@dataclass
class InboundMessage:
    """Normalized inbound message for the WeChat iLink channel."""

    event_id: str
    from_user_id: str
    to_user_id: str
    session_id: str
    group_id: str
    # 投递回话锚点:微信 iLink 为 context_token
    context_token: str
    text: str
    is_group: bool
    raw: dict[str, Any]
    # 入站附件列表(图片/文件);空列表表示纯文本消息。
    attachments: list[InboundAttachment] = field(default_factory=list)

    @property
    def conv_key(self) -> str:
        return self.group_id or self.session_id

    @property
    def external_conv_id(self) -> str:
        if self.is_group:
            return f"wechat_group_{self.conv_key}"
        return f"wechat_p2p_{self.from_user_id}"


def is_self_message(msg: dict[str, Any], ilink_bot_id: str = "") -> bool:
    if msg.get("message_type") == 2:
        return True
    from_user_id = str(msg.get("from_user_id") or "").strip()
    return bool(ilink_bot_id) and from_user_id == ilink_bot_id


def extract_message_text(msg: dict[str, Any]) -> str:
    items = msg.get("item_list")
    if isinstance(items, list):
        parts: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("type") == 1:
                text_item = item.get("text_item")
                if isinstance(text_item, dict):
                    value = str(text_item.get("text") or "").strip()
                    if value:
                        parts.append(value)
            elif item.get("type") == 3:
                # 语音消息:微信侧已转好的文字在 voice_item.text
                voice_item = item.get("voice_item")
                if isinstance(voice_item, dict):
                    value = str(voice_item.get("text") or "").strip()
                    if value:
                        parts.append(value)
        if parts:
            return "\n".join(parts)
    return str(msg.get("text") or msg.get("content") or "").strip()


def extract_message_attachments(msg: dict[str, Any]) -> list[InboundAttachment]:
    """Extract assumed iLink image/file item descriptors."""
    items = msg.get("item_list")
    if not isinstance(items, list):
        return []
    context_token = str(msg.get("context_token") or "").strip()
    attachments: list[InboundAttachment] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == 2:
            info = item.get("image_item") or {}
            media = info.get("media") or {}
            full_url = str(media.get("full_url") or "").strip() if isinstance(media, dict) else ""
            media_id = str(
                info.get("media_id")
                or info.get("file_id")
                or (media.get("media_id") if isinstance(media, dict) else "")
                or full_url
            ).strip()
            if media_id:
                message_id = str(msg.get("message_id") or msg.get("msg_id") or media_id).strip()
                download_params = {"context_token": context_token}
                if full_url:
                    download_params.update(
                        {
                            "full_url": full_url,
                            "encrypt_query_param": str(
                                media.get("encrypt_query_param") or ""
                            ).strip(),
                            "aes_key": str(media.get("aes_key") or info.get("aeskey") or "").strip(),
                            # full_url may return a higher-resolution variant with channel
                            # trailer bytes, so these sizes are only a pre-download limit hint.
                            "declared_size": max(
                                int(info.get("mid_size") or 0),
                                int(info.get("hd_size") or 0),
                            ),
                        }
                    )
                attachments.append(
                    InboundAttachment(
                        media_id=media_id,
                        kind="image",
                        filename=f"{message_id}.jpg",
                        content_type="image/jpeg",
                        download_params=download_params,
                    )
                )
        elif item_type == 4:
            info = item.get("file_item") or {}
            media = info.get("media") or {}
            full_url = str(media.get("full_url") or "").strip() if isinstance(media, dict) else ""
            media_id = str(
                info.get("media_id")
                or info.get("file_id")
                or (media.get("media_id") if isinstance(media, dict) else "")
                or full_url
            ).strip()
            if media_id:
                download_params = {"context_token": context_token}
                if full_url:
                    download_params.update(
                        {
                            "full_url": full_url,
                            "encrypt_query_param": str(
                                media.get("encrypt_query_param") or ""
                            ).strip(),
                            "aes_key": str(media.get("aes_key") or "").strip(),
                            "expected_size": int(info.get("len") or 0),
                        }
                    )
                attachments.append(
                    InboundAttachment(
                        media_id=media_id,
                        kind="file",
                        filename=str(info.get("file_name") or info.get("name") or media_id).strip(),
                        download_params=download_params,
                    )
                )
    return attachments


def normalize_wechat_message(msg: dict[str, Any], *, ilink_bot_id: str = "") -> InboundMessage | None:
    """归一化 getupdates 消息；自身消息/无文本/无 context_token 返回 None（丢弃）。"""
    if not isinstance(msg, dict) or is_self_message(msg, ilink_bot_id):
        return None
    from_user_id = str(msg.get("from_user_id") or "").strip()
    if not from_user_id:
        return None
    context_token = str(msg.get("context_token") or "").strip()
    text = extract_message_text(msg)
    attachments = extract_message_attachments(msg)
    items = msg.get("item_list")
    if isinstance(items, list) and logger.isEnabledFor(logging.DEBUG):
        nested_keys = []
        for item in items:
            if not isinstance(item, dict):
                continue
            for field_name in ("image_item", "file_item", "voice_item"):
                nested = item.get(field_name)
                if isinstance(nested, dict):
                    entry = {
                        "field": field_name,
                        "keys": sorted(nested.keys()),
                        "value_types": {
                            key: type(value).__name__ for key, value in nested.items()
                        },
                    }
                    media = nested.get("media")
                    if isinstance(media, dict):
                        entry["media_keys"] = sorted(media.keys())
                        entry["media_value_types"] = {
                            key: type(value).__name__ for key, value in media.items()
                        }
                    nested_keys.append(entry)
        logger.debug(
            "微信入站消息附件诊断 message_id=%s item_types=%s item_keys=%s "
            "nested_keys=%s recognized_attachments=%s has_text=%s",
            str(msg.get("message_id") or msg.get("msg_id") or "").strip(),
            [item.get("type") for item in items if isinstance(item, dict)],
            [sorted(item.keys()) for item in items if isinstance(item, dict)],
            nested_keys,
            len(attachments),
            bool(text),
        )
    if not context_token or (not text and not attachments):
        return None
    event_id = str(msg.get("message_id") or msg.get("msg_id") or msg.get("client_id") or "").strip()
    if not event_id:
        return None
    session_id = str(msg.get("session_id") or "").strip()
    group_id = str(msg.get("group_id") or "").strip()
    # p2p 会话 session_id 形如 "user#bot"；群聊优先看 group_id，兜底用无 # 的 session_id
    is_group = bool(group_id) or (
        bool(session_id) and "#" not in session_id and session_id != from_user_id
    )
    return InboundMessage(
        event_id=event_id,
        from_user_id=from_user_id,
        to_user_id=str(msg.get("to_user_id") or "").strip(),
        session_id=session_id,
        group_id=group_id,
        context_token=context_token,
        text=text,
        is_group=is_group,
        raw=msg,
        attachments=attachments,
    )
