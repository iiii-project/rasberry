"""語音辨識：上傳到雲端 /audio/transcriptions（樹莓派只負責錄音）。"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import wave

import aiohttp

from . import net

log = logging.getLogger(__name__)

# Whisper 系列在靜音或雜訊時常見的幻覺輸出。只比對整段幻覺的固定說法，
# 避免把「幫我開字幕」這類正常句子丟掉
HALLUCINATION = re.compile(
    r"字幕(由|提供|製作|志願者)|Amara\.org|請不吝點[贊讚]|訂閱.{0,6}(頻道|按讚|打賞)"
    r"|^\W*(謝謝|感謝)(大家|各位)?的?(觀看|收看|聆聽)\W*$"
)


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
        self.timeout = aiohttp.ClientTimeout(total=float(cfg.get("timeout_s", 30)), sock_connect=10)

    async def transcribe(self, pcm: bytes, rate: int) -> str:
        wav = to_wav(pcm, rate)
        for attempt in (1, 2):
            try:
                text = await self._request(wav)
                break
            except net.RETRYABLE as e:
                if attempt == 2:
                    raise
                log.warning("語音辨識連線失敗，重試一次：%r", e)
                await asyncio.sleep(0.5)

        if HALLUCINATION.search(text):
            log.info("略過疑似幻覺的辨識結果：%s", text)
            return ""
        return text

    async def _request(self, wav: bytes) -> str:
        # FormData 只能送一次，每次重試都要重建
        form = aiohttp.FormData()
        form.add_field("file", wav, filename="speech.wav", content_type="audio/wav")
        form.add_field("model", self.model)
        form.add_field("language", self.language)
        form.add_field("response_format", "json")
        if self.prompt:
            form.add_field("prompt", self.prompt)

        async with net.session().post(
            self.base_url + "/audio/transcriptions",
            data=form,
            headers={"Authorization": "Bearer " + self.api_key},
            timeout=self.timeout,
        ) as resp:
            if resp.status != 200:
                raise RuntimeError("STT API {}: {}".format(resp.status, (await resp.text())[:300]))
            return ((await resp.json()).get("text") or "").strip()
