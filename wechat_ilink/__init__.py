from __future__ import annotations

from .client import (
    CHANNEL_VERSION,
    GETUPDATES_TIMEOUT_SECONDS,
    SESSION_EXPIRED_ERRCODE,
    WeChatClient,
    random_wechat_uin,
)
from .crypto import decrypt_secret, derive_fernet_key, encrypt_secret
from .errors import WeChatApiError
from .media import (
    MAX_CHANNEL_MEDIA_BYTES,
    MAX_ENCRYPTED_CHANNEL_MEDIA_BYTES,
    decrypt_wechat_media,
    download_media_url,
    ensure_channel_media_size,
)
from .normalize import (
    InboundAttachment,
    InboundMessage,
    extract_message_attachments,
    extract_message_text,
    is_self_message,
    normalize_wechat_message,
)
from .security import WECHAT_ALLOWED_HOSTS, sanitize_wechat_baseurl, validate_wechat_host
from .text import WECHAT_TEXT_LIMIT, split_channel_text, split_wechat_text

__all__ = [
    "CHANNEL_VERSION",
    "GETUPDATES_TIMEOUT_SECONDS",
    "MAX_CHANNEL_MEDIA_BYTES",
    "MAX_ENCRYPTED_CHANNEL_MEDIA_BYTES",
    "SESSION_EXPIRED_ERRCODE",
    "WECHAT_ALLOWED_HOSTS",
    "WECHAT_TEXT_LIMIT",
    "InboundAttachment",
    "InboundMessage",
    "WeChatApiError",
    "WeChatClient",
    "decrypt_secret",
    "decrypt_wechat_media",
    "derive_fernet_key",
    "download_media_url",
    "encrypt_secret",
    "ensure_channel_media_size",
    "extract_message_attachments",
    "extract_message_text",
    "is_self_message",
    "normalize_wechat_message",
    "random_wechat_uin",
    "sanitize_wechat_baseurl",
    "split_channel_text",
    "split_wechat_text",
    "validate_wechat_host",
]
