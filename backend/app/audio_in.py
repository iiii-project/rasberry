"""錄音：用 ALSA 的 arecord 持續收音（不需要 PortAudio / numpy），再用 VAD 或按鈕切出語句。"""

from __future__ import annotations

import asyncio
import collections
import logging
from typing import AsyncIterator

import webrtcvad

log = logging.getLogger(__name__)

RATE = 16000
FRAME_MS = 30  # webrtcvad 只接受 10/20/30ms 的音框
FRAME_BYTES = RATE * FRAME_MS // 1000 * 2  # 16-bit mono


class MicrophoneError(RuntimeError):
    pass


class Recorder:
    def __init__(self, cfg: dict):
        self.device = cfg.get("input_device")  # ALSA 裝置名稱，例如 default、plughw:0,0
        self.vad = webrtcvad.Vad(cfg.get("vad_aggressiveness", 2))
        self.silence_frames = cfg.get("silence_ms", 1000) // FRAME_MS
        self.min_frames = cfg.get("min_utterance_ms", 400) // FRAME_MS
        self.max_frames = cfg.get("max_utterance_s", 15) * 1000 // FRAME_MS
        self._frames = asyncio.Queue(maxsize=200)  # type: asyncio.Queue
        self._muted = False
        self._reset = False
        self._failure = None  # type: MicrophoneError | None
        self._proc = None
        self._reader = None

    async def start(self) -> None:
        cmd = ["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(RATE), "-c", "1"]
        if self.device:
            cmd += ["-D", self.device]
        self._proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        self._reader = asyncio.ensure_future(self._read_loop())
        log.info("麥克風啟動（%s）", self.device or "ALSA 預設裝置")

    async def stop(self) -> None:
        if self._reader:
            self._reader.cancel()
        if self._proc and self._proc.returncode is None:
            self._proc.kill()
            await self._proc.wait()

    async def _read_loop(self) -> None:
        # 一直把 arecord 的輸出讀掉，避免靜音期間緩衝區塞滿、之後讀到舊聲音
        try:
            while True:
                frame = await self._proc.stdout.readexactly(FRAME_BYTES)
                if self._muted:
                    continue
                if self._frames.full():
                    self._frames.get_nowait()
                self._frames.put_nowait(frame)
        except asyncio.IncompleteReadError:
            # arecord 結束（USB 麥克風被拔掉、裝置名稱錯誤…）。
            # 必須讓主流程知道並結束程式，交給 systemd 重新啟動；否則會永遠卡在等聲音
            code = await self._proc.wait()
            err = (await self._proc.stderr.read()).decode("utf-8", "ignore").strip()
            self._failure = MicrophoneError(
                "arecord 已結束（代碼 {}）：{}".format(code, err or "沒有錯誤訊息")
            )
            self._wake_consumer()

    def _wake_consumer(self) -> None:
        if self._frames.full():
            self._frames.get_nowait()
        self._frames.put_nowait(None)

    async def _next_frame(self) -> bytes:
        frame = await self._frames.get()
        if frame is None:
            raise self._failure
        return frame

    def set_muted(self, muted: bool) -> None:
        """思考/說話時靜音，避免收到自己的聲音。"""
        self._muted = muted
        if muted:
            self._reset = True
            while not self._frames.empty():
                self._frames.get_nowait()
            if self._failure:
                self._wake_consumer()

    async def utterances(self) -> AsyncIterator[bytes]:
        """VAD 模式：自動偵測說話開始與結束，產生一句一句的 PCM。"""
        preroll = collections.deque(maxlen=10)
        voiced = []
        triggered = False
        silence = 0

        while True:
            frame = await self._next_frame()
            if self._reset:
                preroll.clear()
                voiced, triggered, silence = [], False, 0
                self._reset = False

            is_speech = self.vad.is_speech(frame, RATE)

            if not triggered:
                preroll.append((frame, is_speech))
                if sum(s for _, s in preroll) >= 0.7 * preroll.maxlen:
                    triggered = True
                    voiced = [f for f, _ in preroll]
                    preroll.clear()
                    silence = 0
                continue

            voiced.append(frame)
            silence = 0 if is_speech else silence + 1
            if silence >= self.silence_frames or len(voiced) >= self.max_frames:
                speech_frames = len(voiced) - silence
                pcm = b"".join(voiced)
                voiced, triggered, silence = [], False, 0
                if speech_frames >= self.min_frames:
                    yield pcm

    async def record_until(self, stop: asyncio.Event) -> bytes:
        """按鈕模式：從現在錄到 stop 被設定（放開按鈕）為止。"""
        self.set_muted(False)
        frames = []
        try:
            while not stop.is_set() and len(frames) < self.max_frames:
                if self._frames.empty():
                    await asyncio.sleep(FRAME_MS / 1000)
                    continue
                frames.append(await self._next_frame())
            # 放開按鈕時，最後幾個音框可能還在佇列裡，一起收進來，避免句尾被截掉
            while not self._frames.empty() and len(frames) < self.max_frames:
                frames.append(await self._next_frame())
        finally:
            self.set_muted(True)
        return b"".join(frames) if len(frames) >= self.min_frames else b""
