from __future__ import annotations

import asyncio
import base64
import json
import logging
import shutil
import ssl
import subprocess
import time
from urllib.parse import urlparse, urlunparse

import certifi
import httpx
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .errors import WeChatApiError
from .security import validate_wechat_host

try:
    # aiohttp 仅作为 httpx 失败时的可选备选路径,不是硬依赖(见 extras: aiohttp)
    import aiohttp
except ImportError:  # pragma: no cover - depends on environment
    aiohttp = None

logger = logging.getLogger(__name__)

MAX_CHANNEL_MEDIA_BYTES = 25 * 1024 * 1024
MAX_ENCRYPTED_CHANNEL_MEDIA_BYTES = MAX_CHANNEL_MEDIA_BYTES + 32

WECHAT_MEDIA_DOWNLOAD_ATTEMPTS = 4
WECHAT_CURL_DOWNLOAD_ATTEMPTS = 6


def ensure_channel_media_size(size: int, *, encrypted: bool = False) -> None:
    limit = MAX_ENCRYPTED_CHANNEL_MEDIA_BYTES if encrypted else MAX_CHANNEL_MEDIA_BYTES
    if size > limit:
        raise ValueError(f"渠道附件超过大小上限: size={size} limit={limit}")


def decrypt_wechat_media(data: bytes, aes_key: str, *, expected_size: int = 0) -> bytes:
    """Decrypt iLink CDN media using its fixed AES-ECB/PKCS#7 wire format."""
    if not aes_key:
        return data
    try:
        decoded = base64.b64decode(aes_key, validate=True)
        key = bytes.fromhex(decoded.decode("ascii"))
        if len(key) not in {16, 24, 32} or len(data) % 16:
            raise ValueError
        # AES-ECB is mandated by the third-party iLink CDN payload format. This
        # compatibility path only decrypts provider media; it is not reusable storage crypto.
        ecb_mode = modes.ECB()  # lgtm[py/weak-cryptographic-algorithm]
        decryptor = Cipher(algorithms.AES(key), ecb_mode).decryptor()
        padded = decryptor.update(data) + decryptor.finalize()
        unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
        decrypted = unpadder.update(padded) + unpadder.finalize()
    except (ValueError, TypeError) as exc:
        raise WeChatApiError(-1, "微信媒体解密失败") from exc
    if expected_size > 0 and len(decrypted) != expected_size:
        raise WeChatApiError(
            -1,
            f"微信媒体解密后大小不匹配 expected={expected_size} actual={len(decrypted)}",
        )
    return decrypted


async def _download_wechat_cdn_httpx(url: str) -> tuple[bytes, str]:
    """Primary CDN download path (some CDN nodes reject other TLS stacks)."""
    async with httpx.AsyncClient(
        verify=certifi.where(),
        http2=False,
        timeout=15.0,
    ) as client, client.stream("GET", url) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes(64 * 1024):
            total += len(chunk)
            if total > MAX_ENCRYPTED_CHANNEL_MEDIA_BYTES:
                raise ValueError("微信媒体密文超过大小上限")
            chunks.append(chunk)
        return b"".join(chunks), content_type


async def _download_wechat_cdn_aiohttp(url: str) -> tuple[bytes, str]:
    """Optional fallback for CDN nodes that reject httpx's TLS handshake."""
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    for attempt in range(WECHAT_MEDIA_DOWNLOAD_ATTEMPTS):
        try:
            timeout = aiohttp.ClientTimeout(total=15.0)
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            async with (
                aiohttp.ClientSession(timeout=timeout, connector=connector) as client,
                client.get(url) as response,
            ):
                response.raise_for_status()
                content_length = int(response.headers.get("content-length") or 0)
                ensure_channel_media_size(content_length, encrypted=True)
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    total += len(chunk)
                    if total > MAX_ENCRYPTED_CHANNEL_MEDIA_BYTES:
                        raise ValueError(
                            "微信媒体密文超过大小上限: "
                            f"size>{MAX_ENCRYPTED_CHANNEL_MEDIA_BYTES}"
                        )
                    chunks.append(chunk)
                return b"".join(chunks), response.headers.get("content-type", "")
        except (TimeoutError, aiohttp.ClientConnectionError, aiohttp.ClientPayloadError):
            if attempt == WECHAT_MEDIA_DOWNLOAD_ATTEMPTS - 1:
                raise
            # 微信 CDN occasionally rejects a TLS handshake; retry with a new
            # session/connection before dropping the channel attachment.
            await asyncio.sleep(0.5 * (attempt + 1))
    raise RuntimeError("微信媒体下载失败")


