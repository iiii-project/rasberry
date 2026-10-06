"""對話大腦：OpenAI 相容的 Chat Completions API（串流回覆，邊生成邊交給 TTS）。"""

from __future__ import annotations

import json
import logging
import os
from typing import AsyncIterator

import aiohttp

from . import net

log = logging.getLogger(__name__)

VOICE_RULES = """
# 回覆規則（這是語音對話，你的文字會被直接念出來）
- 回覆簡短口語，通常一到三句話，像面對面聊天。
- 不要使用 markdown、條列、表情符號、括號動作描述或網址。
- 數字、單位用念得出來的方式寫。
- 使用者的話來自語音辨識，可能有錯字；依語意理解，聽不懂就自然地請對方再說一次。
"""


class Brain:
    def __init__(self, llm_cfg: dict, character_cfg: dict):
        self.base_url, self.api_key = net.api_settings("LLM")
        self.model = os.getenv("LLM_MODEL") or llm_cfg.get("model", "gpt-4o-mini")
        self.reasoning_effort = llm_cfg.get("reasoning_effort")
        self.max_tokens = llm_cfg.get("max_tokens", 1024)
        self.max_turns = llm_cfg.get("max_history_turns", 8)
        self.timeout = float(os.getenv("LLM_TIMEOUT_SECONDS") or 60)
        self.system = character_cfg["persona"].strip() + "\n" + VOICE_RULES
        self.history = []  # type: list[dict]

    def reset(self) -> None:
        self.history.clear()

    def _messages(self) -> list:
        window = self.history[-(self.max_turns * 2 + 1):]
        return [{"role": "system", "content": self.system}] + window

    async def chat(self, user_text: str) -> AsyncIterator[str]:
        """串流產生回覆文字片段。"""
        self.history.append({"role": "user", "content": user_text})
        payload = {
            "model": self.model,
            "messages": self._messages(),
            "max_completion_tokens": self.max_tokens,
            "stream": True,
        }
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort

        reply = []
        try:
            async with net.session().post(
                self.base_url + "/chat/completions",
                json=payload,
                headers={"Authorization": "Bearer " + self.api_key},
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError("LLM API {}: {}".format(resp.status, (await resp.text())[:300]))
                # Server-Sent Events：每行 "data: {...}"，最後是 "data: [DONE]"
                async for raw in resp.content:
                    line = raw.decode("utf-8", "ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    choices = json.loads(data).get("choices") or []
                    delta = choices[0].get("delta", {}).get("content") if choices else None
                    if delta:
                        reply.append(delta)
                        yield delta
        except BaseException:
            self.history.pop()
            raise

        self.history.append({"role": "assistant", "content": "".join(reply) or "…"})
