#!/usr/bin/env bash
# 金鶴環境安裝腳本：Google AIY Voice Kit V2（Raspberry Pi Zero WH + Voice Bonnet）
#
#   bash scripts/setup.sh              # 完整安裝（互動式）
#   bash scripts/setup.sh --yes        # 全部使用預設值，不詢問
#   bash scripts/setup.sh --check      # 只做環境檢查，不安裝
#
# 其他選項：--skip-apt（不裝系統套件）、--no-service（不設定開機自動啟動）
# 可以重複執行；已完成的步驟會自動略過。

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv"
LOG="$ROOT/setup.log"
SERVICE=voice-companion

ASSUME_YES=0
SKIP_APT=0
NO_SERVICE=0
CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    -y|--yes) ASSUME_YES=1 ;;
    --skip-apt) SKIP_APT=1 ;;
    --no-service) NO_SERVICE=1 ;;
    --check) CHECK_ONLY=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "未知選項：$arg（用 --help 查看說明）"; exit 2 ;;
  esac
done

# ---------- 輸出工具 ----------
STEP=0
step() { STEP=$((STEP + 1)); printf '\n\033[1;36m[%d] %s\033[0m\n' "$STEP" "$*"; }
info() { printf '    %s\n' "$*"; }
okay() { printf '    \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '    \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31m✗ %s\033[0m\n    詳細記錄：%s\n' "$*" "$LOG"; exit 1; }
run()  { echo "+ $*" >>"$LOG"; "$@" >>"$LOG" 2>&1; }

ask() {  # ask "問題" 預設(y/n)
  local prompt=$1 default=$2 reply
  if [ "$ASSUME_YES" = 1 ] || [ ! -t 0 ]; then
    [ "$default" = y ]
    return
  fi
  if [ "$default" = y ]; then prompt="$prompt [Y/n] "; else prompt="$prompt [y/N] "; fi
  read -r -p "    $prompt" reply
  reply=${reply:-$default}
  [[ "$reply" =~ ^[Yy] ]]
}

cd "$ROOT" || exit 1
echo "=== setup $(date) ===" >>"$LOG"

if [ "$CHECK_ONLY" = 1 ]; then
  [ -x "$VENV/bin/python" ] || die "尚未安裝，請先執行 bash scripts/setup.sh"
  exec "$VENV/bin/python" scripts/check_env.py
fi

printf '\033[1m金鶴 環境安裝\033[0m  （%s）\n' "$ROOT"

# ---------- 1. 系統檢查 ----------
step "檢查系統"
[ "$(id -u)" -ne 0 ] || die "請不要用 sudo 執行，直接用一般使用者執行：bash scripts/setup.sh"

MODEL=$(tr -d '\0' 2>/dev/null </proc/device-tree/model || echo "非樹莓派")
CODENAME=$(. /etc/os-release 2>/dev/null && echo "${VERSION_CODENAME:-unknown}")
info "硬體：$MODEL"
info "系統：$(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME")（$(uname -m)）"
info "記憶體：$(free -m | awk '/^Mem:/ {print $2}') MB"
case "$MODEL" in
  *"Zero W"*) okay "Pi Zero W：使用雲端辨識模式，可以正常運作" ;;
  "非樹莓派") warn "不是樹莓派，將以一般 Linux 安裝（按鈕與燈號不可用）" ;;
esac

command -v python3 >/dev/null || die "找不到 python3"
PYVER=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
python3 -c 'import sys; sys.exit(sys.version_info < (3, 7))' || die "Python $PYVER 太舊，需要 3.7 以上"
okay "Python $PYVER"

# 只要拿得到任何 HTTP 回應就代表網路正常（curl 不存在時改用 python）
if ! curl -s -m 10 -o /dev/null https://api.openai.com 2>/dev/null \
   && ! python3 -c 'import urllib.request as u; u.urlopen("https://www.google.com", timeout=10)' 2>/dev/null; then
  die "無法連線到網際網路，請先設定 Wi-Fi（sudo raspi-config → System Options → Wireless LAN）"
fi
okay "網路連線正常"

