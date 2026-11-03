"""موتور اجراها: نمایش نتایج + پایپ‌لاین اسکرپ فوری + جاب اسکژول‌شده + حلقه اسکژولر"""
import csv
import html
import io
import json
import threading
import time
from datetime import datetime

from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

import ai_service
import apify_service
import database
import scoring
import search_service
import pinterest_service
import unified_ranking
import scheduled_report
import instagram_media
from core import bot, fmt_num, main_menu, reset_user, send_long, tehran_now, to_hashtag_urls

TYPE_NAMES = {"posts": "📷 پست", "reels": "🎬 ریلز", "both": "📷+🎬 پست و ریلز"}


# ============================================================
# 🎨 نمایش نتایج (مشترک بین همه فلوها)
# ============================================================
def build_posts_summary(items):
    out = []
    for m in items:
        out.append({
            "url": m.get("url") or "",
            "shortCode": m.get("shortCode") or "",
            "type": m.get("type") or "",
            "caption": (m.get("caption") or "").replace("\n", " ")[:300],
            "likes": m.get("likesCount") or 0,
            "comments_count": m.get("commentsCount") or 0,
            "views": m.get("videoPlayCount") or m.get("igPlayCount") or m.get("videoViewCount") or "",
            "timestamp": m.get("timestamp") or "",
            "owner": m.get("ownerUsername") or "",
            "displayUrl": m.get("displayUrl") or "",
            "viral_score": m.get("viral_score"),
        })
    return sorted(out, key=lambda x: (x["viral_score"] if x["viral_score"] is not None else -1,
                                      x["likes"] or 0), reverse=True)


def render_posts_full(chat_id, status_msg_id, items, title_line, file_prefix, header="✅ <b>نتیجه!</b> 🎉"):
    summary = build_posts_summary(items)
    total_likes = sum(x["likes"] or 0 for x in summary)
    avg_likes = total_likes // len(summary) if summary else 0
    best = summary[0]
    stamp = datetime.now().strftime("%Y%m%d_%H%M")

    bot.edit_message_text(
        f"{header}\n\n{title_line}\n"
        f"📦 تعداد نتایج: <b>{len(summary)}</b>\n"
        f"❤️ مجموع لایک: <b>{fmt_num(total_likes)}</b> | میانگین: <b>{fmt_num(avg_likes)}</b>\n"
        f"🏆 بهترین پست: <b>@{best['owner']}</b> با <b>{fmt_num(best['likes'])}</b> لایک\n\n"
        f"📁 دارم فایل‌ها رو می‌فرستم...",
        chat_id, status_msg_id)

    buf = io.StringIO()
    fields = ["url", "type", "likes", "comments_count", "views", "owner", "timestamp", "caption"]
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    for x in summary:
        w.writerow({k: x.get(k, "") for k in fields})
    data = io.BytesIO(buf.getvalue().encode("utf-8-sig"))
    data.name = f"{file_prefix}_{stamp}.csv"
    bot.send_document(chat_id, data,
        caption=f"📊 <b>فایل CSV</b> - {len(summary)} نتیجه\n{title_line}")

    data = io.BytesIO(json.dumps(items, ensure_ascii=False, indent=2).encode("utf-8"))
    data.name = f"{file_prefix}_full_{stamp}.json"
    bot.send_document(chat_id, data, caption="📦 <b>فایل JSON کامل</b> (همه فیلدها)")

    bot.send_message(chat_id, "🏆 <b>۵ پست برتر</b>:")
    for i, x in enumerate(summary[:5], 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}️⃣"
        score_line = f"🔥 وایرال: {x['viral_score']}" if x["viral_score"] is not None else ""
        views_line = f"👁️ {fmt_num(x['views'])} | " if x["views"] else ""
        caption = (f"{medal} <b>@{x['owner']}</b> {score_line}\n"
                   f"❤️ {fmt_num(x['likes'])} | 💬 {fmt_num(x['comments_count'])} | {views_line}"
                   f"📝 {(x['caption'] or '')[:150]}")
        kb = InlineKeyboardMarkup()
        if x["url"]:
            kb.add(InlineKeyboardButton("🔗 مشاهده در اینستاگرام", url=x["url"]))
        try:
            if x["displayUrl"]:
                bot.send_photo(chat_id, x["displayUrl"], caption=caption, reply_markup=kb)
            else:
                bot.send_message(chat_id, caption, reply_markup=kb)
        except Exception:
            bot.send_message(chat_id, caption, reply_markup=kb)
    return summary


