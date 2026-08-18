from __future__ import annotations

import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# 腾讯官方接入域名:业务请求携带 bot_token,redirect/baseurl 必须限制在官方域内
WECHAT_ALLOWED_HOSTS = ("ilinkai.weixin.qq.com",)


def validate_wechat_host(host: str) -> bool:
    """校验微信接入域名:精确命中官方域或为 *.weixin.qq.com 子域(防 weixin.qq.com.evil.com 绕过)。"""
    normalized = (host or "").strip().lower().split(":", 1)[0]
    if not normalized:
        return False
    return normalized in WECHAT_ALLOWED_HOSTS or normalized.endswith(".weixin.qq.com")


def sanitize_wechat_baseurl(url: str, *, default: str) -> str:
    """把 redirect/confirmed 下发的 baseurl 规范为 https://{host}(丢弃 path/query);非法回退 default。"""
    host = ""
    try:
        parsed = urlparse(url)
        if parsed.scheme in {"http", "https"}:
            host = (parsed.hostname or "").lower()
    except ValueError:
        host = ""
    if host and validate_wechat_host(host):
        return f"https://{host}"
    logger.warning("微信 baseurl 域名不受信任,回退默认接入地址: %s", url)
    return default
