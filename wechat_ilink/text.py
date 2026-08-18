from __future__ import annotations

CHANNEL_TEXT_LIMIT = 2000
WECHAT_TEXT_LIMIT = 2000


def split_channel_text(text: str, limit: int = CHANNEL_TEXT_LIMIT) -> list[str]:
    """按渠道 2000 字上限拆分长文本，优先 \n\n / \n / 空格边界，找不到则硬切。"""
    if not text:
        return []
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = -1
        for sep in ("\n\n", "\n", " "):
            cut = window.rfind(sep)
            if cut > 0:
                break
        if cut <= 0:
            chunks.append(remaining[:limit])
            remaining = remaining[limit:]
            continue
        chunk = remaining[:cut].rstrip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[cut:].lstrip("\n ")
    if remaining:
        chunks.append(remaining)
    return chunks


def split_wechat_text(text: str, limit: int = WECHAT_TEXT_LIMIT) -> list[str]:
    """按 2000 字上限拆分长文本，优先 \n\n / \n / 空格边界，找不到则硬切。"""
    return split_channel_text(text, limit)
