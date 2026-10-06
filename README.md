# 語音陪伴角色「金鶴」（Google AIY Voice Kit V2）

把 AIY Voice Kit 變成一個會聊天的實體角色：對它說話，它用語音回答。不需要螢幕，按鈕上的燈號顯示它現在的狀態。

專為 **Raspberry Pi Zero WH**（單核 armv6、512MB RAM）設計：樹莓派只負責錄音和播放，語音辨識、對話、語音合成都交給雲端。

```
arecord ──► VAD 斷句 ─(wav)─► 雲端語音辨識 ──► 雲端 LLM（串流）
 （或按住按鈕）                                   │ 一句一句
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

## 燈號與操作

| 燈號 | 狀態 |
|---|---|
| 恆亮 | 在聽（VAD 模式）／錄音中（按鈕模式） |
| 閃爍 | 辨識、思考中 |
| 呼吸燈 | 說話中 |
| 閃 3 下 | 發生錯誤 |
| 熄滅 | 等待按鈕（按鈕模式） |

`config.yaml` 的 `audio.mode`：
- `vad`（預設）：直接說話，自動偵測說話開始和結束
- `button`：按住頂部按鈕說話、放開送出。環境吵雜時比較準，也不會被旁人聊天觸發

聽到一句話時會先「嗶」一聲。

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
2. 安裝 `mpg123`、`alsa-utils`；舊版 Buster 映像檔的套件來源失效時，會自動改用 `legacy.raspbian.org`
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
```

## 設定（`config.yaml`）

- `character.persona`：角色人設
- `llm.model`：預設 `gpt-4o-mini`
- `stt.model`：預設 `gpt-4o-mini-transcribe`；`stt.prompt` 可以放常出現的專有名詞，提高辨識率
- `tts.voice`、`rate`、`pitch`：聲音設定
- `audio.mode`：`vad` / `button`
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
  app/audio_in.py     arecord 錄音 + VAD 斷句 / 按鈕錄音
  app/stt.py          雲端語音辨識
  app/llm.py          雲端 LLM 串流對話與對話記憶
  app/text_stream.py  串流切句
  app/tts.py          edge-tts 語音合成（串流）
  app/audio_out.py    mpg123 播放、提示音
  app/hardware.py     AIY 按鈕與燈號
  app/net.py          共用 HTTP 連線、API 設定
  app/companion.py    主流程
scripts/setup.sh      環境安裝腳本
scripts/check_env.py  環境檢查
deploy/               systemd 服務
```

## 常見問題

- **`apt-get update` 失敗**：安裝腳本會自動把 Buster 的套件來源改成 `legacy.raspbian.org`；若仍失敗，多半是映像檔裡其他第三方來源過期，可以先用 `--skip-apt` 並手動安裝 `mpg123`
- **聽到自己的聲音一直自言自語**：程式在說話時會暫停收音；仍有問題就把 `ECHO_GUARD_S`（`companion.py`）調大，或改用 `button` 模式
- **沒有聲音 / 錄不到音**：用 `arecord -d 3 test.wav && aplay test.wav` 確認 Voice Bonnet 正常，必要時在 `config.yaml` 指定 ALSA 裝置
- **反應慢**：大部分時間花在網路上，確認 Wi-Fi 訊號；也可以把 `silence_ms` 調小一點（例如 800）
