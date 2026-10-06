#!/usr/bin/env bash
# 修復舊版 Raspbian Buster（AIY 官方映像檔）的 apt-get update 錯誤
#
#   bash scripts/fix_apt.sh              # 校正時間 + 修復套件來源 + apt-get update
#   bash scripts/fix_apt.sh --time-only  # 只校正時間
#
# 會處理這些已知問題（2026 年實測）：
#   1. raspbian.raspberrypi.org 的 buster 已下架（404）→ 改用 legacy.raspbian.org
#   2. AIY 映像檔內建的 Google 套件來源 packages.cloud.google.com 已關閉（404）→ 停用
#   3. buster 的 Suite 從 stable 變成 oldoldstable → 加上 --allow-releaseinfo-change
#   4. Pi Zero 沒有 RTC 電池，開機時間錯誤會造成「Release file is not valid yet」→ 先校正時間
#
# 修改前會把整個 apt 設定備份到 /etc/apt/backup-<時間>/，可以隨時還原。

set -uo pipefail

APT_DIR=${APT_DIR:-/etc/apt}            # 可覆寫，方便測試
OS_RELEASE=${OS_RELEASE:-/etc/os-release}

okay() { printf '    \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '    \033[33m!\033[0m %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }

[ "$(id -u)" -ne 0 ] || { echo "請不要用 sudo 執行，直接用一般使用者執行：bash $0"; exit 1; }
sudo -v || exit 1

# ---------- 1. 校正時間 ----------
if [ "$(date +%Y)" -lt 2025 ]; then
  warn "系統時間不正確（$(date '+%F %T')），嘗試校正"
  sudo timedatectl set-ntp true 2>/dev/null
  for _ in $(seq 1 15); do
    [ "$(date +%Y)" -ge 2025 ] && break
    sleep 2
  done
  if [ "$(date +%Y)" -lt 2025 ]; then
    # NTP 被擋時，改用 HTTP 回應標頭裡的時間
    NOW=$(curl -sI -m 15 http://legacy.raspbian.org 2>/dev/null | tr -d '\r' | sed -n 's/^[Dd]ate: //p')
    [ -n "$NOW" ] && sudo date -s "$NOW" >/dev/null
  fi
  if [ "$(date +%Y)" -ge 2025 ]; then
    okay "時間已校正：$(date '+%F %T')"
  else
    warn "無法自動校正時間，請手動執行：sudo date -s '2026-10-06 12:00:00'"
  fi
fi

[ "${1:-}" = --time-only ] && exit 0

# shellcheck disable=SC1090
CODENAME=$(. "$OS_RELEASE" 2>/dev/null && echo "${VERSION_CODENAME:-}")
if [ "$CODENAME" != buster ]; then
  info "系統是 ${CODENAME:-未知版本}，不是 Buster，不需要修復來源"
  exec sudo apt-get update
fi

# ---------- 2. 備份 ----------
BACKUP=$APT_DIR/backup-$(date +%Y%m%d-%H%M%S)
sudo mkdir -p "$BACKUP"
sudo cp -a "$APT_DIR"/sources.list "$BACKUP/" 2>/dev/null
sudo cp -a "$APT_DIR"/sources.list.d "$BACKUP/" 2>/dev/null
okay "已備份 apt 設定到 $BACKUP"

# ---------- 3. Raspbian 主要來源改到封存站 ----------
CHANGED=0
for f in "$APT_DIR"/sources.list "$APT_DIR"/sources.list.d/*.list; do
  [ -f "$f" ] || continue
  if grep -qE '(raspbian\.raspberrypi\.org|mirrordirector\.raspbian\.org)/raspbian' "$f"; then
    sudo sed -i -E 's#(raspbian\.raspberrypi\.org|mirrordirector\.raspbian\.org)/raspbian#legacy.raspbian.org/raspbian#g' "$f"
    okay "已改用 legacy.raspbian.org：$f"
    CHANGED=1
  fi
done
[ "$CHANGED" = 1 ] || okay "Raspbian 來源已是可用的位址"

# ---------- 4. 停用已關閉的 Google AIY 來源 ----------
for f in "$APT_DIR"/sources.list.d/*.list; do
  [ -f "$f" ] || continue
  if grep -qE '^[^#]*packages\.cloud\.google\.com' "$f"; then
    sudo mv "$f" "$f.disabled"
    okay "已停用已關閉的來源：$f（改名為 .disabled，已安裝的 aiy 套件不受影響）"
  fi
done
if grep -qE '^[^#]*packages\.cloud\.google\.com' "$APT_DIR"/sources.list; then
  sudo sed -i -E 's#^([^#]*packages\.cloud\.google\.com)#\# \1#' "$APT_DIR"/sources.list
  okay "已註解 sources.list 裡已關閉的 Google 來源"
fi

# ---------- 5. 更新套件清單 ----------
info "執行 apt-get update…"
if sudo apt-get update --allow-releaseinfo-change; then
  okay "apt-get update 成功"
else
  warn "apt-get update 仍有錯誤，請把上面的錯誤訊息完整複製下來回報"
  info "還原設定：sudo cp -a $BACKUP/sources.list $APT_DIR/ && sudo cp -a $BACKUP/sources.list.d $APT_DIR/"
  exit 1
fi
