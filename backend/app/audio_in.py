"""錄音：用 ALSA 的 arecord 持續收音（不需要 PortAudio / numpy），再用 VAD 或按鈕切出語句。"""

from __future__ import annotations

import asyncio
import collections
import logging
import warnings
from typing import AsyncIterator

import webrtcvad

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)  # 3.11+ 提示 audioop 將移除
    try:
        import audioop  # C 實作，Pi Zero 上處理整段錄音也只要幾毫秒；Python 3.13 起移除
    except ImportError:
        audioop = None

log = logging.getLogger(__name__)

RATE = 16000
FRAME_MS = 30  # webrtcvad 只接受 10/20/30ms 的音框
FRAME_BYTES = RATE * FRAME_MS // 1000 * 2  # 16-bit mono
HOLD_S = 1.0  # 按鈕按住超過這個秒數，視為「按住說話」，放開就結束
NO_SPEECH_S = 6.0  # 按一下後這麼久都沒開口，就取消收音
PAD_FRAMES = 10  # 裁掉頭尾靜音時，說話前後各保留 300ms，避免切到字
TARGET_PEAK = 20000  # 音量正規化的目標峰值（約 -4 dBFS）
MAX_GAIN = 6.0  # 最多放大 6 倍，避免把底噪放得太大


def finalize(frames: list, flags: list) -> bytes:
    """送去辨識前的處理：裁掉頭尾靜音（上傳更少、辨識更快，也減少靜音造成的幻覺），
    太小聲時放大音量（辨識更準）。"""
    speech = [i for i, f in enumerate(flags) if f]
    if speech:
        frames = frames[max(0, speech[0] - PAD_FRAMES):speech[-1] + PAD_FRAMES + 1]
    pcm = b"".join(frames)
    if audioop is not None and pcm:
        peak = audioop.max(pcm, 2)
        if peak:
            gain = min(TARGET_PEAK / peak, MAX_GAIN)
            if gain > 1.2:
                pcm = audioop.mul(pcm, 2, gain)
    return pcm


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
        flags = []
        triggered = False
        silence = 0

        while True:
            frame = await self._next_frame()
            if self._reset:
                preroll.clear()
                voiced, flags, triggered, silence = [], [], False, 0
                self._reset = False

            is_speech = self.vad.is_speech(frame, RATE)

            if not triggered:
                preroll.append((frame, is_speech))
                if sum(s for _, s in preroll) >= 0.7 * preroll.maxlen:
                    triggered = True
                    voiced = [f for f, _ in preroll]
                    flags = [s for _, s in preroll]
                    preroll.clear()
                    silence = 0
                continue

            voiced.append(frame)
            flags.append(is_speech)
            silence = 0 if is_speech else silence + 1
            if silence >= self.silence_frames or len(voiced) >= self.max_frames:
                speech_frames = len(voiced) - silence
                pcm = finalize(voiced, flags)
                voiced, flags, triggered, silence = [], [], False, 0
                if speech_frames >= self.min_frames:
                    yield pcm

    async def record_after_press(self, released: asyncio.Event) -> bytes:
        """按鈕模式：按下按鈕後才開始收音（呼叫時按鈕剛被按下）。兩種用法都支援：

        - 按住說話：按住超過 HOLD_S 秒，就錄到放開按鈕為止
        - 按一下就放開：開始收音，由 VAD 偵測到說完（停頓 silence_ms）自動結束；
          NO_SPEECH_S 秒內都沒開口就取消
        """
        self.set_muted(False)
        frames = []
        flags = []
        speech_frames = 0
        silence = 0
        hold = False
        try:
            while len(frames) < self.max_frames:
                elapsed = len(frames) * FRAME_MS / 1000
                if not released.is_set() and elapsed >= HOLD_S:
                    hold = True
                if hold and released.is_set():
                    break
                if self._frames.empty():
                    await asyncio.sleep(FRAME_MS / 1000)
                    continue
                frame = await self._next_frame()
                frames.append(frame)
                is_speech = self.vad.is_speech(frame, RATE)
                flags.append(is_speech)
                if is_speech:
                    speech_frames += 1
                    silence = 0
                else:
                    silence += 1
                if hold:
                    continue  # 按住期間由使用者決定何時結束
                if speech_frames >= self.min_frames and silence >= self.silence_frames:
                    break
                if speech_frames < self.min_frames and elapsed >= NO_SPEECH_S:
                    log.info("按下按鈕後沒有聽到說話，取消")
                    return b""
            # 結束時最後幾個音框可能還在佇列裡，一起收進來，避免句尾被截掉
            while not self._frames.empty() and len(frames) < self.max_frames:
                frame = await self._next_frame()
                frames.append(frame)
                flags.append(self.vad.is_speech(frame, RATE))
        finally:
            self.set_muted(True)
        return finalize(frames, flags) if speech_frames >= self.min_frames else b""