# ---------- 2. 系統套件 ----------
step "安裝系統套件（mpg123、alsa-utils、python3-venv）"
APT_PKGS="mpg123 alsa-utils python3-venv python3-pip curl"

missing_pkgs() {
  local p out=""
  for p in $APT_PKGS; do
    dpkg -s "$p" >/dev/null 2>&1 || out="$out $p"
  done
  echo "$out"
}

fix_buster_sources() {
  # Raspbian Buster 已停止支援，套件來源搬到 legacy.raspbian.org
  if grep -qs 'raspbian.raspberrypi.org' /etc/apt/sources.list; then
    warn "Buster 套件來源已失效，改用 legacy.raspbian.org（原檔備份為 sources.list.bak）"
    sudo cp -n /etc/apt/sources.list /etc/apt/sources.list.bak
    sudo sed -i 's#raspbian.raspberrypi.org/raspbian#legacy.raspbian.org/raspbian#g' /etc/apt/sources.list
  fi
}

if [ "$SKIP_APT" = 1 ]; then
  warn "已略過（--skip-apt）"
elif [ -z "$(missing_pkgs)" ]; then
  okay "已安裝"
else
  info "需要安裝：$(missing_pkgs)"
  info "（需要 sudo 密碼）"
  sudo -v || die "無法取得 sudo 權限"
  if ! run sudo apt-get update; then
    [ "$CODENAME" = buster ] && fix_buster_sources
    run sudo apt-get update || warn "apt-get update 仍有錯誤（可能是第三方來源過期），嘗試繼續安裝"
  fi
  # shellcheck disable=SC2046
  run sudo apt-get install -y $(missing_pkgs) || die "系統套件安裝失敗"
  okay "完成"
fi

# AIY 的按鈕/燈號需要 gpio 權限，錄音播放需要 audio 權限
for g in audio gpio i2c spi; do
  if getent group "$g" >/dev/null && ! id -nG "$USER" | grep -qw "$g"; then
    run sudo usermod -aG "$g" "$USER" && warn "已把 $USER 加入 $g 群組（重新登入後生效）"
  fi
done

# ---------- 3. Python 虛擬環境 ----------
step "建立 Python 虛擬環境"
if [ -x "$VENV/bin/python" ]; then
  okay "已存在：$VENV"
else
  # --system-site-packages：共用 AIY 映像檔內建的 aiy 函式庫
  run python3 -m venv --system-site-packages "$VENV" || die "建立虛擬環境失敗（是否已安裝 python3-venv？）"
  okay "建立完成"
fi

