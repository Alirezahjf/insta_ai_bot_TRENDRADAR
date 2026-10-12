"""فلو اسکژول اسکرپ هوشمند: ساخت + لیست + ویرایش + سطل آشغال"""
import threading

from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

import database
import jobs
from core import (bot, cancel_keyboard, count_keyboard, estimate_cost, get_user,
                  main_menu, num_inline, parse_hashtags, parse_int_in_range,
                  parse_times, reset_user, type_inline, yes_no_inline)

TYPE_NAMES = {"posts": "📷 فقط پست", "reels": "🎬 فقط ریلز", "both": "📷+🎬 هر دو"}


def sched_card(s):
    status = "🟢 فعال" if s["enabled"] else "🔴 خاموش"
    times = "، ".join(s["times"]) if s["times"] else "—"
    tags = "، ".join("#" + t for t in s["hashtags"]) if s["hashtags"] else "—"
    cm = f"✅ ({s['top_n']} پست × {s['comments_per']})" if s["want_comments"] else "❌"
    return (f"{status} <b>اسکژول #{s['id']}</b>\n"
            f"🕐 ساعت‌ها (تهران): <b>{times}</b>\n"
            f"🏷️ هشتگ‌ها: {tags}\n"
            f"🔢 تعداد: {s['count_per_hashtag']} | 📦 {TYPE_NAMES.get(s['content_type'], s['content_type'])} | "
            f"💬 کامنت: {cm}")


# ---------- ورود ----------
def send_hub(message):
    scheds = database.list_schedules(message.chat.id, trashed=False)
    if not scheds:
        start_creation(message.chat.id, first=True)
        return
    wait = bot.send_message(message.chat.id, "⏰")
    render_hub(message.chat.id, wait.message_id)


def render_hub(chat_id, message_id):
    scheds = database.list_schedules(chat_id, trashed=False)
    trash_n = len(database.list_schedules(chat_id, trashed=True))
    if not scheds:
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("➕ ساخت اسکژول جدید", callback_data="sch_new"))
        if trash_n:
            kb.add(InlineKeyboardButton(f"🗑️ سطل آشغال ({trash_n})", callback_data="sch_trash"))
        bot.edit_message_text("⏰ <b>اسکژول‌ها</b>\n\n😕 هنوز اسکژولی نداری. بساز 👇",
                              chat_id, message_id, reply_markup=kb)
        return
    lines = [f"⏰ <b>اسکژول‌های تو</b> ({len(scheds)} تا)\n"]
    kb = InlineKeyboardMarkup()
    for s in scheds:
        emoji = "🟢" if s["enabled"] else "🔴"
        times = ",".join(s["times"]) if s["times"] else "—"
        lines.append(f"{emoji} <b>#{s['id']}</b> 🕐 {times} | {'، '.join('#' + t for t in s['hashtags'][:3])}")
        kb.add(InlineKeyboardButton(f"⚙️ #{s['id']} | 🕐 {times}", callback_data=f"sch_detail_{s['id']}"))
    kb.add(InlineKeyboardButton("➕ اسکژول جدید", callback_data="sch_new"))
    if trash_n:
        kb.add(InlineKeyboardButton(f"🗑️ سطل آشغال ({trash_n})", callback_data="sch_trash"))
    lines.append("\n👆 برای مدیریت، ویرایش یا اجرای دستی روی یه اسکژول بزن")
    bot.edit_message_text("\n".join(lines), chat_id, message_id, reply_markup=kb)


# ---------- جزئیات ----------
def show_detail(chat_id, message_id, sid):
    s = database.get_schedule(sid)
    if not s or s["trashed"]:
        bot.edit_message_text("❌ این اسکژول پیدا نشد (شاید حذف شده).", chat_id, message_id)
        return
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🔴 خاموش کن" if s["enabled"] else "🟢 روشن کن",
                               callback_data=f"sch_toggle_{sid}"),
           InlineKeyboardButton("▶️ اجرای دستی الان", callback_data=f"sch_run_{sid}"))
    kb.add(InlineKeyboardButton("✏️ ویرایش", callback_data=f"sch_edit_{sid}"),
           InlineKeyboardButton("🗑️ حذف", callback_data=f"sch_del_{sid}"))
    kb.add(InlineKeyboardButton("🔙 برگشت به لیست", callback_data="sch_hub"))
    last = s["last_triggered"].replace("|", " ساعت ") if s["last_triggered"] else "هنوز اجرا نشده"
    bot.edit_message_text(sched_card(s) + f"\n\n⏱️ آخرین اجرا: {last}", chat_id, message_id, reply_markup=kb)


