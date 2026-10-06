# 語音陪伴角色「金鶴」（Google AIY Voice Kit V2）

把 AIY Voice Kit 變成廟公「金鶴」的實體版：按下按鈕對它說話，它用語音回答，還能幫你**求籤、解籤**。
不需要螢幕，按鈕上的 RGB 燈顯示它現在的狀態。

金鶴與 [iiii-project-backend](../iiii-project-backend)（AI 求籤系統）的 Live2D 金鶴是同一個角色，
籤詩資料與求籤紀錄都來自同一個後端。

專為 **Raspberry Pi Zero WH**（單核 armv6、512MB RAM）設計：樹莓派只負責錄音和播放，語音辨識、對話、語音合成都交給雲端。

```
按下按鈕 → arecord ─(wav)─► 雲端語音辨識 ──► 雲端 LLM（串流）◄──► 求籤後端 REST API
                                                 │ 一句一句          （查籤、抽籤、擲筊、紀錄）
                                                 ▼
喇叭 ◄── mpg123 ◄──── mp3 片段即時串流 ◄──── edge-tts
```

## 為什麼能在 Pi Zero 上跑

| 做法 | 效果 |
|---|---|
| 語音辨識用雲端 `gpt-4o-mini-transcribe` | 不在本機跑 Whisper，CPU/RAM 幾乎零負擔 |
| 錄音用 `arecord`、播放用 `mpg123` / `aplay` | 不需要 numpy、PortAudio，Python 不做任何音訊解碼 |
| 用 aiohttp 直接呼叫 API，不用 openai SDK | 避開 pydantic-core 等需要用 Rust 編譯的套件 |
| 一次回覆只開一個 mpg123，mp3 片段邊合成邊送進去 | 第一句一合成出來就開始播，句子之間不停頓 |
| 共用同一個 HTTPS 連線 | 省去每次 TLS 握手（Pi Zero 上很慢） |
| 只依賴 5 個 Python 套件，piwheels 都有 armv6 預編譯版 | 安裝時不用在樹莓派上編譯 |
| 支援 Python 3.7 | 可直接用 AIY 官方映像檔 |

## 操作與燈號

**按下按鈕後才開始收音**（`config.yaml` 的 `audio.mode: button`，預設）：
- **按一下就放開**：開始收音，說完停頓 1 秒自動結束；6 秒內沒開口就取消
- **按住說話**：按住超過 1 秒，就錄到放開為止

開始處理時會先「嗶」一聲。**金鶴說話時按一下按鈕可以打斷它**，打斷的那一按會直接開始收你的下一句。

| 燈號 | 狀態 |
|---|---|
| 🟢 綠燈微亮 | 待機，等你按按鈕 |
| 🟢 綠燈全亮 | 收音中 |
| 🟢 綠燈呼吸 | 辨識、思考、查籤中 |
| 🔴 紅燈閃爍 | 金鶴說話中 |
| 🔴 紅燈快閃 | 發生錯誤 |

顏色與閃法可以在 `config.yaml` 的 `hardware.leds` 調整。不想用按鈕時可改 `audio.mode: vad`（一直聽、自動偵測說話，但容易被旁人聊天觸發）。

出錯時（斷網、API 失敗）金鶴會念「抱歉，我剛剛恍神了」。這句話第一次合成後會快取在 `cache/`，之後斷網也念得出來。麥克風故障時程式會結束，由 systemd 在 3 秒後自動重新啟動。

## 求籤後端

金鶴透過 LLM 的工具呼叫（function calling）使用後端資料，不直接連資料庫：

| 你說 | 金鶴做的事 | 後端 API |
|---|---|---|
| 「幫我求一支籤，我想問換工作」 | 建立求籤紀錄 → 祈求 → 抽籤 → 擲筊，念出籤詩並解說 | `POST /divinations/` → `prayer-complete` → `draw` → `blocks` |
| 「幫我解第十籤」 | 查詢籤詩並解說 | `GET /fortune-sets/{籤系}/fortunes/{籤號}/` |
| 「我之前求過什麼？」 | 查這台裝置的求籤紀錄 | `GET /divinations/?anonymous_user_id=…` |

- 還沒說要問什麼事時，金鶴會先問清楚再抽籤；抽籤前會先說「好，我幫你搖一支籤」
- 抽到的籤會記在「本次求籤資料」，之後的追問都會接著這支籤回答
- 擲出聖筊後，會在背景請後端完成解籤，所以這筆紀錄在網頁上也是完整的
- 這台裝置以匿名身分求籤，裝置 ID 第一次啟動時產生，存在 `cache/device_id`
- **每一次求籤都會在後端資料庫建立一筆真的紀錄**

設定：`.env` 的 `BACKEND_URL`（預設 `https://iii.dev-serve.me/api/v1`）。如果自架後端，後端的 `DJANGO_ALLOWED_HOSTS` 必須包含這個主機名稱，否則會收到 HTTP 400。
不需要求籤功能時，把 `config.yaml` 的 `backend.enabled` 設成 `false`。

## 安裝

1. 用 AIY 官方映像檔（已內建 Voice Bonnet 驅動與 `aiy` 按鈕/燈號函式庫），連上 Wi-Fi
2. 把這個資料夾複製到樹莓派，例如 `~/voice-companion`
3. 執行安裝腳本（用一般使用者執行，不要加 sudo）：

```bash
cd ~/voice-companion
bash scripts/setup.sh
```

安裝腳本會依序：

