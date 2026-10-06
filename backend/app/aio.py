"""asyncio 小工具。"""

from __future__ import annotations

import asyncio


async def cancel_and_wait(task: asyncio.Future) -> None:
    """取消子任務並等它真正結束（避免 "Task was destroyed but it is pending"）。
    不會把子任務的 CancelledError 誤當成自己被取消。"""
    if not task.done():
        task.cancel()
    await asyncio.wait([task])
    if not task.cancelled():
        task.exception()  # 標記例外已讀取，避免 "exception was never retrieved"