def render_comments_full(chat_id, status_msg_id, items, title_line="", file_prefix="comments", delete_status=False):
    if delete_status:
        try:
            bot.delete_message(chat_id, status_msg_id)
        except Exception:
            pass
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    buf = io.StringIO()
    fields = ["postUrl", "ownerUsername", "text", "likesCount", "timestamp"]
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    for c in items:
        w.writerow({
            "postUrl": c.get("postUrl") or c.get("inputUrl") or "",
            "ownerUsername": c.get("ownerUsername") or "",
            "text": (c.get("text") or "").replace("\n", " ")[:500],
            "likesCount": c.get("likesCount") or 0,
            "timestamp": c.get("timestamp") or "",
        })
    data = io.BytesIO(buf.getvalue().encode("utf-8-sig"))
    data.name = f"{file_prefix}_{stamp}.csv"
    cap = f"💬 <b>فایل کامنت‌ها</b> - {len(items)} کامنت" + (f"\n{title_line}" if title_line else "")
    bot.send_document(chat_id, data, caption=cap)
    sample = "\n\n".join(
        f"👤 <b>@{c.get('ownerUsername', '?')}</b>: {(c.get('text') or '')[:120]}"
        for c in items[:5])
    bot.send_message(chat_id, f"💬 <b>نمونه کامنت‌ها:</b>\n\n{sample}")


def _match_post(top_posts, comment):
    key = apify_service.post_key_of_comment(comment)
    if not key:
        return None
    for p in top_posts:
        url, sc = p.get("url") or "", p.get("shortCode") or ""
        if (url and (key == url or key in url or url in key)) or (sc and sc in key):
            return url or sc
    return None


def group_comments_by_post(top_posts, comments):
    grouped = {(p.get("url") or p.get("shortCode")): [] for p in top_posts}
    for c in comments:
        k = _match_post(top_posts, c)
        if k and k in grouped:
            grouped[k].append(c)
    return grouped


def send_top_posts_with_comments(chat_id, ranked, grouped, max_posts=10, max_cm=5):
    """هر پست برتر + کامنت‌های خودش زیرش"""
    shown = ranked[:max_posts]
    bot.send_message(chat_id, f"📌 <b>پست‌های برتر با کامنت‌هاشون</b> ({len(shown)} پست):")
    for i, p in enumerate(shown, 1):
        key = p.get("url") or p.get("shortCode")
        cmts = grouped.get(key, [])
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}️⃣"
        txt = (f"{medal} <b>@{p.get('ownerUsername', '?')}</b> | 🔥 {p.get('viral_score', '?')}\n"
               f"❤️ {fmt_num(p.get('likesCount'))} | 💬 {fmt_num(p.get('commentsCount'))} | "
               f"🔗 {p.get('url', '')}\n"
               f"📝 {(p.get('caption') or '')[:120]}")
        if cmts:
            txt += f"\n\n💬 <b>کامنت‌ها ({len(cmts)}):</b>\n"
            for c in cmts[:max_cm]:
                txt += f"  👤 <b>@{c.get('ownerUsername', '?')}</b>: {(c.get('text') or '')[:110]}\n"
            if len(cmts) > max_cm:
                txt += f"  <i>... و {len(cmts) - max_cm} تای دیگه تو فایل CSV</i>"
        else:
            txt += "\n\n💬 <i>کامنتی گرفته نشد.</i>"
        bot.send_message(chat_id, txt)


def fa_age(age_hours):
    try:
        h = float(age_hours)
    except Exception:
        return ""
    if h < 1:
        return "کمتر از یک ساعت پیش"
    if h < 24:
        return f"{int(h)} ساعت پیش"
    return f"{int(h // 24)} روز پیش"


def send_telegram_summary(chat_id, report=None, error=""):
    """نمایش فشرده‌ی نتیجه‌ی جست‌وجوی Telegram؛ هر خطای نمایش ایزوله می‌ماند."""
    lines = ["📡 <b>نتیجه جست‌وجوی Telegram</b>"]
    if report is None:
        lines.append(f"⚠️ اجرا نشد: <code>{html.escape(error or 'خطای نامشخص')}</code>")
    else:
        if report.signals:
            lines.append(f"📈 {len(report.signals)} سیگنال از {report.posts_collected} پست جمع‌آوری‌شده:")
            for signal in report.signals[:5]:
                lines.append(
                    f"• <b>#{html.escape(signal.tag)}</b> — امتیاز {signal.score:.1f}/100"
                )
                if signal.reason:
                    lines.append(f"  {html.escape(signal.reason[:220])}")
        else:
            lines.append(f"ℹ️ سیگنال قابل‌رتبه‌بندی پیدا نشد ({report.posts_collected} پست بررسی شد).")
        if report.errors:
            short_errors = "; ".join(str(item)[:120] for item in report.errors[:2])
            lines.append(f"⚠️ خطای جزئی: <code>{html.escape(short_errors)}</code>")
    try:
        bot.send_message(chat_id, "\n".join(lines), disable_web_page_preview=True)
    except Exception as exc:
        print(f"TELEGRAM SUMMARY ERROR: {exc}")


