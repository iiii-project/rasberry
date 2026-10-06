"""檢查金鶴的執行環境是否就緒。

    .venv/bin/python scripts/check_env.py           # 完整檢查（含 API 與網路）
    .venv/bin/python scripts/check_env.py --offline # 只檢查本機

全部通過時結束碼為 0，否則為 1。只用到 Python 3.7 的語法。
"""

import argparse
import asyncio
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

failures = []


def ok(msg):
    print("  \033[32m✓\033[0m " + msg)


def warn(msg):
    print("  \033[33m!\033[0m " + msg)


def fail(msg, hint=""):
    failures.append(msg)
    print("  \033[31m✗\033[0m " + msg + ("\n      → " + hint if hint else ""))


def check_python():
    print("Python")
    v = sys.version_info
    if v >= (3, 7):
        ok("Python {}.{}.{}".format(v.major, v.minor, v.micro))
    else:
        fail("Python {}.{} 太舊".format(v.major, v.minor), "需要 3.7 以上")
    for mod, pkg in [("aiohttp", "aiohttp"), ("edge_tts", "edge-tts"), ("yaml", "pyyaml"),
                     ("dotenv", "python-dotenv"), ("webrtcvad", "webrtcvad-wheels")]:
        try:
            __import__(mod)
            ok("套件 " + pkg)
        except ImportError:
            fail("缺少套件 " + pkg, ".venv/bin/pip install -r backend/requirements.txt")


def check_commands():
    print("系統指令")
    for cmd, pkg in [("arecord", "alsa-utils"), ("aplay", "alsa-utils"), ("mpg123", "mpg123")]:
        if shutil.which(cmd):
            ok(cmd)
        else:
            fail("找不到 " + cmd, "sudo apt-get install -y " + pkg)


def check_hardware():
    print("AIY 硬體")
    try:
        import aiy.board  # noqa: F401
        ok("aiy 函式庫（按鈕與燈號可用）")
    except ImportError:
        warn("找不到 aiy 函式庫，按鈕與燈號會停用（不影響語音對話）")


def check_config():
    print("設定")
    try:
        from app.config import load_config
        cfg = load_config()
        ok("config.yaml（角色：{}，模式：{}）".format(
            cfg["character"].get("name", "?"), cfg["audio"].get("mode", "vad")))
    except Exception as e:
        fail("config.yaml 讀取失敗：{}".format(e))
        return None
    if not (ROOT / ".env").exists():
        fail("找不到 .env", "cp .env.example .env 後填入 LLM_API_KEY")
    try:
        from app import net
        net.api_settings("LLM")
        ok(".env 已設定 API key")
    except RuntimeError as e:
        fail(str(e))
    return cfg


async def check_online(cfg):
    import aiohttp
    from app import net

    print("網路與 API")
    try:
        base_url, api_key = net.api_settings("LLM")
    except RuntimeError:
        return
    llm_model = os.getenv("LLM_MODEL") or cfg["llm"].get("model")
    stt_model = cfg["stt"].get("model")
    try:
        async with net.session().get(
            base_url + "/models",
            headers={"Authorization": "Bearer " + api_key},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            if resp.status == 401:
                fail("API key 無效（401）", "檢查 .env 的 LLM_API_KEY")
                return
            if resp.status != 200:
                warn("無法列出模型（HTTP {}），略過模型檢查".format(resp.status))
            else:
                ok("API 連線正常（{}）".format(base_url))
                ids = {m.get("id") for m in (await resp.json()).get("data", [])}
                for kind, model in (("對話", llm_model), ("語音辨識", stt_model)):
                    if model in ids:
                        ok("{}模型 {} 可用".format(kind, model))
                    else:
                        fail("{}模型 {} 不在可用清單中".format(kind, model), "修改 config.yaml 或 .env 的模型名稱")
    except Exception as e:
        fail("無法連線到 {}：{}".format(base_url, e), "檢查網路 / Wi-Fi")

    try:
        from app.tts import TextToSpeech
        n = 0
        async for chunk in TextToSpeech(cfg["tts"]).stream("你好"):
            n += len(chunk)
        if n:
            ok("語音合成 edge-tts 正常（{}）".format(cfg["tts"].get("voice")))
        else:
            fail("edge-tts 沒有回傳音訊")
    except Exception as e:
        fail("edge-tts 失敗：{}".format(e), "檢查網路，或用 `edge-tts --list-voices` 確認聲音名稱")
    finally:
        await net.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="不檢查網路與 API")
    args = parser.parse_args()

    check_python()
    check_commands()
    check_hardware()
    cfg = check_config()
    if cfg is not None and not args.offline and not any("套件" in f for f in failures):
        asyncio.run(check_online(cfg))

    print()
    if failures:
        print("\033[31m有 {} 項檢查未通過\033[0m".format(len(failures)))
        sys.exit(1)
    print("\033[32m環境檢查全部通過 🎉\033[0m")


if __name__ == "__main__":
    main()
