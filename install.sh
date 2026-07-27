#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
#  tglyrics — نصب خودکار روی VPS (اوبونتو/دبیان + systemd)
#
#  یک‌خطی (لازم نیست چیزی کلون کنی):
#    bash <(curl -fsSL https://raw.githubusercontent.com/AssA7778/T_Lyricl/main/install.sh)
#
#  یا از داخل پوشه‌ی پروژه:   sudo bash install.sh
#
#  دستورها (به آخر همان دستور اضافه کن):
#    update     آپدیت (همان install)       status     وضعیت سرویس
#    logs       لاگ زنده                    restart    ری‌استارت
#    login      لاگین دوباره‌ی تلگرام        uninstall  حذف کامل
# ═══════════════════════════════════════════════════════════════════════
set -Eeuo pipefail

REPO_URL="https://github.com/AssA7778/T_Lyricl.git"
BRANCH=main
DIR=/opt/tglyrics
USER_NAME=tglyrics
SERVICE=tglyrics
RAW_INSTALL="https://raw.githubusercontent.com/AssA7778/T_Lyricl/main/install.sh"

c()   { printf '\033[1;36m%s\033[0m\n' "$*"; }
ok()  { printf '\033[32m✓\033[0m %s\n' "$*"; }
warn(){ printf '\033[33m!\033[0m %s\n' "$*"; }
die() { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

CLONE_TMP=""
cleanup() { if [[ -n "$CLONE_TMP" ]]; then rm -rf "$CLONE_TMP"; fi; }
trap cleanup EXIT
trap 'printf "\033[31m✗\033[0m اسکریپت وسط کار خطا خورد — پیام‌های بالا را ببین.\n" >&2' ERR

has_tty() { bash -c ': </dev/tty' 2>/dev/null; }
require_root() { [[ $EUID -eq 0 ]] || die "با root اجرا کن:  sudo -i  و بعد دوباره (یا sudo bash install.sh)"; }

cfg_get() {  # cfg_get <key> ← اولین «key = "…"» یا «key = 123» از config.toml
  sed -n "s/^${1} *= *\"\{0,1\}\([^\"]*\)\"\{0,1\}.*/\1/p" "$DIR/config.toml" 2>/dev/null | head -1
}

config_ok() {
  (cd "$DIR" && "$DIR/.venv/bin/python" -m tglyrics -c "$DIR/config.toml" --check >/dev/null 2>&1)
}

SRC=""
resolve_src() {
  local here=""
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || true)"
  if [[ -n "$here" && -f "$here/requirements.txt" && -d "$here/tglyrics" ]]; then
    SRC="$here"
    ok "منبع کد: همین پوشه ($SRC)"
  else
    CLONE_TMP="$(mktemp -d)"
    c "دریافت کد از گیت‌هاب…"
    git clone --quiet --depth 1 --branch "$BRANCH" "$REPO_URL" "$CLONE_TMP/src" \
      || die "کلون نشد — دسترسی به گیت‌هاب را چک کن: $REPO_URL"
    SRC="$CLONE_TMP/src"
    ok "منبع کد: $REPO_URL"
  fi
}