def show_edit_menu(chat_id, message_id, sid):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🕐 ساعت‌ها", callback_data=f"sche_times_{sid}"),
           InlineKeyboardButton("🏷️ هشتگ‌ها", callback_data=f"sche_tags_{sid}"))
    kb.add(InlineKeyboardButton("🔢 تعداد", callback_data=f"sche_count_{sid}"),
           InlineKeyboardButton("📦 نوع محتوا", callback_data=f"sche_type_{sid}"))
    kb.add(InlineKeyboardButton("💬 تنظیمات کامنت", callback_data=f"sche_cm_{sid}"))
    kb.add(InlineKeyboardButton("🔙 برگشت", callback_data=f"sch_detail_{sid}"))
    bot.edit_message_text(f"✏️ <b>ویرایش اسکژول #{sid}</b>\n\nکدوم بخش رو عوض کنم؟",
                          chat_id, message_id, reply_markup=kb)


# ---------- سطل آشغال ----------
def show_trash(chat_id, message_id):
    items = database.list_schedules(chat_id, trashed=True)
    kb = InlineKeyboardMarkup()
    if not items:
        kb.add(InlineKeyboardButton("🔙 برگشت", callback_data="sch_hub"))
        bot.edit_message_text("🗑️ <b>سطل آشغال خالیه</b> ✨", chat_id, message_id, reply_markup=kb)
        return
    lines = [f"🗑️ <b>سطل آشغال</b> ({len(items)} تا)\n"]
    for s in items:
        times = ",".join(s["times"]) if s["times"] else "—"
        lines.append(f"• <b>#{s['id']}</b> 🕐 {times} | {'، '.join('#' + t for t in s['hashtags'][:3])}")
        kb.add(InlineKeyboardButton(f"↩️ برگردون #{s['id']}", callback_data=f"sch_restore_{s['id']}"),
               InlineKeyboardButton(f"❌ حذف کامل #{s['id']}", callback_data=f"sch_purge_{s['id']}"))
    kb.add(InlineKeyboardButton("🧹 خالی کردن کامل سطل", callback_data="sch_purgeall"))
    kb.add(InlineKeyboardButton("🔙 برگشت", callback_data="sch_hub"))
    bot.edit_message_text("\n".join(lines), chat_id, message_id, reply_markup=kb)


# ---------- ساخت اسکژول جدید ----------
def start_creation(chat_id, first=False):
    u = get_user(chat_id)
    u.clear()
    u.update({"flow": "sched", "state": "S_TIMES"})
    pre = "👋 هنوز اسکژولی نداری، بذار باهم بسازیم!\n\n" if first else ""
    bot.send_message(chat_id,
        f"{pre}⏰ <b>ساخت اسکژول — قدم ۱ از ۶: ساعت‌ها</b>\n\n"
        "توی طول روز چه ساعت‌هایی (به <b>وقت تهران</b>) اسکرپ انجام بشه؟\n"
        "با کاما جدا کن. مثال:\n<code>08:00, 12:30, 20:00</code>\n\n⚠️ حداکثر ۸ ساعت",
        reply_markup=cancel_keyboard())


