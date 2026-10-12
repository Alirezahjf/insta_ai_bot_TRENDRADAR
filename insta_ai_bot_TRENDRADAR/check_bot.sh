#!/usr/bin/env bash
# ============================================================
# 🩺 گزارش تشخیصی ربات — این فایل رو روی سرور اجرا کن:
#     chmod +x check_bot.sh && ./check_bot.sh
# خروجی رو کامل کپی کن و برام بفرست.
# (هیچ چیزی رو تغییر نمی‌ده — فقط می‌خونه و گزارش می‌ده)
# ============================================================
set -u
BOT_NAME="${1:-bot.py}"      # اگه اسم فایل رباتت فرق داره: ./check_bot.sh mybot.py

line() { echo; echo "────────────────────────────────────────────────────────────"; echo "▌ $*"; echo "────────────────────────────────────────────────────────────"; }

line "۱) وضعیت کلی"
echo "زمان سرور : $(date '+%Y-%m-%d %H:%M:%S  (%Z)')"
echo "هاست      : $(hostname)"
uptime 2>/dev/null || true

line "۲) منابع (RAM / دیسک / سواپ)"
free -m 2>/dev/null || true
echo
df -h / 2>/dev/null || true

line "۳) پروسه‌های ربات — چند نسخه در حال اجراست؟ (باید ۱ باشه)"
echo "جستجوی: $BOT_NAME"
if command -v pgrep >/dev/null 2>&1; then
    # فقط پروسه‌های پایتون که این فایل رو اجرا می‌کنن (خود اسکریپت رو نشمار)
    PIDS=$(pgrep -f "python[0-9.]*[[:space:]].*${BOT_NAME}" 2>/dev/null | while read -r x; do
               [ "$x" = "$$" ] && continue
               grep -qa "python" "/proc/$x/comm" 2>/dev/null && echo "$x"
           done || true)
    PIDS=$(echo "$PIDS" | tr -d '\r' | sed '/^$/d')
    if [ -z "$PIDS" ]; then
        echo "⛔ هیچ پروسه‌ای با نام «$BOT_NAME» در حال اجرا نیست!"
    else
        n=$(printf '%s\n' "$PIDS" | wc -l)
        echo "✅ تعداد پروسه: $n"
        [ "$n" -gt 1 ] && echo "⚠️⚠️ بیشتر از یکی! این باعث خطای ۴۰۹ تلگرام و رفتار عجیب می‌شه."
        for p in $PIDS; do
            echo
            echo "  PID         : $p"
            echo "  شروع        : $(ps -o lstart= -p "$p" 2>/dev/null)"
            echo "  ترمینال     : $(ps -o tty= -p "$p" 2>/dev/null | tr -d ' ')   ($([ "$(ps -o tty= -p $p | tr -d ' ')" = "?" ] && echo 'جدا از ترمینال ✓ این خوبه' || echo 'وابسته به ترمینال ⚠️ با بستن سشن می‌میره'))"
            echo "  رم (RSS)    : $(ps -o rss= -p "$p" 2>/dev/null | awk '{printf "%.0f MB", $1/1024}')"
            echo "  CPU time    : $(ps -o time= -p "$p" 2>/dev/null)"
            echo "  پوشه اجرا   : $(readlink -f /proc/$p/cwd 2>/dev/null || echo '؟')"
            echo "  دستور کامل  : $(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null || echo '؟')"
        done
    fi
else
    ps aux | grep -i "[b]ot" || true
fi

line "۴) آیا داخل tmux/screen اجرا شده؟"
command -v tmux   >/dev/null 2>&1 && { echo "[tmux]";   tmux ls 2>/dev/null || echo "  سشنی نیست"; } || echo "[tmux] نصب نیست"
command -v screen >/dev/null 2>&1 && { echo "[screen]"; screen -ls 2>/dev/null || echo "  سشنی نیست"; } || echo "[screen] نصب نیست"
command -v systemctl >/dev/null 2>&1 && {
    echo "[systemd] سرویس‌های مرتبط:"
    systemctl list-units --type=service --state=running --no-pager 2>/dev/null | grep -iE "bot|insta" || echo "  سرویس running پیدا نشد"
    systemctl status insta-ai-bot --no-pager 2>/dev/null | head -12 || true
}

line "۵) آیا OOM (کمبود رم) پروسه‌ای رو کشته؟"
dmesg -T 2>/dev/null | grep -iE "out of memory|killed process" | tail -10 || echo "  دسترسی به dmesg نبود یا خروجی خالی (خالی = OOM نبوده ✓)"
journalctl -b --no-pager 2>/dev/null | grep -iE "oom-kill|out of memory|killed process" | tail -10 || true
echo "(خالی بودن = مشکل رم نبوده)"

line "۶) چه چیزی سشن‌ها/پروسه‌ها رو بسته؟ ← دنبال خودِ قاتل"
journalctl -b --no-pager 2>/dev/null | grep -iE "session .*(closed|terminated|removed)|killing|Terminated|sigterm" | tail -25 || echo "  journalctl در دسترس نیست"
echo
echo "— لاگ sshd —"
journalctl -u ssh -u sshd -b --no-pager 2>/dev/null | tail -25 || true

line "۷) آخرین لاگین‌ها (ببین سشن‌ها چقدر عمر می‌کنن)"
last -n 25 2>/dev/null || true

line "۸) کرون‌جاب‌ها (شاید یه اسکریپت داره ربات رو می‌کشه)"
echo "— crontab کاربر root —"
crontab -l 2>/dev/null || echo "  خالی"
echo
echo "— /etc/cron.d —"
ls -la /etc/cron.d/ 2>/dev/null || true
echo
echo "— /etc/crontab —"
grep -vE "^\s*#|^\s*$" /etc/crontab 2>/dev/null || true

line "۹) لاگ خود ربات (اگه از نسخه اصلاح‌شده استفاده می‌کنی: bot.log)"
for f in /root/Desktop/insta_ai_bot/bot.log /root/Desktop/insta_ai_bot/watchdog.log ./bot.log ./watchdog.log; do
    [ -f "$f" ] && { echo "── $f ──"; tail -30 "$f"; echo; }
done

line "✅ تمام — کل این خروجی رو کپی کن"