do_install() {
  require_root
  printf '\033[1;36m\n  🎙 tglyrics — لیریکِ زنده‌ی سینک‌شده توی بیوی تلگرام\n  ────────────────────────────────────────────────────\033[0m\n\n'

  c "۱/۷  پیش‌نیازها"
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv python3-pip git curl ca-certificates >/dev/null
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y -q python3 git curl >/dev/null
  else
    warn "پکیج‌منیجر ناشناخته — فرض می‌کنم python3 و git از قبل نصبند"
  fi
  command -v systemctl >/dev/null 2>&1 || die "این سرور systemd ندارد — اینستالر فقط با systemd کار می‌کند"
  ok "پایتون آماده است ($(python3 -V))"

  resolve_src

  # پروژه‌ی قدیمیِ پلی‌لیست ثابت (lyrics-bio) نباید هم‌زمان بیو را بنویسد
  if systemctl list-unit-files 2>/dev/null | grep -q '^lyrics-bio\.service'; then
    systemctl disable --now lyrics-bio >/dev/null 2>&1 || true
    warn "سرویس قدیمی lyrics-bio خاموش شد — دو برنامه نباید هم‌زمان بیو بنویسند"
  fi

  c "۲/۷  کاربر سرویس"
  id -u "$USER_NAME" &>/dev/null || useradd --system --home "$DIR" --shell /usr/sbin/nologin "$USER_NAME"
  ok "کاربر $USER_NAME"

  c "۳/۷  کپی فایل‌ها به $DIR"
  systemctl stop "$SERVICE" 2>/dev/null || true
  mkdir -p "$DIR"
  local d f
  for d in tglyrics userscript agents tests; do
    [[ -d "$SRC/$d" ]] || continue
    rm -rf "${DIR:?}/${d:?}"
    cp -r "$SRC/$d" "$DIR/"
  done
  for f in login.py simulate.py requirements.txt config.example.toml README.md tglyrics.service install.sh; do
    [[ -f "$SRC/$f" ]] && cp "$SRC/$f" "$DIR/"
  done
  mkdir -p "$DIR/data" "$DIR/lyrics"
  [[ -f "$SRC/lyrics/HOWTO.txt" ]] && cp "$SRC/lyrics/HOWTO.txt" "$DIR/lyrics/"
  ok "فایل‌ها کپی شدند (config.toml و data/ و lyrics/ دست نمی‌خورند)"

  c "۴/۷  محیط مجازی و کتابخانه‌ها"
  [[ -x "$DIR/.venv/bin/python" ]] || python3 -m venv "$DIR/.venv"
  "$DIR/.venv/bin/pip" install -q --upgrade pip
  "$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"
  ok "کتابخانه‌ها نصب شدند"

  c "۵/۷  کانفیگ"
  if [[ ! -f "$DIR/config.toml" ]]; then
    cp "$DIR/config.example.toml" "$DIR/config.toml"
    local token
    token=$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 32)
    sed -i "s|CHANGE_ME_TO_SOMETHING_LONG|$token|" "$DIR/config.toml"
    ok "config.toml ساخته شد (توکن وب‌هوک تصادفی هم تولید شد)"
  else
    warn "config.toml از قبل بود — دست نخورد"
  fi

  c "۶/۷  سشن تلگرام"
  CONFIG_READY=0
  if config_ok; then
    CONFIG_READY=1
    ok "کانفیگ کامل است — لاگین لازم نیست"
  elif has_tty; then
    echo "برای لاگین، api_id و api_hash لازم داری (my.telegram.org → API development tools)."
    local ans=""
    read -r -p "همین حالا سشن تلگرام را بسازیم؟ [Y/n] " ans </dev/tty || true
    if [[ ! "$ans" =~ ^[nN] ]]; then
      if (cd "$DIR" && "$DIR/.venv/bin/python" "$DIR/login.py" --write "$DIR/config.toml" </dev/tty); then
        config_ok && CONFIG_READY=1
      fi
      [[ "$CONFIG_READY" = 1 ]] || warn "لاگین کامل نشد — بعداً:  sudo bash $DIR/install.sh login"
    else
      warn "باشه — بعداً:  sudo bash $DIR/install.sh login"
    fi
  else
    warn "ترمینال تعاملی نیست — بعداً روی سرور:  sudo bash $DIR/install.sh login"
  fi

  chown -R "$USER_NAME:$USER_NAME" "$DIR"
  chmod 600 "$DIR/config.toml"

  c "۷/۷  سرویس systemd"
  cp "$DIR/tglyrics.service" "/etc/systemd/system/$SERVICE.service"
  systemctl daemon-reload
  systemctl enable "$SERVICE" >/dev/null 2>&1
  if [[ "$CONFIG_READY" = 1 ]]; then
    systemctl restart "$SERVICE"
    sleep 3
    if systemctl is-active --quiet "$SERVICE"; then
      ok "سرویس روشن است 🎙"
    else
      die "سرویس بالا نیامد — ببین:  journalctl -u $SERVICE -n 50"
    fi
  else
    warn "سرویس روشن نشد چون کانفیگ کامل نیست. بعد از لاگین:  systemctl restart $SERVICE"
  fi

  local port token_now ip
  port="$(cfg_get port)"; port="${port:-8787}"
  token_now="$(cfg_get token)"
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow "$port"/tcp >/dev/null 2>&1 && ok "پورت $port روی فایروال باز شد" || warn "پورت $port را دستی باز کن"
  fi
  ip=$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')

  cat <<EOF

