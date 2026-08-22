from __future__ import annotations

from enum import StrEnum


class WeChatApiError(Exception):
    """Error returned by the iLink business API (errcode embedded in the body)."""

    def __init__(self, errcode: int, message: str):
        super().__init__(f"微信 iLink 接口错误 errcode={errcode}: {message}")
        self.errcode = errcode


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
    """Structured local/protocol failure of outbound media; `.code` is stable."""

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