async def _download_wechat_cdn_curl(url: str) -> tuple[bytes, str]:
    """Use the system TLS stack for CDN nodes incompatible with Python TLS."""
    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError("微信媒体下载失败: curl 不可用")

    def run() -> bytes:
        command = [
            curl,
            "--silent",
            "--show-error",
            "--fail",
            "--location",
            "--http1.1",
            "--user-agent",
            "Mozilla/5.0",
            "--max-time",
            "30",
            "--connect-timeout",
            "10",
            "--ignore-content-length",
            url,
        ]
        last_error = ""
        for attempt in range(WECHAT_CURL_DOWNLOAD_ATTEMPTS):
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            chunks: list[bytes] = []
            total = 0
            assert process.stdout is not None
            while True:
                chunk = process.stdout.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_ENCRYPTED_CHANNEL_MEDIA_BYTES:
                    process.kill()
                    process.wait()
                    raise ValueError("微信媒体密文超过大小上限")
                chunks.append(chunk)
            stderr = process.stderr.read() if process.stderr else b""
            return_code = process.wait()
            # 微信 CDN sometimes advertises a stale Content-Length. curl
            # returns 18 or 28 after receiving the complete encrypted payload;
            # let AES/expected_size validation decide whether it is usable.
            data = b"".join(chunks)
            if return_code == 0 or (return_code in {18, 28} and data):
                return data
            last_error = stderr.decode("utf-8", errors="replace")[:200]
            if attempt < WECHAT_CURL_DOWNLOAD_ATTEMPTS - 1:
                time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(
            f"微信媒体下载失败: curl exit={return_code} {last_error}"
        )

    data = await asyncio.to_thread(run)
    ensure_channel_media_size(len(data), encrypted=True)
    return data, ""


def download_media_url(
    full_url: str,
    *,
    aes_key: str = "",
    expected_size: int = 0,
) -> bytes:
    """Download the CDN URL supplied by an iLink image/file item.

    httpx 为主路径;aiohttp(若安装)与系统 curl 依次兜底,应对 CDN 节点 TLS 兼容问题。
    """
    parsed = urlparse(full_url)
    if parsed.scheme != "https" or not validate_wechat_host(parsed.hostname or ""):
        raise WeChatApiError(-1, "微信媒体 URL 域名不受信任")
    download_url = urlunparse(parsed)
    try:
        raw, content_type = asyncio.run(_download_wechat_cdn_httpx(download_url))
    except (TimeoutError, httpx.HTTPError, OSError) as exc:
        logger.warning(
            "微信 CDN httpx 下载失败: host=%s error=%s",
            parsed.hostname,
            type(exc).__name__,
        )
        if aiohttp is not None:
            try:
                raw, content_type = asyncio.run(_download_wechat_cdn_aiohttp(download_url))
            except (TimeoutError, aiohttp.ClientError, OSError) as aio_exc:
                logger.warning(
                    "微信 CDN aiohttp 下载失败，切换系统 curl: host=%s error=%s",
                    parsed.hostname,
                    type(aio_exc).__name__,
                )
                raw, content_type = asyncio.run(_download_wechat_cdn_curl(download_url))
        else:
            logger.warning(
                "aiohttp 未安装，切换系统 curl: host=%s",
                parsed.hostname,
            )
            raw, content_type = asyncio.run(_download_wechat_cdn_curl(download_url))
    if "application/json" not in content_type.lower():
        return decrypt_wechat_media(
            raw,
            aes_key,
            expected_size=expected_size,
        )
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise WeChatApiError(-1, "媒体下载响应格式无效") from exc
    errcode = int(data.get("errcode") or data.get("ret") or 0)
    raise WeChatApiError(errcode or -1, str(data.get("errmsg") or "媒体下载返回 JSON"))
