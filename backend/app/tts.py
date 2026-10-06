"""語音合成：edge-tts（微軟神經語音），mp3 片段一收到就往播放器送。"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import AsyncIterator

import edge_tts

from .config import PROJECT_ROOT

log = logging.getLogger(__name__)

CACHE_DIR = PROJECT_ROOT / "cache"

# 移除不該念出來的符號（markdown、emoji、括號動作描述）
_STRIP = re.compile(r"[*_`#>~]|[\U0001F300-\U0001FAFF☀-➿]|（[^）]*）|\([^)]*\)")


class TextToSpeech:
    def __init__(self, cfg: dict):
        self.voice = cfg.get("voice", "zh-TW-HsiaoChenNeural")
        self.rate = cfg.get("rate", "+0%")
        self.pitch = cfg.get("pitch", "+0Hz")

    @staticmethod
    def clean(text: str) -> str:
        return _STRIP.sub("", text).strip()

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        text = self.clean(text)
        if not re.search(r"\w", text):
            return
        communicate = edge_tts.Communicate(text, self.voice, rate=self.rate, pitch=self.pitch)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                yield chunk["data"]

    async def cached(self, text: str) -> bytes:
        """固定句子（例如道歉句）合成一次就存到磁碟，之後斷網也念得出來。"""
        key = hashlib.sha1("|".join((self.voice, self.rate, self.pitch, text)).encode()).hexdigest()
        path = CACHE_DIR / "tts-{}.mp3".format(key[:16])
        if path.exists():
            return path.read_bytes()
        data = b"".join([chunk async for chunk in self.stream(text)])
        if data:
            CACHE_DIR.mkdir(exist_ok=True)
            path.write_bytes(data)
        return data
