#!/usr/bin/env bash
# ============================================================
# 🔁 نگهبان ربات — اگه پروسه به هر دلیلی بمیره، خودش دوباره بالا می‌آره
#    (برای سرورهایی که systemd ندارن، یا اگه نخوای سرویس نصب کنی)
#
# اجرا:
#   chmod +x run_forever.sh
#   nohup ./run_forever.sh >/dev/null 2>&1 &          # روش ساده
#   tmux new -s bot './run_forever.sh'                # روش بهتر (با tmux)
#   ./run_forever.sh                                  # فقط تست
#
# متوقف کردن:
#   pkill -f run_forever.sh && pkill -f bot.py
#
# لاگ‌ها:  bot.log  (خروجی خود ربات)  |  watchdog.log  (شروع/مرگ/ری‌استارت)
# ============================================================
cd "$(dirname "$0")" || exit 1

# پایتون venv اگه بود، وگرنه پایتون سیستم
if [ -x "./venv/bin/python" ]; then
    PY="./venv/bin/python"
elif [ -x "./.venv/bin/python" ]; then
    PY="./.venv/bin/python"
else
    PY="$(command -v python3 || command -v python)"
fi

RESTART_DELAY=10
MAX_FAST_CRASHES=5          # اگه ۵ بار پشت‌سرهم سریع کرش کرد، ترمز می‌کشه

echo "$(date '+%Y-%m-%d %H:%M:%S') | watchdog started | python=$PY | pid=$$" >> watchdog.log

fast_crashes=0
while true; do
    start_ts=$(date +%s)
    echo "$(date '+%Y-%m-%d %H:%M:%S') | ▶️  starting bot.py" >> watchdog.log

    "$PY" -u bot.py >> bot.log 2>&1
    code=$?
    ran_for=$(( $(date +%s) - start_ts ))

    echo "$(date '+%Y-%m-%d %H:%M:%S') | ⏹  bot.py exited (code=$code) after ${ran_for}s" >> watchdog.log

    # اگه کمتر از ۶۰ ثانیه دووم آورد یعنی مشکل جدی‌ست (توکن اشتباه، اینترنت، خطای کد)
    if [ "$ran_for" -lt 60 ]; then
        fast_crashes=$(( fast_crashes + 1 ))
    else
        fast_crashes=0
    fi

    if [ "$fast_crashes" -ge "$MAX_FAST_CRASHES" ]; then
        back=$(( RESTART_DELAY * 6 ))
        echo "$(date '+%Y-%m-%d %H:%M:%S') | ⚠️  $fast_crashes کرش پشت‌سرهم سریع → ${back} ثانیه صبر می‌کنم. bot.log رو ببین!" >> watchdog.log
        sleep "$back"
        fast_crashes=0
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') | ↻  restart in ${RESTART_DELAY}s..." >> watchdog.log
        sleep "$RESTART_DELAY"
    fi
done
