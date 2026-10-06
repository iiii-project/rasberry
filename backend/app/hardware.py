"""AIY Voice Kit 的按鈕與燈號。沒有螢幕，就用按鈕上的燈告訴使用者金鶴現在在做什麼。

沒有安裝 aiy 函式庫（例如在電腦上開發）時，自動變成什麼都不做。
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)


class Board:
    def __init__(self, enabled: bool = True):
        self._board = None
        self._led_states = {}
        self.pressed = asyncio.Event()
        self.released = asyncio.Event()
        self.released.set()
        if not enabled:
            return
        try:
            from aiy.board import Board as AiyBoard, Led
        except ImportError:
            log.info("找不到 aiy 函式庫，停用按鈕與燈號")
            return
        try:
            self._board = AiyBoard()
        except Exception as e:  # 硬體沒接好或沒有權限
            log.warning("AIY 硬體初始化失敗：%s", e)
            return
        self._led_states = {
            "off": Led.OFF,
            "listening": Led.ON,  # 恆亮：在聽
            "recording": Led.ON,
            "thinking": Led.BLINK,  # 閃爍：辨識/思考中
            "speaking": Led.PULSE_QUICK,  # 呼吸：說話中
            "error": Led.BLINK_3,
        }
        loop = asyncio.get_event_loop()
        self._board.button.when_pressed = lambda: loop.call_soon_threadsafe(self._on_press)
        self._board.button.when_released = lambda: loop.call_soon_threadsafe(self._on_release)
        log.info("AIY 按鈕與燈號已啟用")

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
        if self._board is not None and state in self._led_states:
            self._board.led.state = self._led_states[state]

    def close(self) -> None:
        if self._board is not None:
            self._board.led.state = self._led_states["off"]
            self._board.close()
