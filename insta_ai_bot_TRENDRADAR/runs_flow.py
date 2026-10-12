"""مرور ران‌های قبلی + دانلود/نمایش کامل خروجی‌شون"""
import csv
import io
import json
import threading
from datetime import datetime

from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

import apify_service
import jobs
from core import bot, fmt_time, get_user

TRACKED_ACTORS = ["apify/instagram-scraper", "apify/instagram-comment-scraper"]
ACTOR_SHORT = {"apify/instagram-scraper": "📷 پست/ریلز",
               "apify/instagram-comment-scraper": "💬 کامنت"}
MAX_RUNS_LIST = 10
MAX_DOWNLOAD_ITEMS = 2000
STATUS_EMOJI = {"SUCCEEDED": "✅", "RUNNING": "⏳", "READY": "🕐",
                "FAILED": "❌", "ABORTED": "🚫", "TIMED-OUT": "⏱️", "TIMING-OUT": "⏱️"}


def _rf(run, obj_attr, dict_key):
    if isinstance(run, dict):
        return run.get(dict_key)
    return getattr(run, obj_attr, None)


def send_list(message):
    wait = bot.send_message(message.chat.id, "⏳ در حال گرفتن لیست ران‌ها از Apify...")
    render_list(message.chat.id, wait.message_id)


def render_list(chat_id, message_id):
    try:
        client = apify_service.get_apify()
        all_runs = []
        for actor_id in TRACKED_ACTORS:
            try:
                page = client.actor(actor_id).runs().list(limit=MAX_RUNS_LIST, desc=True)
                items = page.items if hasattr(page, "items") else []
                for r in items:
                    all_runs.append((actor_id, r))
            except Exception as e:
                print(f"runs list error {actor_id}: {e}")
        if not all_runs:
            bot.edit_message_text("😕 هنوز رانی ثبت نشده.", chat_id, message_id)
            return
        all_runs.sort(key=lambda x: str(_rf(x[1], "started_at", "startedAt") or ""), reverse=True)
        all_runs = all_runs[:MAX_RUNS_LIST]
        u = get_user(chat_id)
        runs_map = {}
        lines = ["📜 <b>ران‌های قبلی</b> (۱۰ تای آخر، به وقت تهران)\n"]
        kb = InlineKeyboardMarkup()
        for actor_id, r in all_runs:
            rid = _rf(r, "id", "id")
            st = apify_service.status_of(r)
            tm = fmt_time(_rf(r, "started_at", "startedAt"))
            emoji = STATUS_EMOJI.get(st, "•")
            runs_map[rid] = actor_id
            short_actor = ACTOR_SHORT.get(actor_id, "🤖")
            lines.append(f"{emoji} {short_actor} | <code>{rid}</code>\n    🕐 {tm} | {st}")
            kb.add(InlineKeyboardButton(f"{emoji} {short_actor} | {tm}", callback_data=f"run_{rid}"))
        u["runs_map"] = runs_map
        lines.append("\n👆 روی یه ران بزن تا جزئیات و خروجی رو ببینی")
        bot.edit_message_text("\n".join(lines), chat_id, message_id, reply_markup=kb)
    except Exception as e:
        print(f"old runs error: {e}")
        bot.edit_message_text(f"❌ خطا در گرفتن لیست ران‌ها:\n<code>{str(e)[:300]}</code>", chat_id, message_id)


def get_run_input(client, run):
    try:
        kvs = _rf(run, "default_key_value_store_id", "defaultKeyValueStoreId")
        if not kvs:
            return None
        rec = client.key_value_store(kvs).get_record("INPUT")
        if isinstance(rec, dict) and "value" in rec and isinstance(rec["value"], dict):
            return rec["value"]
        return rec if isinstance(rec, dict) else None
    except Exception:
        return None


def summarize_input(inp):
    import re
    if not inp:
        return "—"
    parts = []
    try:
        if inp.get("directUrls"):
            urls = inp["directUrls"]
            tags = []
            for x in urls[:3]:
                m = re.search(r"/explore/tags/([^/]+)/?", x or "")
                tags.append("#" + (m.group(1) if m else (x or "")[:25]))
            parts.append(" ".join(tags) + (f" +{len(urls) - 3}" if len(urls) > 3 else ""))
        if inp.get("hashtags"):
            parts.append(" ".join("#" + h for h in inp["hashtags"][:5]))
        if inp.get("resultsType"):
            parts.append(f"({inp['resultsType']} x{inp.get('resultsLimit', '?')})")
        elif inp.get("resultsLimit"):
            parts.append(f"(x{inp['resultsLimit']})")
    except Exception:
        pass
    return " | ".join(parts) if parts else "—"


