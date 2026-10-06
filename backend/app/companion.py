"""主流程：聆聽 → 辨識 → 思考 → 說話。"""

from __future__ import annotations

import asyncio
import logging

from .audio_out import Speaker
from .hardware import Board
from .llm import Brain
from .text_stream import SentenceSplitter
from .tts import TextToSpeech

log = logging.getLogger(__name__)

ECHO_GUARD_S = 0.3  # 說完後稍等再開麥克風，避免收到殘響
SORRY = "抱歉，我剛剛恍神了，可以再說一次嗎？"


class Companion:
    def __init__(self, cfg: dict, use_mic: bool = True):
        audio = cfg["audio"]
        self.brain = Brain(cfg["llm"], cfg["character"])
        self.tts = TextToSpeech(cfg["tts"])
        self.speaker = Speaker(audio)
        self.board = Board(cfg.get("hardware", {}).get("aiy", True))
        self.beep = audio.get("beep_on_hear", True)
        self.mode = audio.get("mode", "vad")
        self.recorder = None
        self.stt = None
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
        if self.recorder:
            await self.recorder.stop()
        self.board.close()

    # ---- 語音迴圈 ----
    async def run_voice_loop(self) -> None:
        await self.recorder.start()
        self.recorder.set_muted(self.mode == "button")
        log.info("👂 聆聽中（%s 模式）", "按住按鈕說話" if self.mode == "button" else "自動偵測")
        if self.mode == "button":
            while True:
                self.board.led("off")
                await self.board.pressed.wait()
                self.board.led("recording")
                pcm = await self.recorder.record_until(self.board.released)
                if pcm:
                    await self._handle_utterance(pcm)
        else:
            self.board.led("listening")
            async for pcm in self.recorder.utterances():
                self.recorder.set_muted(True)  # 思考/說話時不收音，避免聽到自己的聲音
                await self._handle_utterance(pcm)
                await asyncio.sleep(ECHO_GUARD_S)
                self.recorder.set_muted(False)
                self.board.led("listening")

    async def _handle_utterance(self, pcm: bytes) -> None:
        self.board.led("thinking")
        try:
            if self.beep:
                await self.speaker.beep()
            text = await self.stt.transcribe(pcm, self.rate)
            log.info("👂 %s", text or "（沒聽清楚）")
            if text:
                await self.respond(text)
        except Exception:
            log.exception("處理語音時發生錯誤")
            self.board.led("error")
            await self._speak_lines([SORRY])

    # ---- 文字迴圈（開發測試用，不需要麥克風） ----
    async def run_text_loop(self) -> None:
        loop = asyncio.get_event_loop()
        while True:
            text = (await loop.run_in_executor(None, input, "你：")).strip()
            if text:
                await self.respond(text)

    # ---- 回應一句話 ----
    async def respond(self, user_text: str) -> None:
        self.board.led("thinking")
        try:
            await self._speak_reply(user_text)
        except Exception:
            log.exception("產生回覆失敗")
            self.board.led("error")
            await self._speak_lines([SORRY])

    async def _speak_reply(self, user_text: str) -> None:
        """LLM 串流 → 切句 → TTS → 播放，同時進行，第一句好了就先念。"""
        sentences = asyncio.Queue()  # type: asyncio.Queue

        async def produce():
            splitter = SentenceSplitter()
            try:
                async for delta in self.brain.chat(user_text):
                    for s in splitter.feed(delta):
                        await sentences.put(s)
                for s in splitter.flush():
                    await sentences.put(s)
            finally:
                await sentences.put(None)

        producer = asyncio.ensure_future(produce())
        try:
            await self._speak_queue(sentences)
        finally:
            if not producer.done():
                producer.cancel()
        await producer  # 把 LLM 的例外拋出來

    async def _speak_lines(self, lines: list) -> None:
        q = asyncio.Queue()  # type: asyncio.Queue
        for line in lines:
            q.put_nowait(line)
        q.put_nowait(None)
        await self._speak_queue(q)

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
                                self.board.led("speaking")
                            await chunks.put(chunk)
                    except Exception:
                        log.exception("TTS 失敗：%s", sentence)
            finally:
                await chunks.put(None)

        synth = asyncio.ensure_future(synthesize())
        try:
            await self.speaker.play_stream(chunks)
        finally:
            if not synth.done():
                synth.cancel()
