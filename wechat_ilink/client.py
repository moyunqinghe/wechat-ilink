from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode, urlparse
from uuid import uuid4

import httpx

from .errors import WeChatApiError, WeChatErrorCode, WeChatMediaError
from .media import MAX_CHANNEL_MEDIA_BYTES
from .media import download_media_url as _download_media_url
from .security import validate_wechat_host

logger = logging.getLogger(__name__)

CHANNEL_VERSION = "1.0.0"
GETUPDATES_TIMEOUT_SECONDS = 40.0
# errcode -14:会话疑似过期(token 被 iLink 拒收);调用方应走恢复/重新扫码流程
SESSION_EXPIRED_ERRCODE = -14
WECHAT_CDN_UPLOAD_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c/upload"


def _build_cdn_upload_url(upload_param: str, filekey: str) -> str:
    """Official fallback CDN URL assembly from upload_param + filekey."""
    return f"{WECHAT_CDN_UPLOAD_BASE_URL}?{urlencode({'encrypted_query_param': upload_param, 'filekey': filekey})}"


def random_wechat_uin() -> str:
    """X-WECHAT-UIN：随机 uint32 的十进制字符串再 base64，每次请求重新生成。"""
    value = int.from_bytes(os.urandom(4), "big")
    return base64.b64encode(str(value).encode("utf-8")).decode("utf-8")


class WeChatClient:
    """腾讯 iLink 协议 HTTP 客户端（扫码绑定 + getupdates 收 + sendmessage 发）。"""

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

    def _business_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {self.bot_token}",
            "X-WECHAT-UIN": random_wechat_uin(),
        }

    @staticmethod
    def _base_info() -> dict[str, Any]:
        return {"channel_version": CHANNEL_VERSION}

    def get_bot_qrcode(self, local_token_list: list[str] | None = None) -> dict[str, Any]:
        # local_token_list:本地已有 bot_token 列表(官方签名,最多 10 个)
        resp = self._client.post(
            f"{self.base_url}/ilink/bot/get_bot_qrcode",
            params={"bot_type": 3},
            json={"local_token_list": list(local_token_list or [])[:10]},
            timeout=20.0,
        )
        resp.raise_for_status()
        return dict(resp.json() or {})

    def get_qrcode_status(
        self,
        qrcode: str,
        *,
        verify_code: str | None = None,
        timeout_seconds: float = 35.0,
    ) -> dict[str, Any]:
        params = {"qrcode": qrcode}
        if verify_code:
            params["verify_code"] = verify_code
        resp = self._client.get(
            f"{self.base_url}/ilink/bot/get_qrcode_status",
            params=params,
            headers={"iLink-App-ClientVersion": "1"},
            timeout=timeout_seconds + 5.0,
        )
        resp.raise_for_status()
        return dict(resp.json() or {})

    def get_updates(
        self,
        get_updates_buf: str,
        *,
        timeout_seconds: float = GETUPDATES_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        resp = self._client.post(
            f"{self.base_url}/ilink/bot/getupdates",
            headers=self._business_headers(),
            json={"get_updates_buf": get_updates_buf, "base_info": self._base_info()},
            timeout=timeout_seconds + 5.0,
        )
        resp.raise_for_status()
        return dict(resp.json() or {})

    def send_message(self, to_user_id: str, context_token: str, text: str, client_id: str = "") -> None:
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                # 确定性幂等:同一投递的重试复用同一 client_id;未指定时保持随机
                "client_id": client_id or f"staffdeck:{int(time.time() * 1000)}:{uuid4().hex[:8]}",
                "message_type": 2,
                "message_state": 2,
                "context_token": context_token,
                "item_list": [{"type": 1, "text_item": {"text": text}}],
            },
            "base_info": self._base_info(),
        }
        resp = self._client.post(
            f"{self.base_url}/ilink/bot/sendmessage",
            headers=self._business_headers(),
            json=payload,
            timeout=20.0,
        )
        resp.raise_for_status()
        data = resp.json() if resp.content else {}
        if isinstance(data, dict):
            errcode = data.get("errcode") or data.get("ret") or 0
            if errcode:
                raise WeChatApiError(int(errcode), str(data.get("errmsg") or ""))

    def get_config(self, ilink_user_id: str, context_token: str = "") -> dict[str, Any]:
        resp = self._client.post(
            f"{self.base_url}/ilink/bot/getconfig",
            headers=self._business_headers(),
            json={
                "ilink_user_id": ilink_user_id,
                "context_token": context_token,
                "base_info": self._base_info(),
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        return dict(resp.json() or {})

    def send_typing(self, ilink_user_id: str, typing_ticket: str, status: int = 1) -> None:
        # status: 1=正在输入 2=取消输入
        resp = self._client.post(
            f"{self.base_url}/ilink/bot/sendtyping",
            headers=self._business_headers(),
            json={
                "ilink_user_id": ilink_user_id,
                "typing_ticket": typing_ticket,
                "status": status,
                "base_info": self._base_info(),
            },
            timeout=15.0,
        )
        resp.raise_for_status()

    def download_media(self, context_token: str, media_id: str) -> bytes:
        request = self._client.build_request(
            "POST",
            f"{self.base_url}/ilink/bot/downloadmedia",
            headers=self._business_headers(),
            json={
                "context_token": context_token,
                "media_id": media_id,
                "base_info": self._base_info(),
            },
        )
        response = self._client.send(request, stream=True)
        try:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "application/json" not in content_type.lower():
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes(64 * 1024):
                    total += len(chunk)
                    if total > MAX_CHANNEL_MEDIA_BYTES:
                        raise ValueError("微信媒体超过大小上限")
                    chunks.append(chunk)
                return b"".join(chunks)
            chunks = []
            total = 0
            for chunk in response.iter_bytes(64 * 1024):
                total += len(chunk)
                if total > MAX_CHANNEL_MEDIA_BYTES:
                    raise ValueError("微信媒体响应超过大小上限")
                chunks.append(chunk)
            raw = b"".join(chunks)
        finally:
            response.close()
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise WeChatApiError(-1, "下载响应格式无效") from exc
        errcode = int(data.get("errcode") or data.get("ret") or 0)
        if errcode:
            raise WeChatApiError(errcode, str(data.get("errmsg") or ""))
        raise WeChatApiError(-1, "下载响应缺少二进制内容")

    def download_media_url(
        self,
        full_url: str,
        *,
        aes_key: str = "",
        expected_size: int = 0,
    ) -> bytes:
        """Download the CDN URL supplied by an iLink image item."""
        return _download_media_url(full_url, aes_key=aes_key, expected_size=expected_size)

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

    def close(self) -> None:
        if self._cdn_client is not self._client:
            self._cdn_client.close()
        self._client.close()

    def __enter__(self) -> WeChatClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
