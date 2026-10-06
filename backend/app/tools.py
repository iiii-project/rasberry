"""給 LLM 呼叫的工具（OpenAI function calling），背後接 iiii-project-backend 的資料。

每個工具回傳 (給 LLM 看的結果 JSON, 要記住的求籤資料或 None)。求籤資料會放進 system prompt
的資料區塊，就算對話紀錄被截斷，金鶴也一直記得這次抽到哪支籤。
"""

from __future__ import annotations

import asyncio
import json
import logging

from .fortune_backend import CATEGORIES, CATEGORY_NAMES, BackendError, FortuneBackend

log = logging.getLogger(__name__)

SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "draw_fortune",
            "description": "幫信眾求一支籤：在廟裡的系統建立求籤紀錄、抽籤、擲筊，回傳抽到的籤詩與各方面的解說。"
                           "必須先知道信眾想問什麼事才能呼叫。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "信眾想問的事，用一句完整的話描述，2 到 300 字"},
                    "categories": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(CATEGORIES)},
                        "description": "問題類別，可以多選：love 感情、career 事業、study 學業、wealth 財運、"
                                       "health 健康、family 家庭、relationship 人際、travel 出行、other 其他",
                    },
                },
                "required": ["question", "categories"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_fortune",
            "description": "用籤號查詢籤詩內容（例如信眾已經抽好籤、只是想請你解第幾籤）。",
            "parameters": {
                "type": "object",
                "properties": {"number": {"type": "integer", "minimum": 1, "description": "籤號"}},
                "required": ["number"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recent_divinations",
            "description": "查詢這台裝置最近的求籤紀錄（問過什麼、抽到哪一籤），用在信眾問起以前求過的籤時。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
]


def _meanings(fortune: dict, categories: list) -> dict:
    """只挑跟問題有關的分類解說，減少 token 與念出來的內容。"""
    out = {"整體": fortune.get("general_meaning", "")}
    for c in categories:
        text = fortune.get("{}_meaning".format(c))
        if text:
            out[CATEGORY_NAMES[c]] = text
    return out


def _fortune_view(fortune: dict, categories: list) -> dict:
    return {
        "籤": fortune.get("title") or "第{}籤".format(fortune.get("number")),
        "籤號": fortune.get("number"),
        "籤詩": fortune.get("poem", ""),
        "白話": fortune.get("translation", ""),
        "典故": fortune.get("story", ""),
        "解說": _meanings(fortune, categories),
    }


def _context_text(view: dict, question: str = "", categories: list = ()) -> str:
    lines = []
    if question:
        cats = "、".join(CATEGORY_NAMES.get(c, c) for c in categories)
        lines.append("信眾這次問的事：{}（類別：{}）".format(question, cats))
    lines.append("抽到：{}".format(view["籤"]))
    lines.append("籤詩：{}".format(view["籤詩"]))
    lines.append("白話：{}".format(view["白話"]))
    if view["典故"]:
        lines.append("典故：{}".format(view["典故"]))
    for k, v in view["解說"].items():
        lines.append("{}：{}".format(k, v))
    return "\n".join(lines)


class FortuneTools:
    schemas = SCHEMAS

    def __init__(self, backend: FortuneBackend):
        self.backend = backend

    async def call(self, name: str, arguments: str) -> tuple:
        try:
            args = json.loads(arguments or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments 不是物件")
            handler = getattr(self, "_" + name, None)
            if name not in {s["function"]["name"] for s in SCHEMAS} or handler is None:
                raise ValueError("沒有這個工具：{}".format(name))
            result, context = await handler(**args)
        except asyncio.CancelledError:
            raise
        except BackendError as e:
            log.warning("工具 %s 失敗：%s", name, e)
            result, context = {"error": e.message, "code": e.code}, None
        except (ValueError, TypeError) as e:
            log.warning("工具 %s 參數錯誤：%s", name, e)
            result, context = {"error": "參數錯誤：{}".format(e)}, None
        except Exception as e:  # 網路等非預期錯誤：讓金鶴自己跟信眾說明，不要整段失敗
            log.exception("工具 %s 發生錯誤", name)
            result, context = {"error": "廟裡的系統暫時連不上（{}）".format(type(e).__name__)}, None
        return json.dumps(result, ensure_ascii=False), context

    async def _draw_fortune(self, question: str, categories: list = None) -> tuple:
        if not isinstance(question, str) or len(question.strip()) < 2:
            raise ValueError("question 至少要 2 個字")
        categories = [c for c in (categories or []) if c in CATEGORIES] or ["other"]
        session = await self.backend.draw_fortune(question, categories)
        fortune = session.get("fortune")
        if not fortune:
            raise BackendError("NO_FORTUNE", "抽籤沒有成功，請再試一次")
        view = _fortune_view(fortune, categories)
        block = session.get("block") or {}
        view["擲筊"] = block.get("result_name") or "未擲筊"
        view["已確認"] = bool(block.get("confirmed"))
        log.info("🎋 抽到 %s（%s）", view["籤"], view["擲筊"])
        return view, _context_text(view, question, categories)

    async def _lookup_fortune(self, number: int) -> tuple:
        fortune = await self.backend.lookup_fortune(int(number))
        view = _fortune_view(fortune, list(CATEGORY_NAMES)[:-1])  # 不知道問題類別，全部附上
        return view, _context_text(view)

    async def _recent_divinations(self) -> tuple:
        items = await self.backend.recent_divinations()
        records = [
            {
                "時間": (s.get("created_at") or "")[:10],
                "問題": s.get("question", ""),
                "類別": "、".join(CATEGORY_NAMES.get(c, c) for c in s.get("categories") or []),
                "籤": (s.get("fortune") or {}).get("title") or "還沒抽籤",
            }
            for s in items
        ]
        return {"紀錄": records} if records else {"紀錄": [], "說明": "這台裝置還沒有求籤紀錄"}, None
