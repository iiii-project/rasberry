"""把 LLM 的串流文字切成可以先念出來的句子。"""

from __future__ import annotations

import re

# 句尾標點後面緊跟的右引號／右括號要留在同一句，不要變成下一句的開頭
_HARD_END = re.compile(r"[。！？!?…~～\n]+[」』”’\"')）]*")
_SOFT_END = re.compile(r"[，,、；;：:]")
SOFT_SPLIT_LEN = 28  # 句子太長時，在逗號處先切，降低第一句的等待時間


class SentenceSplitter:
    def __init__(self):
        self._buf = ""

    def feed(self, delta: str) -> list[str]:
        self._buf += delta
        out = []
        while True:
            m = _HARD_END.search(self._buf)
            # 句尾標點剛好在緩衝區最後面時，先等下一段：後面可能還有右引號要跟著這一句
            if m and m.end() < len(self._buf):
                out.append(self._buf[: m.end()])
                self._buf = self._buf[m.end():]
                continue
            if len(self._buf) >= SOFT_SPLIT_LEN:
                soft = [s.end() for s in _SOFT_END.finditer(self._buf)]
                if soft:
                    out.append(self._buf[: soft[-1]])
                    self._buf = self._buf[soft[-1]:]
                    continue
            break
        return [s.strip() for s in out if s.strip()]

    def flush(self) -> list[str]:
        rest, self._buf = self._buf.strip(), ""
        return [rest] if rest else []
