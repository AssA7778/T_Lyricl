#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
#   🎙 Telegram Live Lyrics Bio — اینستالر خودکار سرور (Ubuntu/Debian)
#
#   نصب یا آپدیت (روی سرور، با root):
#     bash <(curl -fsSL https://raw.githubusercontent.com/AssA7778/T_Lyricl/main/install.sh)
#
#   دستورهای دیگه (به آخر همون دستور اضافه کن):
#     status      وضعیت سرویس          logs      لاگ زنده
#     restart     ری‌استارت             login     لاگین دوباره تلگرام
#     uninstall   حذف کامل
# ═══════════════════════════════════════════════════════════════════════
set -Eeuo pipefail

REPO_URL="https://github.com/AssA7778/T_Lyricl.git"
BRANCH="main"
APP_DIR="/opt/lyrics-bio"
SERVICE="lyrics-bio"
VENV_PY="$APP_DIR/venv/bin/python"
RAW_INSTALL="https://raw.githubusercontent.com/AssA7778/T_Lyricl/main/install.sh"

C_G=$'\033[1;32m'; C_Y=$'\033[1;33m'; C_R=$'\033[1;31m'; C_C=$'\033[1;36m'; C_0=$'\033[0m'
ok()   { echo "${C_G}[+]${C_0} $*"; }
warn() { echo "${C_Y}[!]${C_0} $*"; }
err()  { echo "${C_R}[x]${C_0} $*" >&2; }
step() { echo; echo "${C_C}━━━ $* ━━━${C_0}"; }

trap 'err "اسکریپت وسط کار خطا خورد — پیام‌های بالا رو ببین."' ERR

has_tty() { bash -c ': </dev/tty' 2>/dev/null; }

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    err "این اسکریپت دسترسی root می‌خواد. اول «sudo -i» بزن، بعد دوباره اجراش کن."
    exit 1
  fi
}

install_deps() {
  step "۱) نصب پیش‌نیازها (python3 / git)"
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq >/dev/null
    apt-get install -y -qq python3 python3-venv git curl ca-certificates >/dev/null
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y -q python3 git curl >/dev/null
  else
    warn "پکیج‌منیجر ناشناخته — فرض می‌کنم python3 و git از قبل نصبن."
  fi
  command -v systemctl >/dev/null 2>&1 || { err "این سرور systemd نداره — اینستالر فقط روی systemd کار می‌کنه."; exit 1; }
  ok "پیش‌نیازها آماده‌ست"
}