def on_text(message, u) -> bool:
    chat_id, state, txt = message.chat.id, u.get("state"), message.text or ""

    # ----- ساخت -----
    if state == "S_TIMES":
        times, err = parse_times(txt)
        if err:
            bot.send_message(chat_id, f"⚠️ {err}")
            return True
        u["times"] = times
        u["state"] = "S_HASHTAGS"
        bot.send_message(chat_id,
            f"✅ ساعت‌ها: <b>{'، '.join(times)}</b> (تهران)\n\n"
            f"🏷️ <b>قدم ۲ از ۶: هشتگ‌ها</b>\nبفرست (مثال: <code>travel, food</code>) — حداکثر ۵ تا",
            reply_markup=cancel_keyboard())
        return True

    if state == "S_HASHTAGS":
        tags = parse_hashtags(txt)
        if not tags:
            bot.send_message(chat_id, "⚠️ هشتگ معتبر پیدا نکردم!")
            return True
        if len(tags) > 5:
            tags = tags[:5]
        u["hashtags"] = tags
        u["state"] = "S_COUNT"
        bot.send_message(chat_id,
            f"✅ هشتگ‌ها: <b>{'، '.join('#' + t for t in tags)}</b>\n\n"
            f"🔢 <b>قدم ۳ از ۶: تعداد</b>\nبه ازای هر هشتگ چند نتیجه؟ (۱ تا ۲۰۰)\nدکمه بزن یا تایپ کن 👇",
            reply_markup=count_keyboard())
        return True

    if state == "S_COUNT":
        n = parse_int_in_range(txt, 1, 200)
        if n is None:
            bot.send_message(chat_id, "⚠️ عدد بین ۱ تا ۲۰۰!")
            return True
        u["count"] = n
        u["state"] = "S_TYPE"
        bot.send_message(chat_id, f"✅ تعداد: <b>{n}</b>\n\n📦 <b>قدم ۴ از ۶: نوع محتوا</b>",
                         reply_markup=type_inline("stype"))
        return True

    if state == "S_TOPN":
        n = parse_int_in_range(txt, 1, 20)
        if n is None:
            bot.send_message(chat_id, "⚠️ عدد بین ۱ تا ۲۰!")
            return True
        u["top_n"] = n
        u["state"] = "S_CPC"
        bot.send_message(chat_id,
            f"✅ پست‌های برتر: <b>{n}</b>\n\n💬 برای <b>هر پست</b> چند کامنت؟ (۱ تا ۵۰) 👇",
            reply_markup=num_inline("scpc", [5, 10, 20]))
        return True

    if state == "S_CPC":
        n = parse_int_in_range(txt, 1, 50)
        if n is None:
            bot.send_message(chat_id, "⚠️ عدد بین ۱ تا ۵۰!")
            return True
        u["comments_per"] = n
        _show_sched_confirm(chat_id)
        return True

    # ----- ویرایش -----
    ed = u.get("edit") or {}
    sid = ed.get("id")

    if state == "E_TIMES":
        times, err = parse_times(txt)
        if err:
            bot.send_message(chat_id, f"⚠️ {err}")
            return True
        database.update_schedule(sid, {"times": times})
        bot.send_message(chat_id, f"✅ ساعت‌ها شد: <b>{'، '.join(times)}</b>",
                         reply_markup=main_menu())
        return True

    if state == "E_HASHTAGS":
        tags = parse_hashtags(txt)
        if not tags:
            bot.send_message(chat_id, "⚠️ هشتگ معتبر پیدا نکردم!")
            return True
        database.update_schedule(sid, {"hashtags": tags[:5]})
        bot.send_message(chat_id, "✅ هشتگ‌ها آپدیت شد.", reply_markup=main_menu())
        return True

    if state == "E_COUNT":
        n = parse_int_in_range(txt, 1, 200)
        if n is None:
            bot.send_message(chat_id, "⚠️ عدد بین ۱ تا ۲۰۰!")
            return True
        database.update_schedule(sid, {"count_per_hashtag": n})
        bot.send_message(chat_id, f"✅ تعداد شد: <b>{n}</b>", reply_markup=main_menu())
        return True

    if state == "E_TOPN":
        n = parse_int_in_range(txt, 1, 20)
        if n is None:
            bot.send_message(chat_id, "⚠️ عدد بین ۱ تا ۲۰!")
            return True
        database.update_schedule(sid, {"top_n": n})
        u["state"] = "E_CPC"
        bot.send_message(chat_id,
            f"✅ شد <b>{n}</b> پست برتر.\n\n💬 برای هر پست چند کامنت؟ (۱ تا ۵۰) 👇",
            reply_markup=num_inline("schecpc", [5, 10, 20]))
        return True

    if state == "E_CPC":
        n = parse_int_in_range(txt, 1, 50)
        if n is None:
            bot.send_message(chat_id, "⚠️ عدد بین ۱ تا ۵۰!")
            return True
        database.update_schedule(sid, {"comments_per": n})
        bot.send_message(chat_id, f"✅ شد <b>{n}</b> کامنت به ازای هر پست.", reply_markup=main_menu())
        return True

    return False


