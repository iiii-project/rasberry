"""啟動語音陪伴角色「金鶴」。

    python backend/main.py          # 語音模式（麥克風 + 喇叭）
    python backend/main.py --text   # 鍵盤打字、喇叭回答（開發測試用）
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app import net  # noqa: E402
from app.companion import Companion  # noqa: E402
from app.config import load_config  # noqa: E402


async def main(args) -> None:
    cfg = load_config(args.config)
    # 必須在事件迴圈裡建立（Python 3.7~3.9 的 asyncio.Queue / Event 會綁定建立時的迴圈）
    companion = Companion(cfg, use_mic=not args.text)
    try:
        if args.text:
            await companion.run_text_loop()
        else:
            await companion.run_voice_loop()
    finally:
        await companion.close()
        await net.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", help="設定檔路徑（預設為專案根目錄的 config.yaml）")
    parser.add_argument("--text", action="store_true", help="不使用麥克風，改用鍵盤輸入")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(main(args))
    except (KeyboardInterrupt, EOFError):
        pass