1. 檢查硬體、Python 版本（3.7 以上）和網路
2. 修復 Buster 的套件來源（見下方「apt-get update 失敗」），再安裝 `mpg123`、`alsa-utils`
3. 把使用者加入 `audio` / `gpio` 群組
4. 建立 `.venv`（共用系統的 aiy 函式庫），透過 piwheels 安裝預先編譯好的套件
5. 詢問 OpenAI API key，寫入 `.env`
6. 播放測試音，並可選擇錄 3 秒聲音測試麥克風
7. 設定開機自動啟動（systemd）
8. 執行完整環境檢查（套件、指令、API key、模型、語音合成），最後詢問是否立即啟動

腳本可以重複執行，已完成的步驟會自動略過；完整記錄寫在 `setup.log`。

| 選項 | 說明 |
|---|---|
| `--check` | 只做環境檢查，不安裝 |
| `--yes` | 全部使用預設值，不詢問 |
| `--skip-apt` | 不安裝系統套件 |
| `--no-service` | 不設定開機自動啟動 |

常用指令：

```bash
journalctl -u voice-companion -f        # 查看記錄
sudo systemctl restart voice-companion  # 重新啟動
bash scripts/setup.sh --check           # 環境檢查
```

## `.env`

```
LLM_API_KEY=sk-...        # 必填
LLM_BASE_URL=             # 選填，不填就用 OpenAI 官方 API
LLM_MODEL=                # 選填，覆蓋 config.yaml 的 llm.model
LLM_TIMEOUT_SECONDS=60
# STT_API_KEY= / STT_BASE_URL=   語音辨識用不同的服務時才需要
BACKEND_URL=https://iii.dev-serve.me/api/v1   # 求籤後端
```

## 設定（`config.yaml`）

- `character.persona`：角色人設
- `llm.model`：預設 `gpt-4o-mini`
- `stt.model`：預設 `gpt-4o-mini-transcribe`；`stt.prompt` 可以放常出現的專有名詞，提高辨識率
- `tts.voice`、`rate`、`pitch`：聲音設定
- `audio.mode`：`button`（按下按鈕才收音）/ `vad`
- `backend.*`：求籤後端設定（見上方「求籤後端」）
- `hardware.leds`：按鈕燈號的顏色與閃法
- `audio.input_device` / `output_device`：ALSA 裝置名稱（`arecord -L`、`aplay -L` 列出），`null` 使用系統預設
- `audio.vad_aggressiveness`、`silence_ms`：環境吵就調高靈敏度；常被截斷就把 `silence_ms` 調長
- `hardware.aiy`：是否使用 AIY 按鈕與燈號

## 在電腦上開發

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
cp .env.example .env    # 填入 LLM_API_KEY
.venv/bin/python backend/main.py --text   # 鍵盤打字，喇叭回答
.venv/bin/python backend/main.py          # 麥克風 + 喇叭
```

需要 `arecord`/`aplay`（alsa-utils）和 `mpg123`。沒有 aiy 函式庫時，按鈕與燈號會自動停用。

## 專案結構

```
backend/
  main.py             進入點（--text、--config、-v）
  app/audio_in.py     arecord 錄音 + 按鈕收音 / VAD 斷句
  app/stt.py          雲端語音辨識
  app/llm.py          雲端 LLM 串流對話、工具呼叫與對話記憶
  app/tools.py        給 LLM 呼叫的工具（求籤、查籤、查紀錄）
  app/fortune_backend.py  求籤後端 REST API 用戶端
  app/text_stream.py  串流切句
  app/tts.py          edge-tts 語音合成（串流）
  app/audio_out.py    mpg123 播放、提示音
  app/hardware.py     AIY 按鈕與 RGB 燈號
  app/net.py          共用 HTTP 連線、API 設定
  app/companion.py    主流程
scripts/setup.sh      環境安裝腳本
scripts/fix_apt.sh    修復 Buster 的 apt-get update 錯誤
scripts/check_env.py  環境檢查
deploy/               systemd 服務
```

## 常見問題

- **`apt-get update` 失敗**：AIY 官方映像檔是 Raspbian Buster，原本的套件來源已經失效。執行 `bash scripts/fix_apt.sh`（`setup.sh` 會自動執行）會：
  - 把 `raspbian.raspberrypi.org`（已下架，404）改成 `legacy.raspbian.org`
  - 停用已關閉的 Google AIY 來源 `packages.cloud.google.com`（404；已安裝的 aiy 套件不受影響）
  - 用 `--allow-releaseinfo-change` 接受 Buster 從 `stable` 改名為 `oldoldstable`
  - 時間錯誤（Pi Zero 沒有 RTC 電池）時先校正時間

  修改前會把設定備份到 `/etc/apt/backup-<時間>/`。如果仍然失敗，請把錯誤訊息完整貼出來
- **金鶴說系統有狀況、沒辦法求籤**：執行 `bash scripts/setup.sh --check` 看「求籤後端」那幾項；常見原因是 `BACKEND_URL` 打錯或後端的 `DJANGO_ALLOWED_HOSTS` 沒有包含這個主機
- **聽到自己的聲音一直自言自語**（VAD 模式）：程式在說話時會暫停收音；仍有問題就把 `ECHO_GUARD_S`（`companion.py`）調大，或改用 `button` 模式
- **沒有聲音 / 錄不到音**：用 `arecord -d 3 test.wav && aplay test.wav` 確認 Voice Bonnet 正常，必要時在 `config.yaml` 指定 ALSA 裝置
- **反應慢**：大部分時間花在網路上，確認 Wi-Fi 訊號；也可以把 `silence_ms` 調小一點（例如 800）