def _show_sched_confirm(chat_id):
    u = get_user(chat_id)
    u["state"] = "S_CONFIRM"
    tags, count = u.get("hashtags", []), u.get("count", 0)
    times = u.get("times", [])
    n_types = 2 if u.get("content_type") == "both" else 1
    n_posts = len(tags) * count * n_types
    if u.get("want_comments"):
        n_cm = u.get("top_n", 5) * u.get("comments_per", 10)
        cm_line = f"💬 کامنت: ✅ ({u.get('top_n')} پست × {u.get('comments_per')} = {n_cm})"
    else:
        n_cm, cm_line = 0, "💬 کامنت: ❌"
    per_run = estimate_cost(n_posts, n_cm)
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ ذخیره اسکژول", callback_data="schc_start"),
           InlineKeyboardButton("❌ انصراف", callback_data="schc_cancel"))
    bot.send_message(chat_id,
        "🧾 <b>قدم ۶ از ۶: تأیید اسکژول</b>\n\n"
        f"🕐 ساعت‌ها (تهران): <b>{'، '.join(times)}</b>\n"
        f"🏷️ هشتگ‌ها: <b>{'، '.join('#' + t for t in tags)}</b>\n"
        f"🔢 تعداد: <b>{count}</b> | 📦 <b>{TYPE_NAMES.get(u.get('content_type'))}</b>\n"
        f"{cm_line}\n"
        f"🤖 تحلیل AI: ✅ (انتخاب تک‌پست برتر + راهنما)\n\n"
        f"💰 هزینه هر اجرا: حدود <b>${per_run:.2f}</b> | روزی {len(times)} اجرا\n\nذخیره کنم؟",
        reply_markup=kb)


