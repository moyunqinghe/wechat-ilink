from __future__ import annotations


class WeChatApiError(Exception):
    """Error returned by the iLink business API (errcode embedded in the body)."""

    def __init__(self, errcode: int, message: str):
        super().__init__(f"微信 iLink 接口错误 errcode={errcode}: {message}")
        self.errcode = errcode
