"""語音辨識：上傳到雲端 /audio/transcriptions（樹莓派只負責錄音）。"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import wave

import aiohttp

from . import net
from .aio import cancel_and_wait

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
        # 辨識平常約 1 秒，但 OpenAI 偶爾會慢到 6～18 秒；超過這個秒數就同時再送一次，
        # 哪個先回來用哪個（代價是偶爾多一次辨識費用）。設 0 停用
        self.hedge_after = float(cfg.get("hedge_after_s", 2.5))

    async def transcribe(self, pcm: bytes, rate: int) -> str:
        text = await self._hedged(to_wav(pcm, rate))
        if HALLUCINATION.search(text):
            log.info("略過疑似幻覺的辨識結果：%s", text)
            return ""
        return text

    async def _hedged(self, wav: bytes) -> str:
        tasks = [asyncio.ensure_future(self._request(wav))]
        hedged = retried = False
        try:
            while True:
                timeout = self.hedge_after if self.hedge_after > 0 and not hedged else None
                done, _ = await asyncio.wait(tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    hedged = True
                    log.info("語音辨識超過 %.1f 秒，同時再送一次", self.hedge_after)
                    tasks.append(asyncio.ensure_future(self._request(wav)))
                    continue
                error = None
                for task in done:
                    tasks.remove(task)
                    if task.exception() is None:
                        return task.result()
                    error = task.exception()
                if tasks:
                    continue  # 另一個請求還在跑，等它
                if isinstance(error, net.RETRYABLE) and not retried:
                    retried = True
                    log.warning("語音辨識連線失敗，重試一次：%r", error)
                    await asyncio.sleep(0.3)
                    tasks.append(asyncio.ensure_future(self._request(wav)))
                    continue
                raise error
        finally:
            for task in tasks:
                await cancel_and_wait(task)

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
