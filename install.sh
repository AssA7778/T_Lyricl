#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="https://github.com/AssA7778/T_Lyricl.git"
BRANCH=main
DIR=/opt/tglyrics
USER_NAME=tglyrics
SERVICE=tglyrics
RAW_INSTALL="https://raw.githubusercontent.com/AssA7778/T_Lyricl/main/install.sh"

c()   { printf '\033[1;36m%s\033[0m\n' "$*"; }
ok()  { printf '\033[32m+\033[0m %s\n' "$*"; }
warn(){ printf '\033[33m!\033[0m %s\n' "$*"; }
die() { printf '\033[31mx\033[0m %s\n' "$*" >&2; exit 1; }

CLONE_TMP=""
cleanup() { if [[ -n "$CLONE_TMP" ]]; then rm -rf "$CLONE_TMP"; fi; }
trap cleanup EXIT
trap 'printf "\033[31mx\033[0m installer failed — see the messages above.\n" >&2' ERR

has_tty() { bash -c ': </dev/tty' 2>/dev/null; }
require_root() { [[ $EUID -eq 0 ]] || die "run as root:  sudo -i  then run this again (or: sudo bash install.sh)"; }

cfg_get() {
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
    ok "source: local checkout ($SRC)"
  else
    CLONE_TMP="$(mktemp -d)"
    c "fetching code from GitHub..."
    git clone --quiet --depth 1 --branch "$BRANCH" "$REPO_URL" "$CLONE_TMP/src" \
      || die "clone failed — check network access to $REPO_URL"
    SRC="$CLONE_TMP/src"
    ok "source: $REPO_URL"
  fi
}

do_install() {
  require_root
  printf '\033[1;36m\n  tglyrics — live synced lyrics in your Telegram bio\n  --------------------------------------------------\033[0m\n\n'

  c "1/7  prerequisites"
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv python3-pip git curl ca-certificates >/dev/null
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y -q python3 git curl >/dev/null
  else
    warn "unknown package manager — assuming python3 and git are already installed"
  fi
  command -v systemctl >/dev/null 2>&1 || die "this server has no systemd — the installer requires it"
  ok "python ready ($(python3 -V))"

  resolve_src

  if systemctl list-unit-files 2>/dev/null | grep -q '^lyrics-bio\.service'; then
    systemctl disable --now lyrics-bio >/dev/null 2>&1 || true
    warn "old lyrics-bio service disabled — two writers must never fight over the bio"
  fi

  c "2/7  service user"
  id -u "$USER_NAME" &>/dev/null || useradd --system --home "$DIR" --shell /usr/sbin/nologin "$USER_NAME"
  ok "user $USER_NAME"

  c "3/7  copying files to $DIR"
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
  ok "files copied (config.toml, data/ and lyrics/ are left untouched)"

  c "4/7  virtualenv and dependencies"
  [[ -x "$DIR/.venv/bin/python" ]] || python3 -m venv "$DIR/.venv"
  "$DIR/.venv/bin/pip" install -q --upgrade pip
  "$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"
  ok "dependencies installed"

  c "5/7  config"
  if [[ ! -f "$DIR/config.toml" ]]; then
    cp "$DIR/config.example.toml" "$DIR/config.toml"
    local token
    token=$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 32)
    sed -i "s|CHANGE_ME_TO_SOMETHING_LONG|$token|" "$DIR/config.toml"
    ok "config.toml created (random webhook token generated)"
  else
    warn "config.toml already exists — kept as is"
  fi

  c "6/7  Telegram session"
  CONFIG_READY=0
  if config_ok; then
    CONFIG_READY=1
    ok "config is complete — no login needed"
  elif has_tty; then
    echo "You need an api_id and api_hash (my.telegram.org -> API development tools)."
    local ans=""
    read -r -p "Create the Telegram session now? [Y/n] " ans </dev/tty || true
    if [[ ! "$ans" =~ ^[nN] ]]; then
      if (cd "$DIR" && "$DIR/.venv/bin/python" "$DIR/login.py" --write "$DIR/config.toml" </dev/tty); then
        config_ok && CONFIG_READY=1
      fi
      [[ "$CONFIG_READY" = 1 ]] || warn "login incomplete — later run:  sudo bash $DIR/install.sh login"
    else
      warn "ok — later run:  sudo bash $DIR/install.sh login"
    fi
  else
    warn "no interactive terminal — later run:  sudo bash $DIR/install.sh login"
  fi

  chown -R "$USER_NAME:$USER_NAME" "$DIR"
  chmod 600 "$DIR/config.toml"

  c "7/7  systemd service"
  cp "$DIR/tglyrics.service" "/etc/systemd/system/$SERVICE.service"
  systemctl daemon-reload
  systemctl enable "$SERVICE" >/dev/null 2>&1
  if [[ "$CONFIG_READY" = 1 ]]; then
    systemctl restart "$SERVICE"
    sleep 3
    if systemctl is-active --quiet "$SERVICE"; then
      ok "service is running"
    else
      die "service failed to start — check:  journalctl -u $SERVICE -n 50"
    fi
  else
    warn "service not started (config incomplete). After login:  systemctl restart $SERVICE"
  fi

  local port token_now ip
  port="$(cfg_get port)"; port="${port:-8787}"
  token_now="$(cfg_get token)"
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow "$port"/tcp >/dev/null 2>&1 && ok "port $port opened in ufw" || warn "open port $port manually"
  fi
  ip=$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')

  cat <<EOF