def send_unified_results(chat_id, instagram, telegram_report, pinterest, pinterest_errors):
    """Show an explicitly relative shortlist and attach all candidates as JSON."""
    try:
        ranking = unified_ranking.build_unified_ranking(instagram, telegram_report, pinterest)
        lines = ["🏆 <b>رتبه‌بندی مشترک سه پلتفرم</b>",
                 "امتیاز نسبی بر اساس رتبه داخل هر پلتفرم است؛ مقایسه قطعی ترند یا تعامل واقعی نیست.",
                 "Telegram فقط پست‌های برتر سیگنال‌های قابل‌تحلیل را شامل می‌شود."]
        for i, item in enumerate(ranking[:12], 1):
            title = html.escape(item["title"].replace("\n", " ")[:100])
            url = item["url"]
            link = f' — <a href="{html.escape(url, quote=True)}">مشاهده</a>' if url.startswith(("https://", "http://")) else ""
            lines.append(f'{i}. <b>{item["platform"]}</b> | {item["relative_score"]:.0f}/100 | {title}{link}')
        if not ranking:
            lines.append("نتیجه قابل رتبه‌بندی پیدا نشد.")
        if pinterest_errors:
            lines.append("⚠️ Pinterest: " + html.escape("؛ ".join(pinterest_errors[:3])[:500]))
        bot.send_message(chat_id, "\n".join(lines), disable_web_page_preview=True)
        if ranking:
            data = io.BytesIO(json.dumps({"ranking": ranking, "pinterest": pinterest,
                                         "pinterest_errors": pinterest_errors}, ensure_ascii=False, indent=2).encode("utf-8"))
            data.name = "unified_trends.json"
            bot.send_document(chat_id, data, caption="📦 رتبه‌بندی مشترک و جزئیات پینترست")
    except Exception as exc:
        print(f"UNIFIED REPORT ERROR: {exc}")


# ============================================================
# ⚡ پایپ‌لاین اسکرپ فوری
# ============================================================
def run_pipeline(chat_id, cfg):
    try:
        hashtags = cfg.get("hashtags", [])
        count = cfg.get("count", 20)
        ctype = cfg.get("content_type", "both")
        want_cm = cfg.get("want_comments", False)
        urls = to_hashtag_urls(hashtags)
        tag_list = "، ".join(f"#{t}" for t in hashtags)

        status_msg = bot.send_message(chat_id, f"⏳ اتصال به Apify...\n🏷️ {tag_list}")
        all_media = []
        if ctype in ("posts", "both"):
            bot.edit_message_text(f"⏳ در حال گرفتن <b>پست‌ها</b>... 📷\n🏷️ {tag_list}",
                                  chat_id, status_msg.message_id)
            all_media.extend(apify_service.scrape_media(urls, "posts", count))
        if ctype in ("reels", "both"):
            bot.edit_message_text(f"⏳ در حال گرفتن <b>ریلزها</b>... 🎬\n🏷️ {tag_list}",
                                  chat_id, status_msg.message_id)
            all_media.extend(apify_service.scrape_media(urls, "reels", count))
        if not all_media:
            bot.edit_message_text("😕 چیزی پیدا نشد! هشتگ رو چک کن و دوباره تلاش کن.",
                                  chat_id, status_msg.message_id)
            bot.send_message(chat_id, "منوی اصلی 👇", reply_markup=main_menu())
            reset_user(chat_id)
            return

        summary = render_posts_full(chat_id, status_msg.message_id, all_media,
            title_line=f"🏷️ هشتگ: {tag_list}", file_prefix="hashtags", header="✅ <b>تمام شد!</b> 🎉")

        if want_cm:
            top_n = cfg.get("top_n", 5)
            cpc = cfg.get("comments_per", 10)
            top_urls = apify_service.valid_comment_urls(summary[:top_n])
            if top_urls:
                cm_status = bot.send_message(chat_id, f"💬 در حال گرفتن کامنت‌ها... ({len(top_urls)} پست × {cpc})")
                try:
                    comments = apify_service.scrape_comments(top_urls, cpc)
                except Exception as e:
                    print(f"COMMENTS ERROR (instant): {e} | urls={top_urls[:3]}")
                    bot.edit_message_text(f"⚠️ کامنت‌گیری خطا داد و رد شد:\n<code>{str(e)[:200]}</code>",
                                          chat_id, cm_status.message_id)
                    comments = []
                if comments:
                    render_comments_full(chat_id, cm_status.message_id, comments,
                        title_line=f"🏷️ {tag_list}", file_prefix="comments", delete_status=True)
                else:
                    bot.edit_message_text("😕 کامنتی پیدا نشد (شاید کامنت‌ها بسته باشن).",
                                          chat_id, cm_status.message_id)
        bot.send_message(chat_id, "🎉 تموم شد! 👇", reply_markup=main_menu())
        reset_user(chat_id)
    except Exception as e:
        print(f"PIPELINE ERROR: {e}")
        try:
            bot.send_message(chat_id,
                f"❌ <b>خطا!</b>\n\n<code>{str(e)[:400]}</code>\n\n"
                f"احتمال‌ها: توکن Apify اشتباهه / کردیت تموم شده / مشکل اینترنت",
                reply_markup=main_menu())
        except Exception:
            pass
        reset_user(chat_id)


