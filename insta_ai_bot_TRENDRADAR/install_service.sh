#!/usr/bin/env bash
# ============================================================
# ⚙️ نصب خودکار سرویس systemd برای ربات
#    با یک دستور، ربات رو از ترمینال بیرون می‌آره و همیشه‌روشن می‌کنه.
#
# اجرا (باید root باشی):
#     chmod +x install_service.sh
#     ./install_service.sh
#
# حالت آزمایشی (هیچ تغییری نمی‌ده، فقط نشون می‌ده چیکار می‌کنه):
#     ./install_service.sh --dry-run
# ============================================================
set -u

SERVICE_NAME="insta-ai-bot"
BOT_FILE="bot.py"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

DIR="$(cd "$(dirname "$0")" && pwd)"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

say()  { echo "  $*"; }
head2(){ echo; echo "▌ $*"; }

echo "══════════════════════════════════════════════════════════════"
echo "   نصب سرویس $SERVICE_NAME   (مسیر پروژه: $DIR)"
[ "$DRY" = 1 ] && echo "   ⚠️  حالت آزمایشی — هیچ تغییری اعمال نمی‌شه"
echo "══════════════════════════════════════════════════════════════"

# ---------- ۱) بررسی root ----------
if [ "$(id -u)" != "0" ] && [ "$DRY" = 0 ]; then
    echo "❌ باید با root اجرا کنی:  sudo ./install_service.sh"
    exit 1
fi

# ---------- ۲) پیدا کردن پایتون ----------
PY=""
for c in "$DIR/venv/bin/python" "$DIR/.venv/bin/python" "$DIR/venv/bin/python3"; do
    [ -x "$c" ] && { PY="$c"; break; }
done
[ -z "$PY" ] && PY="$(command -v python3 || command -v python || true)"

head2 "۱) بررسی‌ها"
say "پروژه      : $DIR"
say "فایل ربات  : $DIR/$BOT_FILE"
say "پایتون     : ${PY:-❌ پیدا نشد!}"
say "systemd    : $(command -v systemctl >/dev/null 2>&1 && echo 'موجود ✓' || echo '❌ نصب نیست')"

[ -f "$DIR/$BOT_FILE" ]      || { echo "❌ فایل $BOT_FILE در این پوشه نیست!"; exit 1; }
[ -n "$PY" ] && [ -x "$PY" ] || { echo "❌ پایتون پیدا نشد. venv بساز یا python3 نصب کن."; exit 1; }
[ -f "$DIR/.env" ]           || echo "⚠️  فایل .env نیست — توکن‌ها رو ست کن!"
command -v systemctl >/dev/null 2>&1 || { echo "❌ این سرور systemd نداره → از run_forever.sh استفاده کن"; exit 1; }

# ---------- ۳) پروسه‌های تکراری ----------
head2 "۲) پروسه‌های فعلی ربات (باید صفر باشه تا بعد از نصب دو نسخه نشه)"
mapfile -t PIDS < <(pgrep -f "python[0-9.]* .*${BOT_FILE}" 2>/dev/null || true)
if [ "${#PIDS[@]}" -eq 0 ]; then
    say "✅ هیچ نسخه‌ای در حال اجرا نیست"
else
    say "⚠️  ${#PIDS[@]} نسخه در حال اجراست:"
    for p in "${PIDS[@]}"; do
        say "   PID $p | cwd=$(readlink -f /proc/$p/cwd 2>/dev/null || echo '؟') | $(ps -o lstart= -p "$p" 2>/dev/null)"
    done
    if [ "$DRY" = 0 ]; then
        read -r -p "   همه‌شون رو ببندم؟ (y/N) " ans
        if [ "${ans,,}" = "y" ]; then
            for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null; done
            sleep 2
            for p in "${PIDS[@]}"; do kill -9 "$p" 2>/dev/null; done
            say "✅ بسته شدند"
        else
            say "⛔ رد شد — یادت باشه دو نسخه همزمان = خطای ۴۰۹ تلگرام"
        fi
    fi
fi

# ---------- ۴) نوشتن فایل سرویس ----------
head2 "۳) نوشتن فایل سرویس در $UNIT_PATH"
UNIT_CONTENT="[Unit]
Description=Insta AI Bot (Telegram + Apify + AvalAI)
StartLimitIntervalSec=0
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$DIR
ExecStart=$PY $DIR/$BOT_FILE

Restart=always
RestartSec=10

Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8
Environment=LANG=C.UTF-8

TimeoutStopSec=20
KillMode=mixed

[Install]
WantedBy=multi-user.target
"

if [ "$DRY" = 1 ]; then
    echo "──── محتوایی که نوشته می‌شود ────"
    echo "$UNIT_CONTENT"
    echo "────────────────────────────────"
else
    printf '%s' "$UNIT_CONTENT" > "$UNIT_PATH"
    say "✅ نوشته شد"
fi

# ---------- ۵) فعال‌سازی ----------
head2 "۴) فعال‌سازی سرویس"
if [ "$DRY" = 1 ]; then
    say "systemctl daemon-reload"
    say "systemctl enable --now $SERVICE_NAME"
else
    systemctl daemon-reload
    systemctl enable --now "$SERVICE_NAME"
    sleep 4
    head2 "۵) وضعیت"
    systemctl status "$SERVICE_NAME" --no-pager -l | head -20 || true
fi

cat <<EOF

══════════════════════════════════════════════════════════════
 ✅ تمام! از این به بعد:

   دیدن وضعیت      : systemctl status $SERVICE_NAME
   دیدن لاگ زنده   : journalctl -u $SERVICE_NAME -f
   ری‌استارت       : systemctl restart $SERVICE_NAME
   توقف            : systemctl stop $SERVICE_NAME
   غیرفعال‌کردن    : systemctl disable --now $SERVICE_NAME

 🔁 اگه ربات به هر دلیلی بمیره (Terminated/Killed/کرش/قطع SSH)،
    systemd ده ثانیه بعد خودش دوباره بالا می‌آرودش.
    با ری‌استارت سرور هم خودش شروع می‌شه.
══════════════════════════════════════════════════════════════
EOF
