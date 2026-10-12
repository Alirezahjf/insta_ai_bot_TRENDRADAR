"""فلو اسکرپ فوری (بدون اسکژول) — ورودی دستی + دکمه‌ای"""
import threading

from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

import jobs
from core import (bot, cancel_keyboard, count_keyboard, estimate_cost, get_user,
                  num_inline, parse_hashtags, parse_int_in_range, reset_user,
                  type_inline, yes_no_inline)


def start_flow(chat_id):
    u = get_user(chat_id)
    u.clear()
    u.update({"flow": "instant", "state": "WAITING_HASHTAGS"})
    bot.send_message(chat_id,
        "🏷️ <b>قدم ۱ از ۵: هشتگ‌ها</b>\n\n"
        "هشتگ‌هات رو بفرست (تکی یا چندتایی):\n"
        "• با کاما: <code>travel, food</code> • یا هر کدوم تو یه خط\n"
        "• با یا بدون <code>#</code>\n\n⚠️ حداکثر ۵ هشتگ",
        reply_markup=cancel_keyboard())


def on_text(message, u) -> bool:
    """True یعنی این پیام مال این فلو بود و هندل شد"""
    chat_id, state, txt = message.chat.id, u.get("state"), message.text or ""

    if state == "WAITING_HASHTAGS":
        tags = parse_hashtags(txt)
        if not tags:
            bot.send_message(chat_id, "⚠️ هشتگ معتبری پیدا نکردم! مثال: <code>travel, food</code>")
            return True
        if len(tags) > 5:
            bot.send_message(chat_id, f"⚠️ حداکثر ۵ هشتگ! {len(tags)} تا فرستادی، ۵ تای اول رو برداشتم.")
            tags = tags[:5]
        u["hashtags"] = tags
        u["state"] = "WAITING_COUNT"
        bot.send_message(chat_id,
            f"✅ ثبت شد: <b>{'، '.join('#' + t for t in tags)}</b>\n\n"
            f"🔢 <b>قدم ۲ از ۵: تعداد خروجی</b>\n"
            f"به ازای <b>هر هشتگ</b> چند نتیجه؟ (۱ تا ۲۰۰)\n"
            f"از دکمه‌ها بزن یا عدد رو تایپ کن 👇",
            reply_markup=count_keyboard())
        return True

    if state == "WAITING_COUNT":
        n = parse_int_in_range(txt, 1, 200)
        if n is None:
            bot.send_message(chat_id, "⚠️ فقط یه عدد بین ۱ تا ۲۰۰ بفرست!")
            return True
        u["count"] = n
        u["state"] = "WAITING_TYPE"
        bot.send_message(chat_id,
            f"✅ تعداد: <b>{n}</b>\n\n📦 <b>قدم ۳ از ۵: نوع محتوا</b>",
            reply_markup=type_inline("type"))
        return True

    if state == "WAITING_TOP_N":
        n = parse_int_in_range(txt, 1, 20)
        if n is None:
            bot.send_message(chat_id, "⚠️ عدد بین ۱ تا ۲۰ (یا از دکمه‌ها بزن)")
            return True
        u["top_n"] = n
        u["state"] = "WAITING_COMMENTS_PER"
        bot.send_message(chat_id,
            f"✅ پست‌های برتر: <b>{n}</b>\n\n💬 برای <b>هر پست</b> چند کامنت؟ (۱ تا ۵۰)\nاز دکمه‌ها بزن یا تایپ کن 👇",
            reply_markup=num_inline("cpc", [5, 10, 20]))
        return True

    if state == "WAITING_COMMENTS_PER":
        n = parse_int_in_range(txt, 1, 50)
        if n is None:
            bot.send_message(chat_id, "⚠️ عدد بین ۱ تا ۵۰ (یا از دکمه‌ها بزن)")
            return True
        u["comments_per"] = n
        _show_confirm(chat_id)
        return True

    return False