────────────────────────────────────────────────────────────
 نصب تمام شد ✔ — حالا پلیرت را وصل کن:
────────────────────────────────────────────────────────────

 ۱) روی مرورگرت Tampermonkey (یا Violentmonkey) نصب کن
 ۲) یوزراسکریپت را باز کن تا نصب شود:
       $DIR/userscript/tglyrics.user.js
    یا مستقیم از گیت‌هاب:
       https://raw.githubusercontent.com/AssA7778/T_Lyricl/main/userscript/tglyrics.user.js
 ۳) از منوی افزونه → «⚙️ تنظیم سرور tglyrics»:
       سرور : http://$ip:$port
       توکن : $token_now
 ۴) «🔌 تست اتصال» را بزن و یک آهنگ پخش کن 🎶
    (YouTube Music / SoundCloud / Spotify Web / Apple Music / …)

 تست بدون دست‌زدن به تلگرام:
    cd $DIR && .venv/bin/python simulate.py "Radiohead" "Creep"

 کنترل از داخل تلگرام — توی Saved Messages بنویس:
    .lrc            وضعیت
    .lrc on/off     روشن/خاموش (خاموش = بیوی اصلی برمی‌گردد)
    .lrc sync +300  لیریک را جلو ببر
    .lrc help       همه‌ی دستورها

 مدیریت:
    systemctl status $SERVICE          وضعیت
    journalctl -u $SERVICE -f          لاگ زنده
    bash <(curl -fsSL $RAW_INSTALL) update       آپدیت
    bash <(curl -fsSL $RAW_INSTALL) uninstall    حذف کامل

EOF
}

do_login() {
  require_root
  [[ -f "$DIR/config.toml" ]] || die "اول نصب کن:  bash <(curl -fsSL $RAW_INSTALL)"
  (cd "$DIR" && "$DIR/.venv/bin/python" "$DIR/login.py" --write "$DIR/config.toml" </dev/tty)
  chown "$USER_NAME:$USER_NAME" "$DIR/config.toml"
  chmod 600 "$DIR/config.toml"
  chown -R "$USER_NAME:$USER_NAME" "$DIR/data" 2>/dev/null || true
  systemctl restart "$SERVICE"
  ok "سرویس با سشن جدید ری‌استارت شد"
}

do_uninstall() {
  require_root
  c "حذف tglyrics"
  systemctl disable --now "$SERVICE" 2>/dev/null || true
  rm -f "/etc/systemd/system/$SERVICE.service"
  systemctl daemon-reload
  ok "سرویس حذف شد"
  if has_tty; then
    local ans=""
    read -r -p "فایل‌ها و سشن ($DIR) هم پاک شود؟ [y/N] " ans </dev/tty || true
    if [[ "$ans" =~ ^[yY] ]]; then
      rm -rf "$DIR"
      id -u "$USER_NAME" &>/dev/null && userdel "$USER_NAME" 2>/dev/null || true
      ok "فایل‌ها و کاربر سرویس پاک شدند"
    else
      warn "فایل‌ها ماندند: $DIR"
    fi
  else
    warn "فایل‌ها ماندند: $DIR   (اگر خواستی: rm -rf $DIR)"
  fi
}

case "${1:-install}" in
  install|update)   do_install ;;
  uninstall|remove) do_uninstall ;;
  status)           systemctl status "$SERVICE" --no-pager ;;
  logs)             journalctl -u "$SERVICE" -f ;;
  restart)          require_root; systemctl restart "$SERVICE"; ok "ری‌استارت شد" ;;
  login)            do_login ;;
  *) echo "دستورها: install (پیش‌فرض) | update | status | logs | restart | login | uninstall"; exit 1 ;;
esac