fetch_code() {
  step "۲) دریافت کد از گیت‌هاب"
  if [ -d "$APP_DIR/.git" ]; then
    ok "نصب قبلی پیدا شد — آپدیت می‌کنم (پلی‌لیست و لاگینت دست نمی‌خوره)"
    local tmp_cfg=""
    if [ -f "$APP_DIR/config.json" ]; then
      tmp_cfg="$(mktemp)"
      cp "$APP_DIR/config.json" "$tmp_cfg"
    fi
    git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
    git -C "$APP_DIR" reset --hard --quiet "origin/$BRANCH"
    if [ -n "$tmp_cfg" ]; then
      cp "$tmp_cfg" "$APP_DIR/config.json"
      rm -f "$tmp_cfg"
    fi
  elif [ -d "$APP_DIR" ]; then
    local backup="${APP_DIR}.old-$(date +%Y%m%d-%H%M%S)"
    warn "پوشه‌ی قدیمی (بدون git) پیدا شد → منتقل شد به: $backup"
    mv "$APP_DIR" "$backup"
    git clone --quiet --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
    if [ -f "$backup/config.json" ]; then
      cp "$backup/config.json" "$APP_DIR/config.json"
      ok "config.json قبلی (پلی‌لیستت) برگشت"
    fi
    if compgen -G "$backup/*.session" >/dev/null; then
      cp "$backup"/*.session "$APP_DIR/"
      ok "سشن تلگرام قبلی برگشت — لاگین دوباره لازم نیست"
    fi
    if [ -d "$backup/lyrics" ]; then
      local f base
      for f in "$backup"/lyrics/*.lrc; do
        [ -e "$f" ] || continue
        base="$(basename "$f")"
        if [ ! -e "$APP_DIR/lyrics/$base" ]; then
          cp "$f" "$APP_DIR/lyrics/$base"
          ok "فایل متن شخصی برگشت: lyrics/$base"
        fi
      done
    fi
  else
    git clone --quiet --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
    ok "کد دانلود شد → $APP_DIR"
  fi
}

setup_venv() {
  step "۳) محیط پایتون + Telethon"
  if [ ! -x "$VENV_PY" ]; then
    python3 -m venv "$APP_DIR/venv"
  fi
  "$APP_DIR/venv/bin/pip" install --quiet --disable-pip-version-check -r "$APP_DIR/requirements.txt"
  ok "Telethon نصب شد"
}

LOGIN_OK=0
do_login() {
  step "۴) لاگین تلگرام"
  if "$VENV_PY" "$APP_DIR/deploy/login.py" --check; then
    LOGIN_OK=1
    return 0
  fi
  if has_tty; then
    echo "شماره‌ت رو با کد کشور وارد کن (مثل +98912xxxxxxx)، بعد کدی که تلگرام برات می‌فرسته (و پسورد 2FA اگه داری)."
    if "$VENV_PY" "$APP_DIR/deploy/login.py" </dev/tty; then
      LOGIN_OK=1
    fi
  else
    warn "ترمینال تعاملی در دسترس نیست — سرویس نصب می‌شه ولی روشن نمی‌شه."
    warn "برای لاگین، روی سرور این دوتا رو اجرا کن:"
    echo "      $VENV_PY $APP_DIR/deploy/login.py"
    echo "      systemctl restart $SERVICE"
  fi
}

setup_service() {
  step "۵) سرویس systemd (اجرای ۲۴/۷ + استارت خودکار بعد از ری‌بوت)"
  cp "$APP_DIR/deploy/lyrics-bio.service" "/etc/systemd/system/$SERVICE.service"
  systemctl daemon-reload
  systemctl enable "$SERVICE" >/dev/null 2>&1
  if [ "$LOGIN_OK" = "1" ]; then
    systemctl restart "$SERVICE"
    sleep 3
    if systemctl is-active --quiet "$SERVICE"; then
      ok "سرویس روشنه و داره می‌خونه 🎙"
    else
      err "سرویس بالا نیومد! لاگ رو ببین: journalctl -u $SERVICE -n 50"
      exit 1
    fi
  else
    warn "چون لاگین انجام نشده، سرویس استارت نشد. بعد از لاگین: systemctl restart $SERVICE"
  fi
}

summary() {
  step "تمام ✔"
  echo "  📁 مسیر نصب:           $APP_DIR"
  echo "  🎵 عوض کردن پلی‌لیست:   nano $APP_DIR/config.json  (و فایل‌های $APP_DIR/lyrics/*.lrc)"
  echo "                          بعدش:  systemctl restart $SERVICE"
  echo "  📊 وضعیت:              systemctl status $SERVICE"
  echo "  📜 لاگ زنده:            journalctl -u $SERVICE -f"
  echo "  🔄 آپدیت:              دوباره همین دستور نصب رو بزن"
  echo "  🗑  حذف کامل:           bash <(curl -fsSL $RAW_INSTALL) uninstall"
  echo
  warn "توی تلگرام → Settings → Devices، دستگاه این بات رو Terminate نکن — لاگینش می‌پره!"
}

do_install() {
  require_root
  echo "${C_C}"
  echo "   🎙 Telegram Live Lyrics Bio — نصب خودکار"
  echo "   ─────────────────────────────────────────"
  echo "${C_0}"
  install_deps
  systemctl stop "$SERVICE" 2>/dev/null || true
  fetch_code
  setup_venv
  do_login
  setup_service
  summary
}

do_uninstall() {
  require_root
  step "حذف $SERVICE"
  systemctl disable --now "$SERVICE" 2>/dev/null || true
  rm -f "/etc/systemd/system/$SERVICE.service"
  systemctl daemon-reload
  ok "سرویس حذف شد"
  if has_tty; then
    local ans=""
    read -r -p "فایل‌ها و سشن تلگرام ($APP_DIR) هم پاک بشن؟ [y/N] " ans </dev/tty || true
    case "$ans" in
      y|Y|yes|YES) rm -rf "$APP_DIR"; ok "فایل‌ها پاک شدن" ;;
      *) warn "فایل‌ها موندن: $APP_DIR" ;;
    esac
  else
    warn "فایل‌ها موندن: $APP_DIR   (اگه خواستی: rm -rf $APP_DIR)"
  fi
}

case "${1:-install}" in
  install|update) do_install ;;
  uninstall|remove) do_uninstall ;;
  status)  systemctl status "$SERVICE" --no-pager ;;
  logs)    journalctl -u "$SERVICE" -f ;;
  restart) require_root; systemctl restart "$SERVICE"; ok "ری‌استارت شد" ;;
  login)   require_root; "$VENV_PY" "$APP_DIR/deploy/login.py" </dev/tty; systemctl restart "$SERVICE"; ok "سرویس با لاگین جدید ری‌استارت شد" ;;
  *)
    echo "دستورها: install (پیش‌فرض) | update | status | logs | restart | login | uninstall"
    exit 1
    ;;
esac