def item_count(client, ds_id):
    try:
        if not ds_id:
            return None
        ds = client.dataset(ds_id).get()
        if ds is None:
            return None
        return ds.item_count if hasattr(ds, "item_count") else ds.get("itemCount")
    except Exception:
        return None


def show_detail(chat_id, message_id, run_id):
    u = get_user(chat_id)
    actor_id = (u.get("runs_map") or {}).get(run_id, "apify/instagram-scraper")
    bot.edit_message_text("⏳ در حال گرفتن جزئیات ران...", chat_id, message_id)
    try:
        client = apify_service.get_apify()
        r = client.run(run_id).get()
        if r is None:
            bot.edit_message_text("❌ ران پیدا نشد!", chat_id, message_id)
            return
        st = apify_service.status_of(r)
        n = item_count(client, apify_service.dataset_id_of(r))
        text = (f"{STATUS_EMOJI.get(st, '•')} <b>جزئیات ران</b>\n\n"
                f"🤖 Actor: <b>{ACTOR_SHORT.get(actor_id, actor_id)}</b>\n"
                f"🆔 ID: <code>{run_id}</code>\n"
                f"📌 وضعیت: <b>{st}</b>\n"
                f"📥 ورودی: {summarize_input(get_run_input(client, r))}\n"
                f"📦 تعداد خروجی: <b>{n if n is not None else '؟'}</b>\n"
                f"🕐 شروع: {fmt_time(_rf(r, 'started_at', 'startedAt'))} | "
                f"پایان: {fmt_time(_rf(r, 'finished_at', 'finishedAt'))}\n")
        kb = InlineKeyboardMarkup()
        if st == "SUCCEEDED" and n:
            kb.add(InlineKeyboardButton("👁️ نمایش کامل خروجی (مثل اسکرپ تازه)", callback_data=f"full_{run_id}"))
            kb.add(InlineKeyboardButton("📥 فقط CSV", callback_data=f"dlc_{run_id}"),
                   InlineKeyboardButton("📦 فقط JSON", callback_data=f"dlj_{run_id}"))
        elif st == "RUNNING":
            text += "\n⏳ این ران هنوز در حال اجراست، بعداً برگرد."
        elif not n:
            text += "\n😕 این ران خروجی‌ای نداره."
        kb.add(InlineKeyboardButton("🔙 برگشت به لیست", callback_data="runs_back"))
        bot.edit_message_text(text, chat_id, message_id, reply_markup=kb)
    except Exception as e:
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔙 برگشت به لیست", callback_data="runs_back"))
        bot.edit_message_text(f"❌ خطا:\n<code>{str(e)[:300]}</code>", chat_id, message_id, reply_markup=kb)


def download_only(chat_id, run_id, fmt):
    try:
        status = bot.send_message(chat_id, "⏳ در حال دانلود خروجی ران...")
        client = apify_service.get_apify()
        r = client.run(run_id).get()
        ds_id = apify_service.dataset_id_of(r)
        total = item_count(client, ds_id) or 0
        items = list(client.dataset(ds_id).iterate_items(limit=MAX_DOWNLOAD_ITEMS))
        if not items:
            bot.edit_message_text("😕 این ران آیتمی نداره.", chat_id, status.message_id)
            return
        note = f"\n⚠️ فقط {len(items)} تای اول از {total}" if total > len(items) else ""
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        if fmt == "csv":
            buf = io.StringIO()
            first = items[0]
            if "caption" in first or "shortCode" in first:
                fields = ["url", "type", "likesCount", "commentsCount", "videoPlayCount",
                          "igPlayCount", "ownerUsername", "timestamp", "caption"]
            elif "text" in first:
                fields = ["postUrl", "inputUrl", "ownerUsername", "text", "likesCount", "timestamp"]
            else:
                fields = [k for k, v in first.items() if not isinstance(v, (dict, list))][:15]
            w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for it in items:
                row = {}
                for k in fields:
                    v = it.get(k, "")
                    row[k] = str(v).replace("\n", " ")[:800] if not isinstance(v, (dict, list)) else json.dumps(v, ensure_ascii=False)[:800]
                w.writerow(row)
            data = io.BytesIO(buf.getvalue().encode("utf-8-sig"))
            data.name = f"run_{run_id[:8]}_{stamp}.csv"
            bot.send_document(chat_id, data, caption=f"📥 <b>خروجی ران</b> <code>{run_id}</code>\n📦 {len(items)} ردیف{note}")
        else:
            data = io.BytesIO(json.dumps(items, ensure_ascii=False, indent=2).encode("utf-8"))
            data.name = f"run_{run_id[:8]}.json"
            bot.send_document(chat_id, data, caption=f"📦 <b>خروجی ران</b> <code>{run_id}</code>\n📦 {len(items)} آیتم{note}")
        bot.delete_message(chat_id, status.message_id)
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا در دانلود:\n<code>{str(e)[:300]}</code>")


