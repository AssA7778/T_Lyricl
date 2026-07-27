#!/data/data/com.termux/files/usr/bin/bash
set -uo pipefail

SERVER="${SERVER:-}"
TOKEN="${TOKEN:-}"
INTERVAL="${INTERVAL:-1}"
HEARTBEAT="${HEARTBEAT:-10}"

[[ -n "$SERVER" ]] || { echo "SERVER تنظیم نشده. مثال: SERVER=http://1.2.3.4:8787 TOKEN=xxx bash $0"; exit 1; }
command -v adb >/dev/null || { echo "adb نیست:  pkg install android-tools"; exit 1; }

if ! adb get-state >/dev/null 2>&1; then
  echo "adb وصل نیست. اول:  adb pair <ip>:<port>  و بعد  adb connect <ip>:<port>"
  exit 1
fi

echo "→ $SERVER   (هر $INTERVAL ثانیه چک، هر $HEARTBEAT ثانیه ضربان)"

last_sig=""
last_send=0

sync_clock() {
  local t0 t3 s
  t0=$(date +%s%3N)
  s=$(curl -fsS --max-time 5 "$SERVER/time" 2>/dev/null | sed -n 's/.*"server_ms":\s*\([0-9.]*\).*/\1/p')
  t3=$(date +%s%3N)
  if [[ -n "$s" ]]; then
    OFFSET=$(awk -v s="$s" -v a="$t0" -v b="$t3" 'BEGIN{printf "%.0f", s-(a+b)/2}')
    echo "  ساعت هماهنگ شد (اختلاف ${OFFSET}ms، RTT $((t3-t0))ms)"
  else
    OFFSET=0
    echo "  هماهنگی ساعت نشد — بدون جبران تأخیر ادامه می‌دهم"
  fi
}
sync_clock

while true; do
  DUMP=$(adb shell dumpsys media_session 2>/dev/null)

  BLOCK=$(printf '%s' "$DUMP" | awk '
    /package=/ {blk=$0; next}
    /state=PlaybackState/ {blk=blk"\n"$0}
    /description=/ {blk=blk"\n"$0; if (blk ~ /state=3/) {print blk; exit}}
  ')

  if [[ -z "$BLOCK" ]]; then
    if [[ "$last_sig" != "STOP" ]]; then
      curl -fsS -m 5 -X POST "$SERVER/ingest" -H 'Content-Type: application/json' \
        -d "{\"token\":\"$TOKEN\",\"event\":\"stop\"}" >/dev/null 2>&1
      last_sig="STOP"; echo "  ⏹ چیزی پخش نمی‌شود"
    fi
    sleep "$INTERVAL"; continue
  fi

  POS=$(printf '%s' "$BLOCK"   | sed -n 's/.*position=\([0-9-]*\).*/\1/p'   | head -1)
  SPEED=$(printf '%s' "$BLOCK" | sed -n 's/.*speed=\([0-9.]*\).*/\1/p'      | head -1)
  UPD=$(printf '%s' "$BLOCK"   | sed -n 's/.*updated=\([0-9]*\).*/\1/p'     | head -1)
  DESC=$(printf '%s' "$BLOCK"  | sed -n 's/.*description=\(.*\)/\1/p'       | head -1)

  [[ -n "$POS" ]] || { sleep "$INTERVAL"; continue; }
  SPEED="${SPEED:-1.0}"

  TITLE=$(printf '%s' "$DESC" | cut -d, -f1 | sed 's/^ *//;s/ *$//')
  ARTIST=$(printf '%s' "$DESC" | cut -d, -f2 | sed 's/^ *//;s/ *$//')
  [[ -n "$TITLE" && "$TITLE" != "null" ]] || { sleep "$INTERVAL"; continue; }

  NOW_ELAPSED=$(adb shell "awk '{print int(\$1*1000)}' /proc/uptime" 2>/dev/null | tr -d '\r')
  if [[ -n "$UPD" && -n "$NOW_ELAPSED" ]]; then
    POS=$(awk -v p="$POS" -v u="$UPD" -v n="$NOW_ELAPSED" -v s="$SPEED" \
          'BEGIN{d=n-u; if(d<0||d>30000)d=0; printf "%.0f", p+d*s}')
  fi

  CAP=$(( $(date +%s%3N) + ${OFFSET:-0} ))
  sig="$TITLE|$ARTIST"
  now=$(date +%s)

  if [[ "$sig" != "$last_sig" || $((now - last_send)) -ge $HEARTBEAT ]]; then
    curl -fsS -m 5 -X POST "$SERVER/ingest" -H 'Content-Type: application/json' -d @- <<EOF >/dev/null 2>&1
{"token":"$TOKEN","event":"state","title":$(printf '%s' "$TITLE" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))'),
 "artist":$(printf '%s' "$ARTIST" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))'),
 "position_ms":$POS,"playing":true,"rate":$SPEED,"captured_at_server_ms":$CAP,"agent":"termux"}
EOF
    [[ "$sig" != "$last_sig" ]] && echo "  ▶ $ARTIST – $TITLE"
    last_sig="$sig"; last_send=$now
  fi

  sleep "$INTERVAL"
done
