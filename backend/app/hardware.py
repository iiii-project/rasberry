"""AIY Voice Kit V2 的按鈕與 RGB 燈。沒有螢幕，就用按鈕上的燈告訴使用者金鶴現在在做什麼。

燈號（可在 config.yaml 的 hardware.leds 調整）：
  待機 綠燈微亮    收音中 綠燈恆亮    處理中（收完音到開口前）綠燈閃爍    說話中 紅燈閃爍    出錯 紅燈快閃

沒有安裝 aiy 函式庫（例如在電腦上開發）時，自動變成什麼都不做。
API 依據 aiyprojects-raspbian 的 src/aiy/board.py、src/aiy/leds.py。
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

DEFAULT_LEDS = {
    "idle": {"color": [0, 30, 0]},  # 待機：綠燈微亮
    "recording": {"color": [0, 255, 0]},  # 收音中：綠燈恆亮
    "processing": {"color": [0, 255, 0], "pattern": "blink"},  # 收完音到開口前：綠燈閃爍
    "speaking": {"color": [255, 0, 0], "pattern": "blink"},  # 說話：紅燈閃爍
    "error": {"color": [255, 0, 0], "pattern": "blink_fast"},  # 出錯：紅燈快閃
    "off": {"color": [0, 0, 0]},
}
PATTERN_MS = {"blink": 500, "blink_fast": 160, "breathe": 1600}


class Board:
    def __init__(self, cfg: dict = None):
        cfg = cfg or {}
        self._board = None
        self._leds = None
        self._states = {}
        self._current = None
        self.pressed = asyncio.Event()
        self.released = asyncio.Event()
        self.released.set()
        if not cfg.get("aiy", True):
            return
        try:
            from aiy.board import Board as AiyBoard
        except ImportError:
            log.info("找不到 aiy 函式庫，停用按鈕與燈號")
            return
        try:
            self._board = AiyBoard()
        except Exception as e:  # 硬體沒接好或沒有權限
            log.warning("AIY 硬體初始化失敗：%s", e)
            return

        loop = asyncio.get_event_loop()
        # 按鈕事件來自 GPIO 執行緒，要轉回 asyncio 事件迴圈
        self._board.button.when_pressed = lambda: loop.call_soon_threadsafe(self._on_press)
        self._board.button.when_released = lambda: loop.call_soon_threadsafe(self._on_release)

        leds_cfg = dict(DEFAULT_LEDS)
        leds_cfg.update(cfg.get("leds") or {})
        try:
            from aiy.leds import Leds, Pattern

            self._leds = Leds()
            for state, spec in leds_cfg.items():
                color = tuple(int(c) for c in spec.get("color", (0, 0, 0)))
                name = spec.get("pattern")
                if name:
                    pattern = Pattern.breathe(PATTERN_MS["breathe"]) if name == "breathe" \
                        else Pattern.blink(PATTERN_MS.get(name, 500))
                    self._states[state] = (pattern, Leds.rgb_pattern(color))
                else:
                    self._states[state] = (None, Leds.rgb_on(color))
            log.info("AIY 按鈕與 RGB 燈已啟用")
        except Exception as e:
            log.warning("RGB 燈無法使用（%s），只啟用按鈕", e)
            self._leds = None

    @property
    def available(self) -> bool:
        return self._board is not None

    def _on_press(self) -> None:
        self.released.clear()
        self.pressed.set()

    def _on_release(self) -> None:
        self.pressed.clear()
        self.released.set()

    def led(self, state: str) -> None:
        if state == "listening":  # VAD 模式的「在聽」跟待機同一個燈號
            state = "idle"
        if self._leds is None or state == self._current or state not in self._states:
            return
        pattern, channels = self._states[state]
        try:
            if pattern is not None:
                self._leds.pattern = pattern
            self._leds.update(channels)
            self._current = state
        except Exception as e:  # 燈號只是輔助，失敗不能影響對話
            log.warning("設定燈號失敗：%s", e)

    def close(self) -> None:
        if self._leds is not None:
            try:
                self.led("off")
                self._leds.reset()
            except Exception:
                pass
        if self._board is not None:
            self._board.close()