def render_full(chat_id, run_id):
    """نمایش کامل خروجی ران قدیمی — دقیقاً مثل اسکرپ تازه"""
    try:
        status = bot.send_message(chat_id, "⏳ در حال آماده‌سازی خروجی ران...")
        client = apify_service.get_apify()
        r = client.run(run_id).get()
        if r is None:
            bot.edit_message_text("❌ ران پیدا نشد!", chat_id, status.message_id)
            return
        total = item_count(client, apify_service.dataset_id_of(r)) or 0
        items = list(client.dataset(apify_service.dataset_id_of(r)).iterate_items(limit=MAX_DOWNLOAD_ITEMS))
        if not items:
            bot.edit_message_text("😕 این ران آیتمی نداره.", chat_id, status.message_id)
            return
        first = items[0]
        trunc = f"\n⚠️ فقط {len(items)} تای اول از {total}" if total > len(items) else ""
        title = f"🆔 ران: <code>{run_id}</code>\n📥 ورودی: {summarize_input(get_run_input(client, r))}{trunc}"
        if "caption" in first or "shortCode" in first:
            jobs.render_posts_full(chat_id, status.message_id, items, title_line=title,
                                   file_prefix=f"run_{run_id[:8]}", header="✅ <b>خروجی ران</b> 🎉")
        elif "text" in first:
            bot.edit_message_text(f"✅ <b>کامنت‌های ران</b> 💬\n\n{title}\n📦 تعداد: <b>{len(items)}</b>",
                                  chat_id, status.message_id)
            jobs.render_comments_full(chat_id, status.message_id, items, file_prefix=f"run_{run_id[:8]}_comments")
        else:
            data = io.BytesIO(json.dumps(items, ensure_ascii=False, indent=2).encode("utf-8"))
            data.name = f"run_{run_id[:8]}.json"
            bot.edit_message_text(f"✅ <b>خروجی ران</b>\n\n{title}\n📦 تعداد: <b>{len(items)}</b>",
                                  chat_id, status.message_id)
            bot.send_document(chat_id, data, caption=f"📦 خروجی ران <code>{run_id}</code>")
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا در نمایش خروجی:\n<code>{str(e)[:300]}</code>")


def send_last_output(message):
    wait = bot.send_message(message.chat.id, "⏳ در حال پیدا کردن آخرین ران موفق...")
    try:
        client = apify_service.get_apify()
        best = None
        for actor_id in TRACKED_ACTORS:
            try:
                page = client.actor(actor_id).runs().list(limit=5, desc=True)
                items = page.items if hasattr(page, "items") else []
                for r in items:
                    if apify_service.status_of(r) == "SUCCEEDED":
                        ts = str(_rf(r, "started_at", "startedAt") or "")
                        if best is None or ts > best[0]:
                            best = (ts, _rf(r, "id", "id"))
                        break
            except Exception as e:
                print(f"last run search error: {e}")
        if best is None:
            bot.edit_message_text("😕 ران موفقی پیدا نشد.", message.chat.id, wait.message_id)
            return
        bot.delete_message(message.chat.id, wait.message_id)
        render_full(message.chat.id, best[1])
    except Exception as e:
        bot.edit_message_text(f"❌ خطا:\n<code>{str(e)[:300]}</code>", message.chat.id, wait.message_id)


def on_callback(call) -> bool:
    chat_id, mid, data = call.message.chat.id, call.message.message_id, call.data
    if data == "runs_back":
        bot.edit_message_text("⏳ در حال گرفتن لیست ران‌ها...", chat_id, mid)
        render_list(chat_id, mid)
        return True
    if data.startswith("run_"):
        show_detail(chat_id, mid, data[4:])
        return True
    if data.startswith("dlc_") or data.startswith("dlj_"):
        fmt = "csv" if data.startswith("dlc_") else "json"
        bot.answer_callback_query(call.id, "📥 دانلود شروع شد!")
        threading.Thread(target=download_only, args=(chat_id, data[4:], fmt), daemon=True).start()
        return True
    if data.startswith("full_"):
        bot.answer_callback_query(call.id, "👁️ در حال آماده‌سازی...")
        threading.Thread(target=render_full, args=(chat_id, data[5:]), daemon=True).start()
        return True
    return False
