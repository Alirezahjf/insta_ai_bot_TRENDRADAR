# راهنمای امن به‌روزرسانی سرور

این راهنما برای سروری است که قبلاً commit `7f719de` روی آن نصب شده است. عملیات را در پنل/چت عمومی انجام ندهید و خروجی محرمانه را به اشتراک نگذارید.

## ۱) پشتیبان‌گیری پیش از هر عملیات Git یا restart

سرویس را متوقف کنید و از `.env`، همهٔ فایل‌های session، دیتابیس‌ها و مسیرهای سفارشی نسخهٔ پشتیبان بگیرید. نام مسیرها را از تنظیمات واقعی خودتان بخوانید؛ فایل‌های زیر نمونه‌اند و ممکن است مسیر سفارشی داشته باشند:

```bash
cd /root/Desktop/trendradar-new/insta_ai_bot_TRENDRADAR
sudo systemctl stop insta-ai-bot
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="/root/trendradar-backup-$STAMP"
sudo mkdir -p "$BACKUP"
sudo cp -a .env "$BACKUP/" 2>/dev/null || true
sudo find . -maxdepth 3 -type f \( -name '*.session' -o -name '*.session-*' -o -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' \) -exec cp --parents -a {} "$BACKUP/" \;
sudo chmod -R go-rwx "$BACKUP"
```

اگر `TG_SESSION_DIR`، `DB_PATH` یا `TREND_DB_PATH` مسیر بیرون از پوشهٔ برنامه دارد، آن مسیرها را نیز جداگانه backup بگیرید. backup را در Git اضافه نکنید.

## ۲) بررسی وضعیت و دریافت تغییرات

ابتدا مطمئن شوید working tree تغییر محلی پیش‌بینی‌نشده دارد یا نه. اگر تغییر محلی دارید، آن را بازنشانی نکنید و ابتدا diff را بررسی کنید:

```bash
git status --short --branch
git log -1 --oneline
git diff --stat
```

این تغییرات در برنچ `arena/e061c99f-insta-ai-bot-trendradar` و یک PR جداگانه ارائه شده‌اند، زیرا محیط اجرا اجازهٔ push به برنچ PR #2 را نمی‌داد. پس از بررسی/ادغام PR جدید در برنچ هدف، سرور را با fast-forward به‌روز کنید. نمونه برای برنچ PR جدید:

```bash
git fetch origin arena/e061c99f-insta-ai-bot-trendradar
git merge --ff-only FETCH_HEAD
```

اگر سرور باید روی برنچ دیگری بماند، ابتدا PR را در همان برنچ هدف ادغام کنید و سپس فقط `git pull --ff-only` بزنید. از `git reset --hard`، `git clean`، حذف فایل‌ها یا بازنشانی دیتابیس استفاده نکنید.

## ۳) وابستگی و migration

وابستگی اجباری جدیدی اضافه نشده است؛ با این حال محیط مجازی را بررسی کنید:

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m compileall -q .
```

Migration به‌صورت افزایشی و خودکار در `database.init` هنگام شروع ربات اجرا می‌شود. دیتابیس قدیمی را حذف نکنید. پیش از شروع، می‌توانید روی یک کپی از دیتابیس، migration را آزمایش کنید:

```bash
python - <<'PY'
import database, os, shutil, tempfile
path = os.environ.get("DB_PATH", "schedules.db")
with tempfile.TemporaryDirectory() as tmp:
    copy = os.path.join(tmp, "copy.db")
    shutil.copy2(path, copy)
    database.init(copy)
    print("migration copy OK")
PY
```

این دستور فقط روی کپی موقت اجرا می‌شود و دیتابیس اصلی را تغییر نمی‌دهد.

## ۴) راه‌اندازی و بررسی

```bash
sudo systemctl start insta-ai-bot
systemctl status insta-ai-bot --no-pager
sudo journalctl -u insta-ai-bot -n 100 --no-pager
```

در لاگ فقط نوع خطا/وضعیت را بررسی کنید. توکن، session، کوکی، فایل `.env`، شماره تلفن، کد ورود یا محتوای backup را در چت/issue/PR ارسال نکنید. اگر سرویس بالا نیامد، ابتدا backup و مسیرهای سفارشی را بررسی کنید و بدون حذف داده، به commit قبلی برگردید یا از مسئول نگهداری کمک بگیرید.
