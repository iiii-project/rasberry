"""共用的 aiohttp 連線與 API 設定（不用 openai SDK，Pi Zero 上不需要編譯 Rust 套件）。"""

from __future__ import annotations

import asyncio
import logging
import os
import ssl

import aiohttp

log = logging.getLogger(__name__)

OPENAI_BASE_URL = "https://api.openai.com/v1"
KEEPALIVE_S = 60  # aiohttp 預設閒置 15 秒就斷線；拉長讓相鄰兩輪對話能重用連線，省下 TLS 握手

# 值得重試一次的暫時性錯誤（Wi-Fi 閃斷、伺服器關掉閒置連線、逾時）
RETRYABLE = (aiohttp.ClientConnectionError, asyncio.TimeoutError)

_session = None  # type: aiohttp.ClientSession | None


def _ssl_context():
    """AIY 映像檔（2021 年）內建的根憑證可能太舊，改用 certifi（edge-tts 的相依套件，一定有裝）
    提供的最新憑證清單驗證 HTTPS。"""
    try:
        import certifi
    except ImportError:
        return None
    return ssl.create_default_context(cafile=certifi.where())


def session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        kwargs = {"keepalive_timeout": KEEPALIVE_S}
        context = _ssl_context()
        if context is not None:
            kwargs["ssl"] = context
        _session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(**kwargs))
    return _session


async def close() -> None:
    if _session is not None and not _session.closed:
        await _session.close()


def api_settings(prefix: str) -> tuple:
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


async def prewarm(base_url: str) -> None:
    """先跟 API 伺服器建立好 HTTPS 連線放進連線池（例如按下按鈕、開始收音時呼叫），
    等收音結束要送辨識時就不用再做 TLS 握手——Pi Zero 單核心上握手要花不少時間。
    用沒有帶金鑰的 HEAD 請求，伺服器會很快回 401，不會產生任何費用。"""
    try:
        async with session().head(base_url + "/models", timeout=aiohttp.ClientTimeout(total=5)) as resp:
            await resp.read()
    except Exception as e:  # 預熱失敗不影響之後的正式請求
        log.debug("預先連線失敗：%r", e)
