"""هسته مشترک: آبجکت ربات، وضعیت کاربرها، کیبوردها، ابزارها"""
import re
from datetime import datetime, timedelta, timezone

import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

import config

# ⚠️ num_threads: پیش‌فرض کتابخونه فقط ۲ تاست. چون اسکرپ Apify چند دقیقه طول می‌کشه
# و هندلرها همون‌جا (سینک) اجرا می‌شن، با ۲ تا ترد ربات «قفل» می‌شه و جواب نمی‌ده.
bot = telebot.TeleBot(config.TELEGRAM_BOT_TOKEN, parse_mode="HTML", num_threads=16)

# ---------- ساعت تهران ----------
try:
    from zoneinfo import ZoneInfo
    TEHRAN = ZoneInfo(config.TIMEZONE)
except Exception:
    TEHRAN = timezone(timedelta(hours=3, minutes=30))  # fallback


def tehran_now():
    return datetime.now(TEHRAN)


# ---------- وضعیت کاربر ----------
user_data = {}


def get_user(chat_id):
    if chat_id not in user_data:
        user_data[chat_id] = {"state": None}
    return user_data[chat_id]


def reset_user(chat_id):
    user_data[chat_id] = {"state": None}


# ---------- دسترسی ----------
def is_allowed(user_id):
    if not config.ADMIN_IDS:
        return True
    return user_id in config.ADMIN_IDS


def check_access(message):
    if not is_allowed(message.from_user.id):
        bot.send_message(message.chat.id,
            "⛔ <b>دسترسی نداری!</b>\nاین ربات خصوصیه. آیدی عددیت رو برای ادمین بفرست.")
        return False
    return True


# ---------- کیبوردها ----------
def main_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        KeyboardButton("🔍 اسکرپ جدید"),
        KeyboardButton("⏰ اسکژول اسکرپ"),
        KeyboardButton("📜 ران‌های قبلی"),
        KeyboardButton("⭐ آخرین خروجی"),
        KeyboardButton("📖 راهنما"),
        KeyboardButton("💰 تعرفه"),
        KeyboardButton("❌ انصراف"),
    )
    return kb


MENU_TEXTS = ["🔍 اسکرپ جدید", "⏰ اسکژول اسکرپ", "📜 ران‌های قبلی",
              "⭐ آخرین خروجی", "📖 راهنما", "💰 تعرفه", "❌ انصراف"]


def cancel_keyboard():
    return ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton("❌ انصراف"))


def count_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=4)
    kb.add(KeyboardButton("10"), KeyboardButton("20"), KeyboardButton("50"), KeyboardButton("100"))
    kb.add(KeyboardButton("❌ انصراف"))
    return kb


def type_inline(prefix="type"):
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("📷 فقط پست", callback_data=f"{prefix}_posts"),
        InlineKeyboardButton("🎬 فقط ریلز", callback_data=f"{prefix}_reels"),
    )
    kb.add(InlineKeyboardButton("📷+🎬 هر دو (پیشنهاد)", callback_data=f"{prefix}_both"))
    return kb


def yes_no_inline(yes_cb, no_cb, yes_txt="✅ بله", no_txt="❌ نه"):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(yes_txt, callback_data=yes_cb),
           InlineKeyboardButton(no_txt, callback_data=no_cb))
    return kb


def num_inline(prefix, options):
    kb = InlineKeyboardMarkup()
    kb.add(*[InlineKeyboardButton(str(o), callback_data=f"{prefix}_{o}") for o in options])
    return kb


# ---------- ابزار متن ----------
FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def fa_to_en(s):
    return (s or "").translate(FA_DIGITS)


def parse_hashtags(text: str):
    parts = re.split(r"[,\n\s]+", (text or "").strip())
    tags = []
    for p in parts:
        p = p.strip().lstrip("#").strip()
        p = re.sub(r"https?://\S+", "", p).strip()
        if not p or len(p) > 100:
            continue
        if p not in tags:
            tags.append(p)
    return tags


def parse_times(text: str):
    """ورودی مثل 08:00, 12:30 — خروجی: (لیست ['08:00',...] | None, خطا | None)"""
    text = fa_to_en(text or "").replace("؛", ",").replace("，", ",").replace(".", ":")
    parts = re.split(r"[,\n\s]+", text.strip())
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", p)
        if not m:
            return None, f"قالب «{p}» درست نیست! مثال درست: 08:00"
        out.append(f"{int(m.group(1)):02d}:{m.group(2)}")
    out = sorted(set(out))
    if not out:
        return None, "ساعتی پیدا نکردم! مثال: 08:00, 12:30"
    if len(out) > 8:
        return None, "حداکثر ۸ ساعت در روز!"
    return out, None


def parse_int_in_range(text, lo, hi):
    try:
        n = int(fa_to_en((text or "").strip()))
    except Exception:
        return None
    if lo <= n <= hi:
        return n
    return None


def to_hashtag_urls(hashtags):
    return [f"https://www.instagram.com/explore/tags/{h}/" for h in hashtags]


def estimate_cost(n_posts, n_comments):
    return n_posts * 0.0027 + n_comments * 0.0023


def fmt_num(n):
    if n is None or n == "":
        return "—"
    try:
        n = int(n)
        if n >= 1000000:
            return f"{n / 1000000:.1f}M"
        if n >= 1000:
            return f"{n / 1000:.1f}K"
        return str(n)
    except Exception:
        return str(n)


def fmt_time(dt):
    if not dt:
        return "—"
    try:
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        return dt.astimezone(TEHRAN).strftime("%m-%d %H:%M")
    except Exception:
        return str(dt)[:16]


def send_long(chat_id, text, header=""):
    """ارسال متن بلند با تکه‌تکه کردن (سقف تلگرام ۴۰۹۶)"""
    chunks, cur = [], ""
    for para in text.split("\n"):
        if len(cur) + len(para) + 1 > 3800:
            chunks.append(cur)
            cur = ""
        cur += para + "\n"
    if cur.strip():
        chunks.append(cur)
    for i, ch in enumerate(chunks):
        prefix = header if i == 0 else f"(ادامه {i + 1}/{len(chunks)})\n"
        bot.send_message(chat_id, prefix + ch)