def on_callback(call, u) -> bool:
    chat_id, mid, data = call.message.chat.id, call.message.message_id, call.data

    # ----- ساخت: نوع/کامنت/تعداد -----
    if data.startswith("stype_"):
        u["content_type"] = data.replace("stype_", "")
        u["state"] = "S_CM_ASK"
        bot.edit_message_text(
            f"✅ نوع: <b>{TYPE_NAMES.get(u['content_type'])}</b>\n\n"
            f"💬 <b>قدم ۵ از ۶:</b> کامنت پست‌های برتر هم جمع بشه؟ (برای تحلیل AI بهتره ✅)",
            chat_id, mid, reply_markup=yes_no_inline("scm_yes", "scm_no",
                "✅ بله", "❌ نه"))
        return True

    if data == "scm_no":
        u["want_comments"] = False
        _show_sched_confirm(chat_id)
        return True

    if data == "scm_yes":
        u["want_comments"] = True
        u["state"] = "S_TOPN"
        bot.edit_message_text(
            "✅ کامنت: <b>بله</b>\n\n🏆 برای <b>چند پست برتر</b> کامنت بگیرم؟ (۱ تا ۲۰)\nدکمه بزن یا تایپ کن 👇",
            chat_id, mid, reply_markup=num_inline("stop", [3, 5, 10]))
        return True

    if data.startswith("stop_"):
        u["top_n"] = int(data.replace("stop_", ""))
        u["state"] = "S_CPC"
        bot.edit_message_text(
            f"✅ پست‌های برتر: <b>{u['top_n']}</b>\n\n💬 برای <b>هر پست</b> چند کامنت؟ (۱ تا ۵۰) 👇",
            chat_id, mid, reply_markup=num_inline("scpc", [5, 10, 20]))
        return True

    if data.startswith("scpc_"):
        u["comments_per"] = int(data.replace("scpc_", ""))
        _show_sched_confirm(chat_id)
        return True

    if data == "schc_cancel":
        reset_user(chat_id)
        bot.edit_message_text("❌ ساخت اسکژول لغو شد.", chat_id, mid)
        bot.send_message(chat_id, "منوی اصلی 👇", reply_markup=main_menu())
        return True

    if data == "schc_start":
        sid = database.create_schedule({
            "chat_id": chat_id, "times": u.get("times", []),
            "hashtags": u.get("hashtags", []), "count_per_hashtag": u.get("count", 20),
            "content_type": u.get("content_type", "both"),
            "want_comments": u.get("want_comments", False),
            "top_n": u.get("top_n", 5), "comments_per": u.get("comments_per", 10),
            "enabled": True,
        })
        reset_user(chat_id)
        bot.edit_message_text(f"✅ <b>اسکژول #{sid} ذخیره و فعال شد!</b> 🎉\n\nسر ساعت‌های مشخص (تهران) خودکار اجرا میشه و گزارش کامل + تحلیل AI میاد تو همین چت.",
                              chat_id, mid)
        show_detail(chat_id, mid, sid)
        return True

    # ----- هاب و جزئیات -----
    if data == "sch_new":
        bot.edit_message_text("⏰ ساخت اسکژول جدید...", chat_id, mid)
        start_creation(chat_id)
        return True

    if data == "sch_hub":
        render_hub(chat_id, mid)
        return True

    if data == "sch_trash":
        show_trash(chat_id, mid)
        return True

    if data.startswith("sch_detail_"):
        show_detail(chat_id, mid, int(data.replace("sch_detail_", "")))
        return True

    if data.startswith("sch_toggle_"):
        sid = int(data.replace("sch_toggle_", ""))
        s = database.get_schedule(sid)
        if s:
            database.set_enabled(sid, not s["enabled"])
        show_detail(chat_id, mid, sid)
        return True

    if data.startswith("sch_run_"):
        sid = int(data.replace("sch_run_", ""))
        bot.answer_callback_query(call.id, "▶️ اجرای دستی شروع شد!")
        threading.Thread(target=jobs.run_scheduled_job, args=(sid,), daemon=True).start()
        return True

    if data.startswith("sch_edit_"):
        show_edit_menu(chat_id, mid, int(data.replace("sch_edit_", "")))
        return True

    # ----- حذف (دو مرحله‌ای) -----
    if data.startswith("sch_delok_"):
        sid = int(data.replace("sch_delok_", ""))
        database.trash_schedule(sid)
        bot.edit_message_text(f"🗑️ اسکژول #{sid} رفت تو سطل آشغال.\n(از بخش سطل آشغال می‌تونی برگردونیش یا کامل حذفش کنی)",
                              chat_id, mid)
        return True

    if data.startswith("sch_del_"):
        sid = int(data.replace("sch_del_", ""))
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🗑️ بله، ببر تو سطل آشغال", callback_data=f"sch_delok_{sid}"),
               InlineKeyboardButton("🔙 بیخیال", callback_data=f"sch_detail_{sid}"))
        bot.edit_message_text(f"اسکژول #{sid} بره تو سطل آشغال؟", chat_id, mid, reply_markup=kb)
        return True

    # ----- سطل آشغال -----
    if data.startswith("sch_restore_"):
        sid = int(data.replace("sch_restore_", ""))
        database.restore_schedule(sid)
        bot.answer_callback_query(call.id, "↩️ برگردونده شد!")
        show_trash(chat_id, mid)
        return True

    if data.startswith("sch_purgeok_"):
        sid = int(data.replace("sch_purgeok_", ""))
        database.purge_schedule(sid)
        bot.answer_callback_query(call.id, "❌ کامل حذف شد!")
        show_trash(chat_id, mid)
        return True

    if data.startswith("sch_purge_"):
        sid = int(data.replace("sch_purge_", ""))
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("❌ بله، کامل حذفش کن", callback_data=f"sch_purgeok_{sid}"),
               InlineKeyboardButton("🔙 بیخیال", callback_data="sch_trash"))
        bot.edit_message_text(f"⚠️ اسکژول #{sid} <b>کامل و برای همیشه</b> حذف بشه؟", chat_id, mid, reply_markup=kb)
        return True

    if data == "sch_purgeall_ok":
        database.purge_all_trash(chat_id)
        show_trash(chat_id, mid)
        return True

    if data == "sch_purgeall":
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🧹 بله، سطل رو خالی کن", callback_data="sch_purgeall_ok"),
               InlineKeyboardButton("🔙 بیخیال", callback_data="sch_trash"))
        bot.edit_message_text("⚠️ همه سطل آشغال <b>کامل</b> خالی بشه؟", chat_id, mid, reply_markup=kb)
        return True

    # ----- ویرایش فیلدها -----
    if data.startswith("sche_times_"):
        sid = int(data.replace("sche_times_", ""))
        u["edit"] = {"id": sid}
        u["state"] = "E_TIMES"
        bot.edit_message_text(f"🕐 ساعت‌های جدید اسکژول #{sid} رو بفرست (تهران):\n<code>08:00, 12:30</code>",
                              chat_id, mid)
        bot.send_message(chat_id, "منتظر ساعت‌های جدیدم ⌨️", reply_markup=cancel_keyboard())
        return True

    if data.startswith("sche_tags_"):
        sid = int(data.replace("sche_tags_", ""))
        u["edit"] = {"id": sid}
        u["state"] = "E_HASHTAGS"
        bot.edit_message_text(f"🏷️ هشتگ‌های جدید اسکژول #{sid} رو بفرست:", chat_id, mid)
        return True

    if data.startswith("sche_count_"):
        sid = int(data.replace("sche_count_", ""))
        u["edit"] = {"id": sid}
        u["state"] = "E_COUNT"
        kb = InlineKeyboardMarkup()
        kb.add(*[InlineKeyboardButton(str(o), callback_data=f"schec_{sid}_{o}") for o in (10, 20, 50, 100)])
        bot.edit_message_text(f"🔢 تعداد جدید اسکژول #{sid}؟ (۱ تا ۲۰۰)\nدکمه بزن یا تایپ کن 👇",
                              chat_id, mid, reply_markup=kb)
        return True

    if data.startswith("schec_"):
        _, sid, n = data.split("_")
        database.update_schedule(int(sid), {"count_per_hashtag": int(n)})
        bot.answer_callback_query(call.id, "✅ ذخیره شد!")
        show_detail(chat_id, mid, int(sid))
        return True

    if data.startswith("schetype_"):
        rest = data.replace("schetype_", "")
        sid_s, t = rest.rsplit("_", 1)
        database.update_schedule(int(sid_s), {"content_type": t})
        bot.answer_callback_query(call.id, "✅ ذخیره شد!")
        show_detail(chat_id, mid, int(sid_s))
        return True

    if data.startswith("sche_type_"):
        sid = int(data.replace("sche_type_", ""))
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("📷 فقط پست", callback_data=f"schetype_{sid}_posts"),
               InlineKeyboardButton("🎬 فقط ریلز", callback_data=f"schetype_{sid}_reels"))
        kb.add(InlineKeyboardButton("📷+🎬 هر دو", callback_data=f"schetype_{sid}_both"))
        kb.add(InlineKeyboardButton("🔙 برگشت", callback_data=f"sch_edit_{sid}"))
        bot.edit_message_text(f"📦 نوع جدید اسکژول #{sid}؟", chat_id, mid, reply_markup=kb)
        return True

    if data == "schecm_no_" or data.startswith("schecm_no_"):
        sid = int(data.replace("schecm_no_", ""))
        database.update_schedule(sid, {"want_comments": False})
        bot.answer_callback_query(call.id, "✅ ذخیره شد!")
        show_detail(chat_id, mid, sid)
        return True

    if data.startswith("schecm_yes_"):
        sid = int(data.replace("schecm_yes_", ""))
        database.update_schedule(sid, {"want_comments": True})
        u["edit"] = {"id": sid}
        u["state"] = "E_TOPN"
        kb = InlineKeyboardMarkup()
        kb.add(*[InlineKeyboardButton(str(o), callback_data=f"schetop_{sid}_{o}") for o in (3, 5, 10)])
        bot.edit_message_text("🏆 برای چند پست برتر کامنت بگیرم؟ (۱ تا ۲۰) 👇", chat_id, mid, reply_markup=kb)
        return True

    if data.startswith("schetop_"):
        _, sid, n = data.split("_")
        database.update_schedule(int(sid), {"top_n": int(n)})
        u["edit"] = {"id": int(sid)}
        u["state"] = "E_CPC"
        kb = InlineKeyboardMarkup()
        kb.add(*[InlineKeyboardButton(str(o), callback_data=f"schecpc_{sid}_{o}") for o in (5, 10, 20)])
        bot.edit_message_text(f"✅ شد {n} پست برتر.\n\n💬 برای هر پست چند کامنت؟ (۱ تا ۵۰) 👇",
                              chat_id, mid, reply_markup=kb)
        return True

    if data.startswith("schecpc_"):
        _, sid, n = data.split("_")
        database.update_schedule(int(sid), {"comments_per": int(n)})
        bot.answer_callback_query(call.id, "✅ ذخیره شد!")
        show_detail(chat_id, mid, int(sid))
        return True

    if data.startswith("sche_cm_"):
        sid = int(data.replace("sche_cm_", ""))
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("✅ بله، کامنت بگیر", callback_data=f"schecm_yes_{sid}"),
               InlineKeyboardButton("❌ نه", callback_data=f"schecm_no_{sid}"))
        kb.add(InlineKeyboardButton("🔙 برگشت", callback_data=f"sch_edit_{sid}"))
        bot.edit_message_text(f"💬 کامنت‌گیری اسکژول #{sid}؟", chat_id, mid, reply_markup=kb)
        return True

    return False
