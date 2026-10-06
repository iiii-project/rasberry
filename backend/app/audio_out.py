"""播放：mp3 交給 mpg123、提示音交給 aplay，Python 端不做任何音訊解碼。"""

from __future__ import annotations

import asyncio
import logging
import math
import struct

log = logging.getLogger(__name__)

BEEP_RATE = 16000


def _make_beep(freq: float = 880, ms: int = 120, volume: float = 0.25) -> bytes:
    n = BEEP_RATE * ms // 1000
    samples = (
        int(32767 * volume * math.sin(2 * math.pi * freq * i / BEEP_RATE)
            * (0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1))))
        for i in range(n)
    )
    return struct.pack("<{}h".format(n), *samples)


class Speaker:
    def __init__(self, cfg: dict):
        self.device = cfg.get("output_device")  # ALSA 裝置名稱
        self.volume = float(cfg.get("volume", 1.0))
        self._beep = _make_beep(volume=0.25 * self.volume)  # 啟動時算好，之後直接播

    def _mpg123_cmd(self) -> list:
        cmd = ["mpg123", "-q", "-f", str(int(32768 * self.volume))]
        if self.device:
            cmd += ["-a", self.device]
        return cmd + ["-"]

    async def play_stream(self, chunks: asyncio.Queue) -> None:
        """一次回覆只開一個 mpg123，把每句的 mp3 依序灌進去，句子之間不會有停頓。
        chunks 收到 None 代表結束。"""
        proc = await asyncio.create_subprocess_exec(
            *self._mpg123_cmd(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            while True:
                chunk = await chunks.get()
                if chunk is None:
                    break
                proc.stdin.write(chunk)
                await proc.stdin.drain()
            proc.stdin.close()
            await proc.wait()
        except (BrokenPipeError, ConnectionResetError):
            log.error("mpg123 意外結束，請檢查喇叭裝置設定")
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    async def beep(self) -> None:
        cmd = ["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(BEEP_RATE), "-c", "1"]
        if self.device:
            cmd += ["-D", self.device]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        await proc.communicate(self._beep)
