"""把 LLM 的串流文字切成可以先念出來的句子。"""

from __future__ import annotations

import re

# 句尾標點後面緊跟的右引號／右括號要留在同一句，不要變成下一句的開頭
_HARD_END = re.compile(r"[。！？!?…~～\n]+[」』”’\"')）]*")
_SOFT_END = re.compile(r"[，,、；;：:]")
SOFT_SPLIT_LEN = 28  # 句子太長時，在逗號處先切
FIRST_SOFT_SPLIT_LEN = 8  # 第一句只要湊到 8 個字、遇到逗號就先送去合成，讓金鶴更早開口


class SentenceSplitter:
    def __init__(self):
        self._buf = ""
        self._soft_len = FIRST_SOFT_SPLIT_LEN

    def feed(self, delta: str) -> list[str]:
        self._buf += delta
        out = []
        while True:
            m = _HARD_END.search(self._buf)
            # 句尾標點剛好在緩衝區最後面時，先等下一段：後面可能還有右引號要跟著這一句
            if m and m.end() < len(self._buf):
                cut = m.end()
            else:
                # 句子太長時，在湊滿門檻字數後的第一個逗號先切
                cuts = [x.end() for x in _SOFT_END.finditer(self._buf) if x.end() >= self._soft_len]
                if not cuts:
                    break
                cut = cuts[0]
            piece, self._buf = self._buf[:cut].strip(), self._buf[cut:]
            if piece:
                out.append(piece)
                self._soft_len = SOFT_SPLIT_LEN  # 只有第一句用短門檻
        return out

    def flush(self) -> list[str]:
        rest, self._buf = self._buf.strip(), ""
        if rest:
            self._soft_len = SOFT_SPLIT_LEN
        return [rest] if rest else []
