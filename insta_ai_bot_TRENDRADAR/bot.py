"""نقطه ورود ربات — روترها + اجرا
⚠️ ترتیب ثبت هندلرها مهمه: اول دکمه‌های خاص، آخرش هندلر عمومی متن‌ها
"""
import logging
import os
import signal
import sys
import threading
import time

import config
import database
import instant_flow
import jobs
import runs_flow
import sched_flow
from core import MENU_TEXTS, bot, check_access, get_user, main_menu, reset_user, tehran_now


# ============================================================
# 📝 لاگ‌گیری (کنسول + فایل bot.log با تاریخ و ساعت)
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "bot.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
    force=True,
)
log = logging.getLogger("insta_ai_bot")


def _on_signal(signum, _frame):
    """اگه کسی از بیرون SIGTERM/SIGINT/SIGHUP بفرسته، تو لاگ ثبت می‌شه"""
    try:
        name = signal.Signals(signum).name
    except Exception:
        name = str(signum)
    log.warning("⛔ سیگنال %s دریافت شد → پروسه داره بسته می‌شه (عامل بیرونی؛ قطع SSH، اسکژولر سیستم، پنل هاست، OOM و...) ================================", name)
    sys.exit(0)


for _sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
    try:
        signal.signal(_sig, _on_signal)
    except Exception:
        pass


def _rss_mb():
    """مصرف رم پروسه به مگابایت (برای تشخیص نشت حافظه)"""
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 0


