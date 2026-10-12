"""خواندن تنظیمات از فایل .env"""
import os

# تلاش برای لود dotenv (اگه نصب نبود، دستی parse می‌کنیم)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    _env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(_env_path):
        with open(_env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _int_list(s, default=""):
    out = []
    for p in (s or default).split(","):
        p = p.strip()
        if p.lstrip("-").isdigit():
            out.append(int(p))
    return out


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN", "")
AVALAI_API_KEY = os.getenv("AVALAI_API_KEY", "")
AVALAI_BASE_URL = os.getenv("AVALAI_BASE_URL", "https://api.avalai.ir/v1")
AVALAI_MODEL = os.getenv("AVALAI_MODEL", "gpt-4o-mini")
ADMIN_IDS = _int_list(os.getenv("ADMIN_IDS", ""))
TIMEZONE = os.getenv("TIMEZONE", "Asia/Tehran")
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "schedules.db"))
SHOW_APIFY_LOGS = os.getenv("SHOW_APIFY_LOGS", "0") == "1"


def validate():
    """لیست خطاهای تنظیمات (خالی = اوکی)"""
    missing = []
    if not TELEGRAM_BOT_TOKEN or "PUT_YOUR" in TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not APIFY_API_TOKEN or "PUT_YOUR" in APIFY_API_TOKEN:
        missing.append("APIFY_API_TOKEN")
    if not AVALAI_API_KEY or "PUT_YOUR" in AVALAI_API_KEY:
        missing.append("AVALAI_API_KEY (برای تحلیل AI لازمه)")
    return missing