------------------------------------------------------------
 Done. Now connect your player:
------------------------------------------------------------

 1) Install Tampermonkey (or Violentmonkey) in your browser
 2) Open this URL to install the userscript:
       https://raw.githubusercontent.com/AssA7778/T_Lyricl/main/userscript/tglyrics.user.js
 3) Extension menu -> "tglyrics server settings":
       server : http://$ip:$port
       token  : $token_now
 4) Hit "connection test", then play a song
    (YouTube Music / SoundCloud / Spotify Web / Apple Music / ...)

 Dry-run without touching Telegram:
    cd $DIR && .venv/bin/python simulate.py "Radiohead" "Creep"

 Control from inside Telegram (type in Saved Messages):
    .lrc          status          .lrc off      pause (restores bio)
    .lrc +300     nudge lyrics    .lrc help     all commands

 Manage:
    systemctl status $SERVICE          status
    journalctl -u $SERVICE -f          live log
    bash <(curl -fsSL $RAW_INSTALL) update       update
    bash <(curl -fsSL $RAW_INSTALL) uninstall    remove

EOF
}

do_login() {
  require_root
  [[ -f "$DIR/config.toml" ]] || die "install first:  bash <(curl -fsSL $RAW_INSTALL)"
  (cd "$DIR" && "$DIR/.venv/bin/python" "$DIR/login.py" --write "$DIR/config.toml" </dev/tty)
  chown "$USER_NAME:$USER_NAME" "$DIR/config.toml"
  chmod 600 "$DIR/config.toml"
  chown -R "$USER_NAME:$USER_NAME" "$DIR/data" 2>/dev/null || true
  systemctl restart "$SERVICE"
  ok "service restarted with the new session"
}

do_uninstall() {
  require_root
  c "removing tglyrics"
  systemctl disable --now "$SERVICE" 2>/dev/null || true
  rm -f "/etc/systemd/system/$SERVICE.service"
  systemctl daemon-reload
  ok "service removed"
  if has_tty; then
    local ans=""
    read -r -p "Also delete files and the Telegram session ($DIR)? [y/N] " ans </dev/tty || true
    if [[ "$ans" =~ ^[yY] ]]; then
      rm -rf "$DIR"
      id -u "$USER_NAME" &>/dev/null && userdel "$USER_NAME" 2>/dev/null || true
      ok "files and service user removed"
    else
      warn "files kept: $DIR"
    fi
  else
    warn "files kept: $DIR   (to delete: rm -rf $DIR)"
  fi
}

case "${1:-install}" in
  install|update)   do_install ;;
  uninstall|remove) do_uninstall ;;
  status)           systemctl status "$SERVICE" --no-pager ;;
  logs)             journalctl -u "$SERVICE" -f ;;
  restart)          require_root; systemctl restart "$SERVICE"; ok "restarted" ;;
  login)            do_login ;;
  *) echo "usage: install (default) | update | status | logs | restart | login | uninstall"; exit 1 ;;
esac