def on_callback(call, u) -> bool:
    chat_id, mid, data = call.message.chat.id, call.message.message_id, call.data

    if data.startswith("type_"):
        u["content_type"] = data.replace("type_", "")
        u["state"] = "WAITING_COMMENTS_ASK"
        bot.edit_message_text(
            f"✅ نوع: <b>{jobs.TYPE_NAMES.get(u['content_type'])}</b>\n\n"
            f"💬 <b>قدم ۴ از ۵:</b> متن کامنت‌های پست‌های برتر رو هم می‌خوای؟",
            chat_id, mid, reply_markup=yes_no_inline("cm_yes", "cm_no",
                "✅ بله، کامنت هم می‌خوام", "❌ نه"))
        return True

    if data == "cm_no":
        u["want_comments"] = False
        _show_confirm(chat_id)
        return True

    if data == "cm_yes":
        u["want_comments"] = True
        u["state"] = "WAITING_TOP_N"
        bot.edit_message_text(
            "✅ کامنت: <b>بله</b>\n\n🏆 برای <b>چند پست برتر</b> کامنت بگیرم؟ (۱ تا ۲۰)\nاز دکمه‌ها بزن یا تایپ کن 👇",
            chat_id, mid, reply_markup=num_inline("top", [3, 5, 10]))
        return True

    if data.startswith("top_"):
        u["top_n"] = int(data.replace("top_", ""))
        u["state"] = "WAITING_COMMENTS_PER"
        bot.edit_message_text(
            f"✅ پست‌های برتر: <b>{u['top_n']}</b>\n\n💬 برای <b>هر پست</b> چند کامنت؟ (۱ تا ۵۰) 👇",
            chat_id, mid, reply_markup=num_inline("cpc", [5, 10, 20]))
        return True

    if data.startswith("cpc_"):
        u["comments_per"] = int(data.replace("cpc_", ""))
        _show_confirm(chat_id)
        return True

    if data == "confirm_cancel":
        from core import main_menu
        reset_user(chat_id)
        bot.edit_message_text("❌ لغو شد.", chat_id, mid)
        bot.send_message(chat_id, "منوی اصلی 👇", reply_markup=main_menu())
        return True

    if data == "confirm_start":
        u["state"] = "RUNNING"
        bot.edit_message_text("⏳ <b>در حال اجرا...</b>", chat_id, mid)
        snapshot = dict(u)
        threading.Thread(target=jobs.run_pipeline, args=(chat_id, snapshot), daemon=True).start()
        return True

    return False


def _show_confirm(chat_id):
    u = get_user(chat_id)
    u["state"] = "WAITING_CONFIRM"
    tags, count = u.get("hashtags", []), u.get("count", 0)
    n_types = 2 if u.get("content_type") == "both" else 1
    n_posts = len(tags) * count * n_types
    if u.get("want_comments"):
        n_comments = u.get("top_n", 5) * u.get("comments_per", 10)
        cm_line = f"💬 کامنت: ✅ ({u.get('top_n')} پست × {u.get('comments_per')} = {n_comments})"
    else:
        n_comments, cm_line = 0, "💬 کامنت: ❌"
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ شروع اسکرپ", callback_data="confirm_start"),
           InlineKeyboardButton("❌ انصراف", callback_data="confirm_cancel"))
    # آخرین پیام رو پیدا و ویرایش کن؛ اگه نشد پیام جدید
    try:
        bot.send_message(chat_id,
            "🧾 <b>قدم ۵ از ۵: تأیید نهایی</b>\n\n"
            f"🏷️ هشتگ‌ها: <b>{'، '.join('#' + t for t in tags)}</b>\n"
            f"🔢 تعداد: <b>{count}</b> به ازای هر هشتگ\n"
            f"📦 نوع: <b>{jobs.TYPE_NAMES.get(u.get('content_type'))}</b>\n"
            f"{cm_line}\n\n"
            f"📊 جمع نتایج: حدود <b>{n_posts + n_comments}</b>\n"
            f"💰 هزینه تقریبی: <b>${estimate_cost(n_posts, n_comments):.2f}</b>\n\nشروع کنم؟",
            reply_markup=kb)
    except Exception:
        pass