PIP_ARGS=(--disable-pip-version-check)
case "$(uname -m)" in
  armv6l|armv7l)
    # piwheels 提供預先編譯好的 ARM 套件，Pi Zero 上就不用花好幾小時編譯
    grep -qs piwheels /etc/pip.conf || PIP_ARGS+=(--extra-index-url https://www.piwheels.org/simple)
    ;;
esac

step "安裝 Python 套件（Pi Zero 上約需 5~10 分鐘）"
run "$VENV/bin/python" -m pip install "${PIP_ARGS[@]}" --upgrade pip wheel || warn "pip 更新失敗，使用現有版本繼續"
run "$VENV/bin/python" -m pip install "${PIP_ARGS[@]}" -r backend/requirements.txt \
  || die "Python 套件安裝失敗"
okay "完成"

# ---------- 4. API 金鑰 ----------
step "設定 API 金鑰（.env）"
env_key() { grep -E '^LLM_API_KEY=' .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'"' "; }
[ -f .env ] || cp .env.example .env
KEY=$(env_key)
if [ -n "$KEY" ] && [ "$KEY" != "sk-..." ]; then
  okay "已設定（…${KEY: -4}）"
elif [ "$ASSUME_YES" = 0 ] && [ -t 0 ]; then
  read -r -s -p "    請輸入 OpenAI API key（輸入時不會顯示）：" KEY; echo
  if [ -n "$KEY" ]; then
    if grep -qE '^LLM_API_KEY=' .env; then
      sed -i "s#^LLM_API_KEY=.*#LLM_API_KEY=$KEY#" .env
    else
      echo "LLM_API_KEY=$KEY" >>.env
    fi
    chmod 600 .env
    okay "已寫入 .env"
  else
    warn "略過，之後請手動編輯 $ROOT/.env"
  fi
else
  warn "尚未設定，請編輯 $ROOT/.env 填入 LLM_API_KEY"
fi

# ---------- 5. 音訊測試 ----------
step "測試喇叭與麥克風"
if aplay -l 2>/dev/null | grep -q '^card'; then
  info "播放裝置：$(aplay -l | awk -F': ' '/^card/ {print $2; exit}')"
else
  warn "找不到播放裝置"
fi
if arecord -l 2>/dev/null | grep -q '^card'; then
  info "錄音裝置：$(arecord -l | awk -F': ' '/^card/ {print $2; exit}')"
else
  warn "找不到錄音裝置"
fi

if "$VENV/bin/python" -c "import sys; sys.path.insert(0, 'backend'); from app.audio_out import _make_beep; sys.stdout.buffer.write(_make_beep() * 2)" \
   | aplay -q -t raw -f S16_LE -r 16000 -c 1 2>>"$LOG"; then
  okay "已播放測試音（應該聽到兩聲「嗶」）"
else
  warn "喇叭測試失敗，請檢查 ALSA 設定或 config.yaml 的 audio.output_device"
fi

if ask "要錄 3 秒聲音再播放，測試麥克風嗎？" n; then
  TMP=$(mktemp --suffix=.wav)
  info "請在「嗶」之後說話…"
  aplay -q -t raw -f S16_LE -r 16000 -c 1 < <("$VENV/bin/python" -c "import sys; sys.path.insert(0, 'backend'); from app.audio_out import _make_beep; sys.stdout.buffer.write(_make_beep())") 2>/dev/null
  if arecord -q -d 3 -f S16_LE -r 16000 -c 1 "$TMP" 2>>"$LOG"; then
    info "播放錄音…"
    aplay -q "$TMP" 2>>"$LOG" && okay "麥克風測試完成（有聽到自己的聲音就代表正常）"
  else
    warn "錄音失敗，請檢查 config.yaml 的 audio.input_device"
  fi
  rm -f "$TMP"
fi

# ---------- 6. 開機自動啟動 ----------
step "設定開機自動啟動（systemd）"
if [ "$NO_SERVICE" = 1 ] || ! command -v systemctl >/dev/null; then
  warn "已略過"
else
  sed -e "s|__ROOT__|$ROOT|g" -e "s|__USER__|$USER|g" -e "s|__UID__|$(id -u)|g" \
    deploy/$SERVICE.service | sudo tee /etc/systemd/system/$SERVICE.service >/dev/null \
    && run sudo systemctl daemon-reload \
    && run sudo systemctl enable $SERVICE \
    && okay "已設定 $SERVICE 開機自動啟動" \
    || warn "systemd 設定失敗"
fi

# ---------- 7. 環境檢查 ----------
step "環境檢查"
if "$VENV/bin/python" scripts/check_env.py; then
  CHECK_OK=1
else
  CHECK_OK=0
fi

# ---------- 完成 ----------
echo
if [ "$CHECK_OK" = 1 ]; then
  printf '\033[1;32m✅ 安裝完成！\033[0m\n'
  if [ "$NO_SERVICE" = 0 ] && command -v systemctl >/dev/null && ask "現在就啟動金鶴嗎？" y; then
    sudo systemctl restart $SERVICE && okay "已啟動，對著麥克風說話試試看！"
  fi
else
  printf '\033[1;33m⚠️  安裝完成，但有檢查項目未通過，請依上方提示修正後執行：bash scripts/setup.sh --check\033[0m\n'
fi
cat <<EOF

常用指令：
  查看記錄    journalctl -u $SERVICE -f
  重新啟動    sudo systemctl restart $SERVICE
  停止        sudo systemctl stop $SERVICE
  手動執行    $VENV/bin/python backend/main.py
  文字測試    $VENV/bin/python backend/main.py --text
  環境檢查    bash scripts/setup.sh --check
EOF
