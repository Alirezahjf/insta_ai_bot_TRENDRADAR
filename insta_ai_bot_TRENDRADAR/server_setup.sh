#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
#  🚀 راه‌اندازی ربات به‌صورت سرویس (راه‌حل قطعی «Terminated»)
#
#  کل این فایل رو کپی کن و روی سرور پیست کن، یا:
#      bash server_setup.sh
#
#  این اسکریپت: نسخه‌های تکراری رو می‌بنده + سرویس systemd می‌سازه
#              + فعالش می‌کنه. از اون به بعد ربات همیشه روشن می‌مونه.
# ══════════════════════════════════════════════════════════════════════
set -u

PKG="/root/Desktop/insta_ai_bot"          # ← اگه مسیر پروژه فرق داره این رو عوض کن
SVC="insta-ai-bot"
BOT="bot.py"

echo "════════════════════════════════════════════════════════════"
echo " ۱) بررسی مسیر و پایتون"
echo "════════════════════════════════════════════════════════════"
[ -d "$PKG" ]     || { echo "❌ پوشه $PKG پیدا نشد!"; exit 1; }
[ -f "$PKG/$BOT" ] || { echo "❌ فایل $PKG/$BOT پیدا نشد!"; exit 1; }

if   [ -x "$PKG/venv/bin/python" ];  then PY="$PKG/venv/bin/python"
elif [ -x "$PKG/.venv/bin/python" ]; then PY="$PKG/.venv/bin/python"
elif [ -x "$PKG/venv/bin/python3" ]; then PY="$PKG/venv/bin/python3"
else PY="$(command -v python3 || command -v python)"; fi

echo "  پوشه پروژه : $PKG"
echo "  پایتون     : $PY"
"$PY" --version 2>/dev/null || { echo "❌ پایتون کار نمی‌کنه!"; exit 1; }
[ -f "$PKG/.env" ] || echo "  ⚠️  فایل .env پیدا نشد — توکن‌ها رو ست کن!"

echo
echo "════════════════════════════════════════════════════════════"
echo " ۲) نسخه‌های در حال اجرا (پیش‌نمایش — هنوز چیزی بسته نشده)"
echo "════════════════════════════════════════════════════════════"
FOUND=0
for p in $(pgrep -f "[b]ot\.py$" 2>/dev/null); do
    CWD="$(readlink -f /proc/$p/cwd 2>/dev/null || echo '؟')"
    CMD="$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null)"
    MARK=""
    [ "$CWD" = "$PKG" ] && { MARK=" ←  مال همین پروژه"; FOUND=1; }
    echo "  PID $p | cwd=$CWD$MARK"
    echo "          $CMD"
done
[ "$FOUND" = 0 ] && echo "  (مال این پروژه چیزی در حال اجرا نیست)"

echo
echo "════════════════════════════════════════════════════════════"
echo " ۳) بستن فقط پروسه‌های همین پروژه (به بقیه ربات‌ها کاری ندارم)"
echo "════════════════════════════════════════════════════════════"
KILLED=0
for p in $(pgrep -f "[b]ot\.py$" 2>/dev/null); do
    CWD="$(readlink -f /proc/$p/cwd 2>/dev/null || echo '?')"
    if [ "$CWD" = "$PKG" ]; then
        kill "$p" 2>/dev/null && echo "  ✅ بسته شد: PID $p"
        KILLED=1
    else
        echo "  ⏭  دست نزدم (مال پروژه دیگری است): PID $p  →  $CWD"
    fi
done
[ "$KILLED" = 1 ] && sleep 2
for p in $(pgrep -f "[b]ot\.py$" 2>/dev/null); do
    CWD="$(readlink -f /proc/$p/cwd 2>/dev/null || echo '?')"
    [ "$CWD" = "$PKG" ] && { kill -9 "$p" 2>/dev/null; echo "  💀 kill -9 روی PID $p"; }
done
[ "$KILLED" = 0 ] && echo "  چیزی برای بستن نبود"

echo
echo "════════════════════════════════════════════════════════════"
echo " ۴) ساخت فایل سرویس"
echo "════════════════════════════════════════════════════════════"
cat > "/etc/systemd/system/$SVC.service" << EOF
[Unit]
Description=Insta AI Bot (Telegram + Apify + AvalAI)
StartLimitIntervalSec=0
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$PKG
ExecStart=$PY $PKG/$BOT

# 🔁 هر دلیلی که پروسه مرد، ۱۰ ثانیه بعد خودکار بالا میاد
Restart=always
RestartSec=10

Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8
Environment=LANG=C.UTF-8

TimeoutStopSec=20
KillMode=mixed

[Install]
WantedBy=multi-user.target
EOF
echo "  ✅ نوشته شد: /etc/systemd/system/$SVC.service"

echo
echo "════════════════════════════════════════════════════════════"
echo " ۵) فعال‌سازی و روشن کردن"
echo "════════════════════════════════════════════════════════════"
systemctl daemon-reload
systemctl enable --now "$SVC" 2>&1 | tail -2
sleep 5

echo
echo "════════════════════════════════════════════════════════════"
echo " ۶) وضعیت نهایی"
echo "════════════════════════════════════════════════════════════"
systemctl status "$SVC" --no-pager -l 2>/dev/null | head -16

cat << 'EOF'

════════════════════════════════════════════════════════════════
 ✅ تمام شد! از این به بعد:

    وضعیت        : systemctl status insta-ai-bot
    لاگ زنده     : journalctl -u insta-ai-bot -f
    ری‌استارت    : systemctl restart insta-ai-bot
    توقف         : systemctl stop insta-ai-bot

 🔁 حالا حتی اگه:
      • سشن SSH قطع بشه        • لپ‌تاپ خواب بره
      • ترمینال بسته بشه       • سرور ری‌استارت بشه
      • پروسه به هر دلیلی بمیره
    ...ربات ۱۰ ثانیه بعد خودش برمی‌گرده. ✅
════════════════════════════════════════════════════════════════
EOF
