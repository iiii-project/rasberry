"""iiii-project-backend（AI 求籤系統）的 REST API 用戶端。

金鶴不直接連資料庫：資料庫在後端伺服器上，求籤流程（祈求 → 抽籤 → 擲筊）的規則也在後端，
走 API 才會留下正確的求籤紀錄，網頁上也看得到。API 文件見 iiii-project-backend/docs/API.md。

這台裝置用匿名身分求籤（anonymous_user_id = 裝置 ID），同一台裝置可以查到自己過去的紀錄。
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid

import aiohttp

from . import net
from .config import PROJECT_ROOT

log = logging.getLogger(__name__)

CATEGORIES = ("love", "career", "study", "wealth", "health", "family", "relationship", "travel", "other")
CATEGORY_NAMES = {
    "love": "感情", "career": "事業", "study": "學業", "wealth": "財運", "health": "健康",
    "family": "家庭", "relationship": "人際", "travel": "出行", "other": "其他",
}
MAX_BLOCK_ATTEMPTS = 3
DEVICE_ID_FILE = PROJECT_ROOT / "cache" / "device_id"


class BackendError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__("{}: {}".format(code, message))
        self.code = code
        self.message = message


def _device_id(cfg: dict) -> str:
    """設定檔有指定就用；否則第一次啟動時產生一個並存起來，之後固定使用。"""
    if cfg.get("device_id"):
        return str(cfg["device_id"])[:100]
    if DEVICE_ID_FILE.exists():
        return DEVICE_ID_FILE.read_text().strip()
    device_id = "jinhe-pi-" + uuid.uuid4().hex[:12]
    DEVICE_ID_FILE.parent.mkdir(exist_ok=True)
    DEVICE_ID_FILE.write_text(device_id)
    return device_id


class FortuneBackend:
    def __init__(self, cfg: dict):
        self.base_url = (os.getenv("BACKEND_URL") or cfg.get("base_url") or "").rstrip("/")
        if not self.base_url:
            raise RuntimeError("沒有設定後端網址，請在 .env 設定 BACKEND_URL")
        self.fortune_set = cfg.get("fortune_set") or None  # None = 後端的預設籤系
        self.device_id = _device_id(cfg)
        self.timeout = aiohttp.ClientTimeout(total=float(cfg.get("timeout_s", 15)), sock_connect=5)
        self._background = set()  # 背景解籤任務

    async def close(self) -> None:
        for task in list(self._background):
            task.cancel()

    async def _request(self, method: str, path: str, json: dict = None, params: dict = None,
                       timeout: aiohttp.ClientTimeout = None) -> dict:
        # 只有 GET 會重試；POST 會改變求籤紀錄的狀態，重送可能出錯
        attempts = (1, 2) if method == "GET" else (2,)
        for attempt in attempts:
            try:
                async with net.session().request(
                    method, self.base_url + path, json=json, params=params,
                    timeout=timeout or self.timeout,
                ) as resp:
                    try:
                        body = await resp.json(content_type=None)
                    except ValueError:
                        raise BackendError("BAD_RESPONSE", self._explain_non_json(resp.status))
                break
            except net.RETRYABLE as e:
                if attempt == 2:
                    raise BackendError("UNREACHABLE", "連不上後端：{!r}".format(e))
                log.warning("後端連線失敗，重試一次：%r", e)
                await asyncio.sleep(0.5)

        if not isinstance(body, dict) or not body.get("success"):
            err = (body or {}).get("error") or {} if isinstance(body, dict) else {}
            raise BackendError(err.get("code", "HTTP_{}".format(resp.status)), err.get("message", str(body)[:200]))
        return body.get("data") or {}

    def _explain_non_json(self, status: int) -> str:
        """後端回 HTML 而不是 JSON 時，猜出最可能的原因（Django 錯誤頁對使用者沒有幫助）。"""
        host = self.base_url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
        if status == 400:
            return "HTTP 400，通常是後端的 DJANGO_ALLOWED_HOSTS 沒有包含「{}」".format(host)
        if status == 404:
            return "HTTP 404，請確認 BACKEND_URL 結尾是 /api/v1"
        if status in (502, 503, 504):
            return "HTTP {}，後端暫時無法服務".format(status)
        return "HTTP {}，後端回傳的不是 JSON".format(status)

    # ---- 查詢 ----
    async def health(self) -> bool:
        data = await self._request("GET", "/health/")
        return data.get("status") == "ok"

    async def default_fortune_set(self) -> str:
        if self.fortune_set:
            return self.fortune_set
        items = (await self._request("GET", "/fortune-sets/")).get("items") or []
        default = next((s for s in items if s.get("is_default")), items[0] if items else None)
        if not default:
            raise BackendError("FORTUNE_SET_NOT_FOUND", "後端沒有可用的籤系")
        self.fortune_set = default["code"]
        return self.fortune_set

    async def lookup_fortune(self, number: int) -> dict:
        code = await self.default_fortune_set()
        return await self._request("GET", "/fortune-sets/{}/fortunes/{}/".format(code, int(number)))

    async def recent_divinations(self, limit: int = 5) -> list:
        data = await self._request("GET", "/divinations/", params={"anonymous_user_id": self.device_id})
        return (data.get("items") or [])[:limit]

    # ---- 求籤：建立紀錄 → 祈求 → 抽籤 → 擲筊 ----
    async def draw_fortune(self, question: str, categories: list) -> dict:
        categories = [c for c in categories if c in CATEGORIES] or ["other"]
        payload = {
            "question": question.strip()[:300],
            "categories": categories,
            "interaction_mode": "click",  # 後端只接受 click / motion
            "anonymous_user_id": self.device_id,
        }
        if self.fortune_set:
            payload["fortune_set_code"] = self.fortune_set
        session = await self._request("POST", "/divinations/", json=payload)
        sid = session["session_id"]
        await self._request("POST", "/divinations/{}/prayer-complete/".format(sid))
        session = await self._request("POST", "/divinations/{}/draw/".format(sid))

        block = {}
        for _ in range(MAX_BLOCK_ATTEMPTS):
            block = await self._request("POST", "/divinations/{}/blocks/".format(sid))
            if block.get("confirmed") or not block.get("remaining_attempts"):
                break

        if block.get("confirmed"):
            self._finish_interpretation(sid)
        session["block"] = block
        return session

    def _finish_interpretation(self, session_id: str) -> None:
        """在背景請後端完成解籤，讓這筆紀錄在網頁上也是完整的（後端抽籤時已預先開始生成，
        通常很快）。金鶴自己會先口頭解說，不等這個結果。"""
        async def run():
            try:
                await self._request(
                    "POST", "/divinations/{}/interpret/".format(session_id), json={},
                    timeout=aiohttp.ClientTimeout(total=180),
                )
                log.info("後端解籤完成：%s", session_id)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.info("後端解籤沒有完成（不影響對話）：%r", e)

        task = asyncio.ensure_future(run())
        self._background.add(task)
        task.add_done_callback(self._background.discard)