def _heartbeat_loop():
    """هر ۳۰ دقیقه یه خط لاگ می‌ندازه: عمر ربات + رم + تعداد ترد"""
    start = time.time()
    while True:
        time.sleep(1800)
        try:
            mins = int((time.time() - start) // 60)
            log.info("💓 heartbeat | age=%d min | RAM=%d MB | threads=%d",
                     mins, _rss_mb(), threading.active_count())
        except Exception:
            pass



@bot.message_handler(commands=["start"])
def cmd_start(message):
    if not check_access(message):
        return
    reset_user(message.chat.id)
    bot.send_message(message.chat.id,
        "سلام! 👋\nمن ربات <b>هوشمند اسکرپ و تحلیل اینستاگرام</b> هستم 📸🤖\n\n"
        "🔍 <b>اسکرپ جدید</b> — اسکرپ فوری هشتگ\n"
        "⏰ <b>اسکژول اسکرپ</b> — اجرای خودکار سر ساعت + تحلیل AI\n"
        "📜 <b>ران‌های قبلی</b> — مرور و دانلود نتایج قدیمی\n"
        "⭐ <b>آخرین خروجی</b> — نمایش آخرین نتیجه\n\n"
        f"🕐 ساعت تهران الان: <b>{tehran_now().strftime('%H:%M')}</b>\n\nاز منو انتخاب کن 👇",
        reply_markup=main_menu())


@bot.message_handler(commands=["cancel"])
def cmd_cancel(message):
    reset_user(message.chat.id)
    bot.send_message(message.chat.id, "❌ لغو شد 👇", reply_markup=main_menu())


@bot.message_handler(func=lambda m: m.text == "❌ انصراف")
def btn_cancel(message):
    if not check_access(message):
        return
    reset_user(message.chat.id)
    bot.send_message(message.chat.id, "❌ لغو شد. از منو انتخاب کن 👇", reply_markup=main_menu())


@bot.message_handler(func=lambda m: m.text == "🔍 اسکرپ جدید")
def btn_instant(message):
    if not check_access(message):
        return
    instant_flow.start_flow(message.chat.id)


@bot.message_handler(func=lambda m: m.text == "⏰ اسکژول اسکرپ")
def btn_sched(message):
    if not check_access(message):
        return
    sched_flow.send_hub(message)


@bot.message_handler(func=lambda m: m.text == "📜 ران‌های قبلی")
def btn_runs(message):
    if not check_access(message):
        return
    runs_flow.send_list(message)


@bot.message_handler(func=lambda m: m.text == "⭐ آخرین خروجی")
def btn_last(message):
    if not check_access(message):
        return
    runs_flow.send_last_output(message)


@bot.message_handler(func=lambda m: m.text == "📖 راهنما")
def btn_help(message):
    if not check_access(message):
        return
    bot.send_message(message.chat.id,
        "📖 <b>راهنما</b>\n\n"
        "🔍 <b>اسکرپ جدید:</b> هشتگ بده → تعداد → نوع → کامنت → نتیجه\n"
        "⏰ <b>اسکژول اسکرپ:</b> ساعت‌های تهران رو بده (مثل <code>08:00, 12:30</code>) + تنظیمات اسکرپ. "
        "سر ساعت خودکار اجرا میشه: اسکرپ → وایرال‌اسکور → کامنت → <b>تحلیل AI</b> → ارسال گزارش تو همین چت.\n"
        "• اسکژول‌ها رو می‌تونی ویرایش، خاموش/روشن، دستی اجرا یا حذف کنی (سطل آشغال هم داره 🗑️)\n\n"
        "🤖 <b>تحلیل AI:</b> بین ۳ پست برتر الگوریتم، تک‌پست برتر رو با دلیل، نقاط قوت/ضعف و راهنمای پست‌گذاری معرفی می‌کنه.\n"
        "👁️ <b>بازدید</b> فقط برای ریلز/ویدیوست. فقط پیج‌های <b>پابلیک</b>.\n"
        "⌨️ همه عددها رو هم می‌تونی از دکمه بزنی هم تایپ کنی (حتی با اعداد فارسی).",
        reply_markup=main_menu())


@bot.message_handler(func=lambda m: m.text == "💰 تعرفه")
def btn_price(message):
    if not check_access(message):
        return
    bot.send_message(message.chat.id,
        "💰 <b>تعرفه‌ها</b>\n\n"
        "📷 هر ۱۰۰۰ پست/ریلز Apify: <b>$2.70</b>\n"
        "💬 هر ۱۰۰۰ کامنت Apify: <b>$2.30</b>\n"
        "🤖 هر تحلیل AI (مدل سبک): حدود <b>$0.01 تا $0.03</b>\n\n"
        "مثال اسکژول: ۱ هشتگ × ۲۰ پست+۲۰ ریلز + ۵×۱۰ کامنت + تحلیل AI ≈ <b>$0.25</b> به ازای هر اجرا.",
        reply_markup=main_menu())


# ---------- هندلر عمومی متن‌ها (حتماً آخر!) ----------
@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(message):
    if not check_access(message):
        return
    if message.text in MENU_TEXTS:
        return
    u = get_user(message.chat.id)
    if instant_flow.on_text(message, u):
        return
    if sched_flow.on_text(message, u):
        return
    bot.send_message(message.chat.id, "از منوی پایین انتخاب کن 👇 (یا /start)", reply_markup=main_menu())


# ---------- روتر کال‌بک‌ها ----------
@bot.callback_query_handler(func=lambda c: True)
def handle_callback(call):
    from core import is_allowed
    if not is_allowed(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ دسترسی نداری!", show_alert=True)
        return
    u = get_user(call.message.chat.id)
    d = call.data or ""
    handled = False
    if (d.startswith("type_") or d in ("cm_yes", "cm_no") or d.startswith("top_")
            or d.startswith("cpc_") or d.startswith("confirm_")):
        handled = instant_flow.on_callback(call, u)
    elif (d.startswith("stype_") or d.startswith("scm_") or d.startswith("stop_")
            or d.startswith("scpc_") or d.startswith("sch")):
        handled = sched_flow.on_callback(call, u)
    elif (d.startswith("run_") or d.startswith("dlc_") or d.startswith("dlj_")
            or d.startswith("full_") or d == "runs_back"):
        handled = runs_flow.on_callback(call)
    if handled:
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass


def main():
    missing = config.validate()
    if "TELEGRAM_BOT_TOKEN" in str(missing) or not config.TELEGRAM_BOT_TOKEN or "PUT_YOUR" in config.TELEGRAM_BOT_TOKEN:
        log.error("❌ TELEGRAM_BOT_TOKEN رو تو فایل .env بذار!")
        return
    if missing:
        log.warning("⚠️ هشدار تنظیمات: %s", ", ".join(missing))
    database.init(config.DB_PATH)
    log.info("=" * 70)
    log.info("🚀 شروع ربات | PID=%s | Python=%s | log=%s",
             os.getpid(), sys.version.split()[0], LOG_PATH)
    threading.Thread(target=jobs.scheduler_loop, daemon=True).start()
    threading.Thread(target=_heartbeat_loop, daemon=True).start()
    print(f"🤖 Bot is running... | Tehran: {tehran_now().strftime('%Y-%m-%d %H:%M')}")
    print(f"🤖 AI model: {config.AVALAI_MODEL} @ {config.AVALAI_BASE_URL}")
    try:
        bot.infinity_polling(skip_pending=True, timeout=60)
    except BaseException as e:
        log.exception("🛑 polling تموم شد: %s", e)
        raise
    log.warning("🛑 polling بدون خطا برگشت → پروسه داره بسته می‌شه ================================")


if __name__ == "__main__":
    main()
