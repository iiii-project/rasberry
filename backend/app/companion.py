"""主流程：聆聽 → 辨識 → 思考 → 說話。

錯誤處理原則：沒有螢幕，所以任何錯誤都要用聲音（道歉句）和燈號讓人知道，
而且語音迴圈本身絕對不能因為單次失敗而停下來。唯一的例外是麥克風壞掉，
這時讓程式結束，交給 systemd 重新啟動。

注意 Python 3.7 的 asyncio.CancelledError 是 Exception 的子類別（3.8 起不是），
所以每個 `except Exception` 前面都要先放 `except asyncio.CancelledError: raise`，
否則按鈕打斷會被當成錯誤處理。
"""

from __future__ import annotations

import asyncio
import logging
import time

from . import net
from .aio import cancel_and_wait
from .audio_out import Speaker
from .hardware import Board
from .llm import FLUSH, Brain
from .text_stream import SentenceSplitter
from .tts import TextToSpeech

log = logging.getLogger(__name__)

ECHO_GUARD_S = 0.3  # 說完後稍等再開麥克風，避免收到殘響
SORRY = "抱歉，我剛剛恍神了，可以再說一次嗎？"


class Companion:
    def __init__(self, cfg: dict, use_mic: bool = True):
        audio = cfg["audio"]
        self.backend = None
        tools = None
        backend_cfg = cfg.get("backend") or {}
        if backend_cfg.get("enabled", True):
            from .fortune_backend import FortuneBackend
            from .tools import FortuneTools

            self.backend = FortuneBackend(backend_cfg)
            tools = FortuneTools(self.backend)
            log.info("求籤後端：%s（裝置 ID：%s）", self.backend.base_url, self.backend.device_id)
        self.brain = Brain(cfg["llm"], cfg["character"], tools=tools)
        self.tts = TextToSpeech(cfg["tts"])
        self.speaker = Speaker(audio)
        self.board = Board(cfg.get("hardware") or {})
        self.mode = audio.get("mode", "vad")
        self.recorder = None
        self.stt = None
        self._warmup = None
        self._prewarm = None
        self._t = {}  # 這一輪各階段的時間點，用來記錄耗時
        if use_mic:
            from .audio_in import RATE, Recorder
            from .stt import SpeechToText

            self.rate = RATE
            self.recorder = Recorder(audio)
            self.stt = SpeechToText(cfg["stt"])
        if self.mode == "button" and not self.board.available:
            log.warning("沒有偵測到 AIY 按鈕，改用 VAD 自動偵測模式")
            self.mode = "vad"

    async def close(self) -> None:
        for task in (self._warmup, self._prewarm):
            if task:
                await cancel_and_wait(task)
        if self.recorder:
            await self.recorder.stop()
        self.board.close()

    async def _warm_cache(self) -> None:
        """啟動時在背景預先準備：道歉句快取、預設籤系、到 API 的連線。"""
        try:
            await self.tts.cached(SORRY)
        except Exception as e:  # 開機時網路還沒好很正常，之後第一次需要時會再試
            log.info("道歉句還沒快取：%r", e)
        if self.backend:
            try:
                await self.backend.warm_up()
            except Exception as e:
                log.info("求籤後端還連不上：%r", e)

    def _start_prewarm(self) -> None:
        """開始收音的同時，先跟辨識 API 建立好 HTTPS 連線（收音通常要好幾秒，剛好藏住握手時間）。"""
        if self.stt and (self._prewarm is None or self._prewarm.done()):
            self._prewarm = asyncio.ensure_future(net.prewarm(self.stt.base_url))

    # ---- 語音迴圈 ----
    async def run_voice_loop(self) -> None:
        self._warmup = asyncio.ensure_future(self._warm_cache())
        await self.recorder.start()
        self.recorder.set_muted(self.mode == "button")
        log.info("👂 %s", "按住按鈕說話，放開送出" if self.mode == "button" else "自動偵測說話")
        if self.mode == "button":
            while True:
                self.board.led("idle")
                await self.board.pressed.wait()
                self.board.led("recording")
                self._start_prewarm()
                pcm = await self.recorder.record_while_held(self.board.released)
                if pcm:
                    await self._interruptible(self._handle_utterance(pcm))
        else:
            self.board.led("listening")
            async for pcm in self.recorder.utterances():
                self.recorder.set_muted(True)  # 思考/說話時不收音，避免聽到自己的聲音
                await self._interruptible(self._handle_utterance(pcm))
                await asyncio.sleep(ECHO_GUARD_S)
                self.recorder.set_muted(False)
                self.board.led("listening")

    async def _interruptible(self, coro) -> None:
        """執行 coro；期間按下按鈕就立刻打斷（停止辨識、思考或說話）。
        按鈕模式下，打斷用的這一按會接著直接開始錄下一句。"""
        task = asyncio.ensure_future(coro)
        if not self.board.available or self.board.pressed.is_set():
            await task
            return
        waiter = asyncio.ensure_future(self.board.pressed.wait())
        try:
            await asyncio.wait([task, waiter], return_when=asyncio.FIRST_COMPLETED)
        finally:
            pressed = waiter.done() and not waiter.cancelled()
            await cancel_and_wait(waiter)
            if not task.done():
                if pressed:  # 程式關閉時也會走到這裡，那不是按鈕打斷
                    log.info("✋ 按鈕打斷")
                await cancel_and_wait(task)
        if not task.cancelled():
            task.result()

    async def _handle_utterance(self, pcm: bytes) -> None:
        self.board.led("processing")  # 收完音，辨識與準備回答期間綠燈閃爍
        self._t = {"start": time.time()}
        try:
            text = await self.stt.transcribe(pcm, self.rate)
            self._t["stt"] = time.time()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("語音辨識失敗")
            await self._say_sorry()
            return
        log.info("👂 %s", text or "（沒聽清楚）")
        if text:
            await self.respond(text)
            self._log_timing(len(pcm) / 2 / self.rate)

    def _log_timing(self, audio_s: float) -> None:
        t = self._t
        if not {"start", "stt", "text", "audio"} <= set(t):
            return
        log.info(
            "⏱ 錄音 %.1f 秒｜辨識 %.1f 秒 → LLM 第一個字 +%.1f 秒 → 開口 +%.1f 秒｜收完音到開口共 %.1f 秒",
            audio_s, t["stt"] - t["start"], t["text"] - t["stt"], t["audio"] - t["text"], t["audio"] - t["start"],
        )

    # ---- 文字迴圈（開發測試用，不需要麥克風） ----
    async def run_text_loop(self) -> None:
        loop = asyncio.get_event_loop()
        while True:
            text = (await loop.run_in_executor(None, input, "你：")).strip()
            if text:
                await self.respond(text)

    # ---- 回應一句話（不會拋出例外，取消除外） ----
    async def respond(self, user_text: str) -> None:
        self.board.led("processing")
        try:
            await self._speak_reply(user_text)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("產生回覆失敗")
            await self._say_sorry()

    async def _say_sorry(self) -> None:
        """出錯時的最後防線：播放快取的道歉句。這裡再失敗也只記錄，不往外拋。"""
        self.board.led("error")
        try:
            mp3 = await self.tts.cached(SORRY)
            if mp3:
                await self.speaker.play_bytes(mp3)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("連道歉句都播不出來（網路或喇叭異常）")

    async def _speak_reply(self, user_text: str) -> None:
        """LLM 串流 → 切句 → TTS → 播放，同時進行，第一句好了就先念。"""
        sentences = asyncio.Queue()  # type: asyncio.Queue

        async def produce():
            splitter = SentenceSplitter()
            try:
                async for delta in self.brain.chat(user_text):
                    self._t.setdefault("text", time.time())
                    # FLUSH：要開始查資料了，模型若已說了半句話，先把它念出來
                    pieces = splitter.flush() if delta == FLUSH else splitter.feed(delta)
                    for s in pieces:
                        await sentences.put(s)
                for s in splitter.flush():
                    await sentences.put(s)
            finally:
                await sentences.put(None)

        producer = asyncio.ensure_future(produce())
        try:
            await self._speak_queue(sentences)
        except BaseException:
            await cancel_and_wait(producer)
            raise
        # 播完了：LLM 若中途出錯，在這裡拋出，讓 respond() 念道歉句
        await producer

    async def _speak_queue(self, sentences: asyncio.Queue) -> None:
        # TTS 的 mp3 片段一邊產生一邊送進 mpg123，邊合成邊播放
        chunks = asyncio.Queue()  # type: asyncio.Queue
        speaking = False

        async def synthesize():
            nonlocal speaking
            try:
                while True:
                    sentence = await sentences.get()
                    if sentence is None:
                        break
                    log.info("🗣  %s", sentence)
                    try:
                        async for chunk in self.tts.stream(sentence):
                            if not speaking:
                                speaking = True
                                self._t.setdefault("audio", time.time())
                                self.board.led("speaking")
                            await chunks.put(chunk)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        log.exception("TTS 失敗，略過這句：%s", sentence)
            finally:
                await chunks.put(None)

        synth = asyncio.ensure_future(synthesize())
        try:
            await self.speaker.play_stream(chunks)
        finally:
            await cancel_and_wait(synth)