# ============================================================
# ⏰ جاب اسکژول‌شده: اسکرپ + وایرال‌اسکور + کامنت + تحلیل AI
# ============================================================
def run_scheduled_job(schedule_id):
    """One scheduled run: search, deduplicate, analyze only eligible posts, report."""
    sched = database.get_schedule(schedule_id)
    if not sched or sched["trashed"]:
        return
    chat_id = sched["chat_id"]
    terms = [ai_service.normalize_search_keyword(t) for t in sched.get("hashtags", [])]
    terms = list(dict.fromkeys(t for t in terms if t))
    warnings = []
    status_msg = None
    started = time.monotonic()
    run_id = f"schedule-{schedule_id}-{int(time.time())}"
    progress_state = {"percent": 0, "stage": "شروع"}
    progress_lock = threading.Lock()
    heartbeat_stop = threading.Event()
    heartbeat = None

    def progress(percent, stage):
        if status_msg is None or heartbeat_stop.is_set():
            return
        try:
            percent = max(0, min(100, int(percent)))
            with progress_lock:
                progress_state.update(percent=percent, stage=stage)
            bar = "▰" * (percent // 10) + "▱" * (10 - percent // 10)
            elapsed = int(time.monotonic() - started)
            bot.edit_message_text(
                f"⏳ <b>اسکژول #{schedule_id}</b>\n{bar} {percent}٪\n"
                f"{html.escape(stage)}\nزمان سپری‌شده: {elapsed} ثانیه\n"
                "<i>درصد تقریبی مرحله‌هاست؛ زمان پایان به سرویس‌ها بستگی دارد.</i>",
                chat_id, status_msg.message_id)
        except Exception as exc:
            if "message is not modified" not in str(exc).lower():
                print(f"PROGRESS UPDATE: {type(exc).__name__}")

    try:
        status_msg = bot.send_message(chat_id, f"⏳ شروع اسکژول #{schedule_id}…")
        def heartbeat_loop():
            while not heartbeat_stop.wait(20):
                with progress_lock:
                    percent, stage = progress_state["percent"], progress_state["stage"]
                progress(percent, stage)
        heartbeat = threading.Thread(target=heartbeat_loop, daemon=True)
        heartbeat.start()
        progress(5, "در حال گسترش موضوع و بررسی عبارت‌های قابل تلاش مجدد")
        topic = sched.get("topic") or "، ".join(sched.get("hashtags") or [])
        topic_key_value = database.topic_key(topic)
        try:
            terms, _fresh, error = search_service.generate_topic_search_terms(sched, max_keywords=5)
            if error:
                warnings.append(f"پیشنهاد AI: {error}")
        except Exception as exc:
            terms = []
            warnings.append(f"پیشنهاد AI: {type(exc).__name__}")
        if not terms:
            raise ValueError("عبارت تازه یا قابل تلاش مجدد برای جست‌وجو وجود ندارد")
        ig_terms = database.topic_terms_for_platform(chat_id, topic_key_value, "instagram", terms)
        tg_terms = database.topic_terms_for_platform(chat_id, topic_key_value, "telegram", terms)
        pin_terms = database.topic_terms_for_platform(chat_id, topic_key_value, "pinterest", terms)
        media = []
        kinds = [k for k in ("posts", "reels") if sched.get("content_type") in (k, "both")]
        ig_success = False
        if ig_terms and kinds:
            urls = to_hashtag_urls(ig_terms)
            for i, kind in enumerate(kinds):
                progress(15 + i * 12, f"دریافت {kind} از Instagram ({i+1}/{len(kinds)})")
                try:
                    media.extend(apify_service.scrape_media(urls, kind, sched["count_per_hashtag"]))
                    ig_success = True
                except Exception as exc:
                    warnings.append(f"Instagram {kind}: {type(exc).__name__}")
            database.record_keyword_platform_result(chat_id, topic_key_value, ig_terms, "instagram",
                                                    "succeeded" if ig_success else "failed",
                                                    "" if ig_success else "all requested kinds failed", run_id)
        elif ig_terms:
            warnings.append("Instagram: نوع محتوای معتبر تنظیم نشده است.")
            database.record_keyword_platform_result(chat_id, topic_key_value, ig_terms, "instagram", "failed", "invalid content_type", run_id)
        else:
            warnings.append("Instagram: عبارت جدید یا قابل تلاش مجدد وجود نداشت؛ جست‌وجوی تکراری انجام نشد.")
        seen, unique_media = set(), []
        for item in media:
            identity = scheduled_report.record_identity({"platform": "Instagram", "url": item.get("url"), "raw": item})
            key = identity["key"] if identity else None
            if key and key in seen:
                continue
            if key:
                seen.add(key)
                item = dict(item)
                item["url"] = identity["url"]
            unique_media.append(item)
        valid_posts = [item for item in unique_media if scheduled_report.valid_post_url({"platform": "Instagram", "url": item.get("url"), "raw": item})]
        if len(valid_posts) != len(unique_media):
            warnings.append(f"Instagram: {len(unique_media) - len(valid_posts)} نتیجهٔ فاقد لینک معتبر پست کنار گذاشته شد.")
        ranked = scoring.compute_virality(valid_posts)
        progress(40, "کشف کانال، عضویت مجاز و جست‌وجوی هشتگ/متن Telegram")
        telegram_report = None
        if tg_terms:
            try:
                telegram_report, telegram_error = search_service.scan_telegram_terms(
                    tg_terms, auto_join=bool(sched.get("telegram_auto_join")), schedule_id=schedule_id,
                    progress=lambda message: progress(40, message))
            except Exception as exc:
                telegram_report, telegram_error = None, type(exc).__name__
            if telegram_error:
                warnings.append(f"Telegram: {telegram_error}")
            if telegram_report:
                warnings.extend(f"Telegram: {e}" for e in telegram_report.errors)
                for term in tg_terms:
                    status = getattr(telegram_report, "term_results", {}).get(term, "failed")
                    database.record_keyword_platform_result(chat_id, topic_key_value, [term], "telegram",
                                                            "succeeded" if status == "succeeded" else "failed",
                                                            "" if status == "succeeded" else status, run_id)
            else:
                database.record_keyword_platform_result(chat_id, topic_key_value, tg_terms, "telegram", "failed", telegram_error, run_id)
        else:
            warnings.append("Telegram: عبارت جدید یا قابل تلاش مجدد وجود نداشت؛ جست‌وجوی تکراری انجام نشد.")
        progress(55, "ترجمه و جست‌وجوی Pinterest")
        pins, pin_audit = [], {"queries": [], "term_results": {}}
        if pin_terms:
            try:
                pins, pin_errors, pin_audit = pinterest_service.scan_pinterest_terms(pin_terms, topic=topic, return_queries=True)
                warnings.extend(f"Pinterest: {e}" for e in pin_errors)
            except Exception as exc:
                pins = []
                warnings.append(f"Pinterest: {type(exc).__name__}")
                pin_audit["term_results"] = {term: "failed" for term in pin_terms}
            for term in pin_terms:
                status = pin_audit.get("term_results", {}).get(term, "failed")
                if status in ("succeeded", "failed"):
                    database.record_keyword_platform_result(chat_id, topic_key_value, [term], "pinterest", status,
                                                            "" if status == "succeeded" else "search/translation failed", run_id)
        else:
            warnings.append("Pinterest: عبارت جدید یا قابل تلاش مجدد وجود نداشت؛ جست‌وجوی تکراری انجام نشد.")
        grouped, comments = {}, []
        if sched.get("want_comments") and ranked:
            progress(65, "دریافت نمونه کامنت پست‌های برتر Instagram")
            top = ranked[:sched.get("top_n", 5)]
            try:
                comment_urls = apify_service.valid_comment_urls(top)
                if comment_urls:
                    comments = apify_service.scrape_comments(comment_urls, sched.get("comments_per", 10))
                    grouped = group_comments_by_post(top, comments)
            except Exception as exc:
                warnings.append(f"کامنت‌ها: {type(exc).__name__}")
        records = scheduled_report.build_records(ranked, pins, telegram_report, grouped)
        records, run_duplicates = scheduled_report.dedupe_records(records)
        progress(70, f"حذف تکراری‌ها و آماده‌سازی تحلیل {len(records)} نتیجه")
        new_records, previous_skipped = database.reserve_post_records(chat_id, topic_key_value, records, run_id)
        analysis = scheduled_report.analyze_all(
            new_records, terms, topic=topic,
            progress=lambda i, n: progress(70 + int(15 * (i - 1) / max(n, 1)), f"تحلیل هوشمند دسته {i} از {n}"))
        warnings.extend(analysis["errors"])
        # Server-side guard: text-only AI cannot classify captionless media as relevant.
        for item in new_records:
            if item.get("media_type") in ("photo", "video", "image", "sidecar") and not item.get("text_available", True):
                if item["id"] in analysis.get("relevance", {}):
                    analysis["relevance"][item["id"]] = None
                    detail = analysis.setdefault("analysis_details", {}).setdefault(item["id"], {})
                    limits = detail.setdefault("data_limits", [])
                    note = "متن کافی وجود ندارد و تحلیل بصری انجام نشده است."
                    if note not in limits:
                        limits.append(note)
        database.complete_post_records(chat_id, topic_key_value, new_records, analysis, run_id)
        relevant_all = [r for r in new_records if analysis["relevance"].get(r["id"]) is True]
        unified = scheduled_report.rank_records(relevant_all)
        progress(92, "تهیه نتیجه و فایل‌های جامع")
        by_id = {r["id"]: r for r in new_records}
        ai_winner = by_id.get(analysis.get("best_id"))
        if ai_winner not in relevant_all:
            ai_winner = None
        viral_winner = next((r for r in unified if r["platform"] == "Instagram"), None)
        unknown = [r for r in new_records if r["id"] in analysis.get("relevance", {}) and analysis["relevance"][r["id"]] is None]
        unanalyzed = [r for r in new_records if r["id"] not in analysis.get("relevance", {})]
        unrelated_count = sum(analysis["relevance"].get(r["id"]) is False for r in new_records)
        failed_analysis = len(new_records) - analysis["analyzed"]
        translated = sum(q["status"] == "translated" for q in pin_audit.get("queries", []))
        lines = [f"✅ <b>نتیجه اسکژول #{schedule_id}</b>",
                 "━━━━━━━━━━━━━━━━━━", f"🔎 عبارت‌ها: {html.escape('، '.join(terms))}",
                 f"📊 نتایج خام: Instagram {len(ranked)} | Pinterest {len(pins)} | Telegram {getattr(telegram_report, 'posts_collected', 0)}",
                 f"🔁 تکراری همین اجرا: {run_duplicates} | قبلاً پردازش‌شده: {len(previous_skipped)}",
                 f"🆕 تحلیل جدید: {analysis['analyzed']} از {len(new_records)} | مرتبط: {len(relevant_all)} | نامرتبط: {unrelated_count} | نامشخص: {len(unknown)} | تحلیل‌نشده: {failed_analysis}",
                 f"🌐 ترجمه Pinterest: {translated} عبارت"]
        if ai_winner:
            lines += [f"🏆 <b>انتخاب AI</b> — {html.escape(ai_winner['platform'])}",
                      html.escape(ai_winner['title'][:180] or 'بدون عنوان')]
            if ai_winner.get("url"):
                lines.append(html.escape(ai_winner["url"]))
            if analysis.get("summary"):
                lines.append(html.escape(analysis["summary"][:300]))
        else:
            lines.append("⚠️ انتخاب نهایی AI در دسترس نیست؛ جزئیات در فایل گزارش آمده است.")
        def line(item, position):
            title = html.escape((item.get("title") or "بدون عنوان")[:85])
            score = item.get("relative_score")
            metric = item.get("viral_score") if item["platform"] == "Instagram" else item.get("platform_score") if item["platform"] == "Pinterest" else item.get("views")
            metric_label = {"Instagram": "وایرال", "Pinterest": "امتیاز پین", "Telegram": "بازدید"}[item["platform"]]
            link = f'<a href="{html.escape(item["url"], quote=True)}">مشاهده پست</a>' if item.get("url") else "لینک مستقیم موجود نیست"
            reason = html.escape((analysis.get("analysis_details", {}).get(item["id"], {}).get("reason") or "")[:140])
            return (f"{position}. {html.escape(item['platform'])} | رتبه نسبی {score} | {metric_label}: {html.escape(str(metric if metric is not None else 'نامشخص'))}\n"
                    f"{title} — {link}" + (f"\n💡 {reason}" if reason else ""))
        lines += ["", "🏆 <b>سه نتیجهٔ برتر مشترک (رتبهٔ نسبی)</b>"]
        if unknown or failed_analysis:
            lines.append("⚠️ رتبه‌بندی موقت است؛ موارد نامشخص/تحلیل‌نشده در آن حضور ندارند.")
        lines.extend(line(item, i) for i, item in enumerate(unified[:3], 1))
        if not unified:
            lines.append("نتیجهٔ مرتبط جدید برای رتبه‌بندی موجود نیست.")
        for platform in ("Instagram", "Pinterest", "Telegram"):
            items = [r for r in unified if r["platform"] == platform][:3]
            lines += ["", f"📌 <b>برترین‌های {platform}</b>"]
            lines.extend(line(item, i) for i, item in enumerate(items, 1))
            if not items:
                lines.append("نتیجهٔ مرتبط جدید در دسترس نیست.")
        lines.append("<i>رتبه‌ها نسبی درون هر پلتفرم‌اند. AI فقط متن و آمار را بررسی می‌کند، نه محتوای تصویر/ویدئو.</i>")
        if warnings:
            lines += ["", "⚠️ <b>محدودیت‌ها و خطاها</b>", *["• " + html.escape(str(w)[:160]) for w in warnings[:3]]]
        # Trim by complete lines so HTML tags/entities are never cut in half.
        message, used = "", 0
        for line in lines:
            if used + len(line) + 1 > 3800:
                message += "\n… ادامه و تحلیل کامل در فایل JSON"
                break
            message += line + "\n"
            used += len(line) + 1
        message = message.rstrip()
        heartbeat_stop.set()
        if heartbeat:
            heartbeat.join(timeout=5)
        bot.edit_message_text(message, chat_id, status_msg.message_id, disable_web_page_preview=True)
        progress(95, "ارسال رسانه‌های برتر")
        ig_media = [r for r in unified if r["platform"] == "Instagram"][:3]
        ig_delivery = instagram_media.send_instagram_media(bot, chat_id, ig_media, analysis)
        for delivery in ig_delivery:
            if delivery["status"] != "sent":
                warnings.append(f"رسانه Instagram #{delivery['rank']}: {'؛ '.join(delivery['details'])}")
        unknown_tg_media = [r for r in unknown if r["platform"] == "Telegram" and r.get("media_type") in ("photo", "video")]
        tg_media = [r for r in unified if r["platform"] == "Telegram" and r.get("media_type") in ("photo", "video")]
        tg_media.extend(sorted(unknown_tg_media, key=lambda r: -(r.get("views") or 0)))
        tg_delivery = []
        for index, item in enumerate(tg_media[:3], 1):
            flag = analysis["relevance"].get(item["id"])
            status = "مرتبط بر اساس متن" if flag is True else "ارتباط نامشخص؛ بررسی دستی لازم است"
            caption = (f"📡 <b>رسانهٔ Telegram #{index}</b> | {html.escape(status)}\n"
                       f"{html.escape(str(item.get('channel_title') or 'کانال نامشخص')[:100])}\n"
                       f"{html.escape(str(item.get('title') or 'بدون متن')[:300])}")
            if item.get("url"):
                caption += f"\n🔗 {html.escape(item['url'])}"
            delivery = {"id": item["id"], "rank": index, "status": "sent"}
            try:
                content, error = search_service.fetch_telegram_media(item)
                if error:
                    delivery.update(status="unavailable", detail=error)
                    bot.send_message(chat_id, caption + "\n⚠️ رسانه قابل ارسال نیست: " + html.escape(error))
                else:
                    data = io.BytesIO(content)
                    data.name = f"telegram_{index}.{'jpg' if item['media_type'] == 'photo' else 'mp4'}"
                    if item["media_type"] == "photo":
                        bot.send_photo(chat_id, data, caption=caption)
                    else:
                        bot.send_video(chat_id, data, caption=caption)
            except Exception as exc:
                delivery.update(status="send_failed", detail=type(exc).__name__)
                try:
                    bot.send_message(chat_id, caption + "\n⚠️ ارسال رسانه ناموفق بود.")
                except Exception:
                    pass
            tg_delivery.append(delivery)
            if index < min(len(tg_media), 3):
                time.sleep(1)
        retained_ids = {r["id"] for r in relevant_all} | {r["id"] for r in unknown} | {r["id"] for r in unanalyzed}
        ai_report = {**analysis,
                     "analyses": {k: v for k, v in analysis["analyses"].items() if k in retained_ids},
                     "analysis_details": {k: v for k, v in analysis.get("analysis_details", {}).items() if k in retained_ids},
                     "relevance": {k: analysis["relevance"].get(k) for k in retained_ids},
                     "best_id": ai_winner["id"] if ai_winner else None,
                     "summary": analysis["summary"] if ai_winner else ""}
        payload = {"schedule_id": schedule_id, "topic": sched.get("topic"), "search_terms": terms,
                   "platform_terms": {"instagram": ig_terms, "telegram": tg_terms, "pinterest": pin_terms},
                   "records": relevant_all, "excluded_count": unrelated_count, "unknown_records": unknown,
                   "run_duplicate_count": run_duplicates, "previous_processed_count": len(previous_skipped),
                   "unanalyzed_records": unanalyzed,
                   "previous_processed_records": previous_skipped, "failed_analysis_count": failed_analysis,
                   "pinterest_queries": pin_audit.get("queries", []), "ai": ai_report,
                   "unified_ranking": [r["id"] for r in unified], "instagram_viral_winner": viral_winner,
                   "instagram_media_delivery": ig_delivery, "telegram_media_delivery": tg_delivery,
                   "telegram": {"posts_collected": getattr(telegram_report, "posts_collected", 0),
                                "stats": getattr(telegram_report, "provider_stats", {}).get("telegram", {})} if telegram_report else None,
                   "warnings": warnings}
        if new_records or previous_skipped:
            data = io.BytesIO(json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8"))
            data.name = f"trend_{schedule_id}_full.json"
            bot.send_document(chat_id, data, caption="📦 نتایج جدید، موارد نامشخص و آمار تکراری/قبلی")
            csv_records = list(relevant_all) + list(unknown) + list(unanalyzed)
            csv_analyses = dict(analysis["analyses"])
            csv_relevance = dict(analysis["relevance"])
            for i, skipped in enumerate(previous_skipped):
                uid = f"history:{i}"
                previous = skipped.get("previous_analysis") or {}
                csv_records.append({"id": uid, "platform": skipped.get("platform"), "url": skipped.get("url", ""),
                                    "post_key": skipped.get("post_key"), "record_status": "previously_processed",
                                    "title": "قبلاً پردازش شده؛ تحلیل مجدد انجام نشد"})
                csv_analyses[uid] = previous.get("analysis", "")
                csv_relevance[uid] = skipped.get("previous_relevance")
            table = io.BytesIO(scheduled_report.report_csv(csv_records, csv_analyses, csv_relevance, analysis.get("analysis_details", {})))
            table.name = f"trend_{schedule_id}_analysis.csv"
            bot.send_document(chat_id, table, caption="📊 جدول نتایج جدید و موارد قبلاً پردازش‌شده")
        state = "OK" if (new_records or previous_skipped) and not warnings else ("PARTIAL" if (new_records or previous_skipped) else "EMPTY")
        database.log_run(schedule_id, state, len(new_records), viral_winner.get("url", "") if viral_winner else "",
                         "; ".join(warnings)[:1000], search_terms=terms)
    except Exception as exc:
        heartbeat_stop.set()
        print(f"SCHEDULED JOB ERROR #{schedule_id}: {type(exc).__name__}")
        try:
            if status_msg:
                bot.edit_message_text(f"❌ اجرای اسکژول #{schedule_id} ناموفق بود: {html.escape(type(exc).__name__)}", chat_id, status_msg.message_id)
            else:
                bot.send_message(chat_id, f"❌ اجرای اسکژول #{schedule_id} ناموفق بود.")
        except Exception:
            pass
        database.log_run(schedule_id, "FAILED", 0, "", type(exc).__name__, search_terms=terms)


# ============================================================
# 🔁 حلقه اسکژولر (هر ۲۰ ثانیه، به وقت تهران)
# ============================================================
def check_schedules():
    now = tehran_now()
    hm = now.strftime("%H:%M")
    day = now.strftime("%Y-%m-%d")
    for s in database.list_schedules(trashed=False):
        if not s["enabled"]:
            continue
        if hm in (s["times"] or []):
            key = f"{day}|{hm}"
            if s["last_triggered"] != key:
                database.mark_triggered(s["id"], key)
                print(f"⏰ Trigger schedule #{s['id']} at {hm} Tehran")
                threading.Thread(target=run_scheduled_job, args=(s["id"],), daemon=True).start()


def scheduler_loop():
    print("⏰ Scheduler started (Asia/Tehran)")
    while True:
        try:
            check_schedules()
        except Exception as e:
            print(f"SCHEDULER ERROR: {e}")
        time.sleep(20)
