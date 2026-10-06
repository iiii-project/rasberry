"""共用的 aiohttp 連線與 API 設定（不用 openai SDK，Pi Zero 上不需要編譯 Rust 套件）。"""

from __future__ import annotations

import os

import aiohttp

OPENAI_BASE_URL = "https://api.openai.com/v1"

_session: aiohttp.ClientSession | None = None


def session() -> aiohttp.ClientSession:
    """重複使用同一個連線，省去每次 TLS 握手（在 Pi Zero 上很花時間）。"""
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def close() -> None:
    if _session is not None and not _session.closed:
        await _session.close()


def api_settings(prefix: str) -> tuple[str, str]:
    """回傳 (base_url, api_key)。依序讀取 {prefix}_*、LLM_*、OPENAI_*。"""
    def pick(name: str) -> str:
        for p in (prefix, "LLM", "OPENAI"):
            value = os.getenv("{}_{}".format(p, name))
            if value:
                return value
        return ""

    base_url = (pick("BASE_URL") or OPENAI_BASE_URL).rstrip("/")
    api_key = pick("API_KEY")
    if not api_key:
        raise RuntimeError("找不到 API key，請在 .env 設定 LLM_API_KEY")
    return base_url, api_key
