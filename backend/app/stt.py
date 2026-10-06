"""語音辨識：上傳到雲端 /audio/transcriptions（樹莓派只負責錄音）。"""

from __future__ import annotations

import io
import logging
import wave

import aiohttp

from . import net

log = logging.getLogger(__name__)

# Whisper 系列在靜音或雜訊時常見的幻覺輸出
HALLUCINATIONS = ("字幕", "訂閱", "點贊", "點讚", "Amara", "謝謝觀看", "感謝收看")


def to_wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


class SpeechToText:
    def __init__(self, cfg: dict):
        self.base_url, self.api_key = net.api_settings("STT")
        self.model = cfg.get("model", "gpt-4o-mini-transcribe")
        self.language = cfg.get("language", "zh")
        self.prompt = cfg.get("prompt", "")
        self.timeout = float(cfg.get("timeout_s", 30))

    async def transcribe(self, pcm: bytes, rate: int) -> str:
        form = aiohttp.FormData()
        form.add_field("file", to_wav(pcm, rate), filename="speech.wav", content_type="audio/wav")
        form.add_field("model", self.model)
        form.add_field("language", self.language)
        form.add_field("response_format", "json")
        if self.prompt:
            form.add_field("prompt", self.prompt)

        async with net.session().post(
            self.base_url + "/audio/transcriptions",
            data=form,
            headers={"Authorization": "Bearer " + self.api_key},
            timeout=aiohttp.ClientTimeout(total=self.timeout),
        ) as resp:
            if resp.status != 200:
                raise RuntimeError("STT API {}: {}".format(resp.status, (await resp.text())[:300]))
            text = (await resp.json()).get("text", "").strip()

        if any(h in text for h in HALLUCINATIONS):
            return ""
        return text
