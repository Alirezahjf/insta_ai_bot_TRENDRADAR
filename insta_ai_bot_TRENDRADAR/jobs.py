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
    sched = database.get_schedule(schedule_id)
    if not sched or sched["trashed"]:
        return
    chat_id = sched["chat_id"]
    hashtags = []
    for raw_tag in sched["hashtags"] or []:
        tag = ai_service.normalize_search_keyword(raw_tag)
        if tag and tag not in hashtags:
            hashtags.append(tag)
    count = sched["count_per_hashtag"]
    ctype = sched["content_type"]
    want_cm = bool(sched["want_comments"])
    top_n = sched["top_n"]
    cpc = sched["comments_per"]
    search_terms = list(hashtags)
    generated_terms = []
    keyword_error = ""
    telegram_report = None
    telegram_error = ""
    apify_errors = []
    hm = tehran_now().strftime("%H:%M")

    try:
        status_msg = bot.send_message(chat_id,
            f"⏰ <b>اجرای خودکار اسکژول #{schedule_id}</b>\n🕐 {hm} به وقت تهران\n"
            f"🏷️ هشتگ‌های پایه: {'، '.join('#' + t for t in hashtags)}\n\n"
            "🤖 در حال پیشنهاد کلیدواژه‌های تازه با AvalAI...")
        try:
            search_terms, generated_terms, keyword_error = search_service.generate_schedule_search_terms(
                schedule_id, hashtags, max_keywords=5
            )
        except Exception as exc:
            # خرابی سرویس AI/تاریخچه نباید مسیر اصلی Instagram را متوقف کند.
            search_terms = list(hashtags)
            keyword_error = f"{type(exc).__name__}: {exc}"[:300]
        search_terms = search_terms or list(hashtags)
        tag_list = "، ".join(f"#{t}" for t in search_terms)
        generated_list = "، ".join(f"#{t}" for t in generated_terms) or "بدون پیشنهاد تازه"
        bot.edit_message_text(
            f"⏰ <b>اجرای خودکار اسکژول #{schedule_id}</b>\n🕐 {hm} به وقت تهران\n"
            f"🏷️ عبارت‌های مشترک برای Instagram، Telegram و Pinterest: {tag_list}\n"
            f"🤖 پیشنهاد تازه: {generated_list}\n\n⏳ در حال اسکرپ Instagram با Apify...",
            chat_id, status_msg.message_id)

        urls = to_hashtag_urls(search_terms)
        all_media = []
        if ctype in ("posts", "both"):
            try:
                all_media.extend(apify_service.scrape_media(urls, "posts", count))
            except Exception as exc:
                apify_errors.append(f"posts: {type(exc).__name__}: {exc}"[:180])
        if ctype in ("reels", "both"):
            try:
                all_media.extend(apify_service.scrape_media(urls, "reels", count))
            except Exception as exc:
                apify_errors.append(f"reels: {type(exc).__name__}: {exc}"[:180])

        # Telegram مستقل از نتیجه‌ی Apify است؛ خطای هر مسیر نباید مسیر دیگر را قطع کند.
        bot.edit_message_text(
            f"⏰ اسکژول #{schedule_id}\n📡 در حال جست‌وجوی Telegram با همان عبارت‌ها...\n🏷️ {tag_list}",
            chat_id, status_msg.message_id)
        telegram_report, telegram_error = search_service.scan_telegram_terms(search_terms)
        bot.edit_message_text(
            f"⏰ اسکژول #{schedule_id}\n📌 در حال جست‌وجوی Pinterest...",
            chat_id, status_msg.message_id)
        try:
            pinterest_pins, pinterest_errors = pinterest_service.scan_pinterest_terms(search_terms)
        except Exception as exc:
            pinterest_pins, pinterest_errors = [], [f"{type(exc).__name__}: {exc}"[:240]]
        # Produce the shared shortlist even if Instagram or Telegram returned no results.
        ranked = scoring.compute_virality(all_media) if all_media else []
        send_unified_results(chat_id, ranked, telegram_report, pinterest_pins, pinterest_errors)

        if not all_media:
            bot.edit_message_text(
                f"⏰ اسکژول #{schedule_id}: از Instagram چیزی پیدا نشد.\n🏷️ {tag_list}\n"
                "گزارش Telegram جداگانه ارسال می‌شود.",
                chat_id, status_msg.message_id)
            send_telegram_summary(chat_id, telegram_report, telegram_error)
            warnings = []
            if keyword_error:
                warnings.append(f"⚠️ پیشنهاد AvalAI در دسترس نبود؛ از هشتگ‌های پایه استفاده شد: {keyword_error[:180]}")
            warnings.extend(f"⚠️ خطای Apify ({item})" for item in apify_errors[:2])
            if warnings:
                bot.send_message(chat_id, "\n".join(html.escape(warning) for warning in warnings))
            run_status = "PARTIAL" if pinterest_pins or telegram_report is not None else "EMPTY"
            run_error = "; ".join(apify_errors + ([telegram_error] if telegram_error else []) + pinterest_errors)
            database.log_run(
                schedule_id, run_status,
                getattr(telegram_report, "posts_collected", 0), "", run_error,
                search_terms=search_terms,
            )
            return

        # ۱) وایرال‌اسکور
        bot.edit_message_text(f"⏰ اسکژول #{schedule_id}\n📊 در حال محاسبه وایرال‌اسکور {len(all_media)} پست...",
                              chat_id, status_msg.message_id)
        # رتبه‌بندی پیش‌تر برای گزارش مشترک محاسبه شده است.

        # ۲) کامنت برای پست‌های برتر (بر اساس وایرال‌اسکور)
        comments, grouped, cm_error = [], {}, ""
        top_posts = ranked[:max(top_n, 3)]
        if want_cm:
            top_urls = apify_service.valid_comment_urls(top_posts[:top_n])
            if top_urls:
                bot.edit_message_text(
                    f"⏰ اسکژول #{schedule_id}\n💬 در حال گرفتن کامنت {len(top_urls)} پست برتر...",
                    chat_id, status_msg.message_id)
                try:
                    comments = apify_service.scrape_comments(top_urls, cpc)
                    grouped = group_comments_by_post(top_posts, comments)
                except Exception as e:
                    cm_error = str(e)[:200]
                    print(f"COMMENTS ERROR (sched #{schedule_id}): {e} | urls={top_urls[:3]}")
                    comments, grouped = [], {}
            else:
                cm_error = "URL معتبری برای کامنت‌گیری پیدا نشد"

        # ۳) نمایش کامل نتایج
        render_posts_full(chat_id, status_msg.message_id, all_media,
            title_line=f"⏰ اسکژول #{schedule_id} | 🕐 {hm}\n🏷️ {tag_list}",
            file_prefix=f"sched{schedule_id}", header="✅ <b>اجرای خودکار تمام شد!</b> 🎉")

        if cm_error:
            bot.send_message(chat_id,
                f"⚠️ <b>کامنت‌گیری رد شد:</b>\n<code>{cm_error}</code>\n"
                f"<i>تحلیل AI فقط با دیتای پست‌ها انجام میشه.</i>")

        # ۴) هر پست برتر + کامنت‌هاش زیرش
        if want_cm and comments:
            send_top_posts_with_comments(chat_id, ranked[:top_n], grouped)
            # فایل کامل کامنت‌ها
            import io as _io, csv as _csv
            buf = _io.StringIO()
            fields = ["postUrl", "ownerUsername", "text", "likesCount", "timestamp"]
            w = _csv.DictWriter(buf, fieldnames=fields)
            w.writeheader()
            for c in comments:
                w.writerow({"postUrl": c.get("postUrl") or c.get("inputUrl") or "",
                            "ownerUsername": c.get("ownerUsername") or "",
                            "text": (c.get("text") or "").replace("\n", " ")[:500],
                            "likesCount": c.get("likesCount") or 0,
                            "timestamp": c.get("timestamp") or ""})
            data = _io.BytesIO(buf.getvalue().encode("utf-8-sig"))
            data.name = f"sched{schedule_id}_comments.csv"
            bot.send_document(chat_id, data, caption=f"💬 <b>فایل همه کامنت‌ها</b> - {len(comments)} کامنت")

        # ۵) تحلیل AI روی ۳ تای برتر
        ai_msg = bot.send_message(chat_id, "🤖 در حال تحلیل هوشمند ۳ پست برتر با AI...")
        top3 = ranked[:3]
        candidates = []
        for p in top3:
            key = p.get("url") or p.get("shortCode")
            cmts = grouped.get(key, []) if grouped else []
            parts = p.get("_viral_parts", {})
            candidates.append({
                "url": p.get("url", ""), "owner": p.get("ownerUsername", "?"),
                "viral_score": p.get("viral_score", "?"),
                "likes": p.get("likesCount", 0), "comments_count": p.get("commentsCount", 0),
                "views": p.get("videoPlayCount") or p.get("igPlayCount") or p.get("videoViewCount") or "—",
                "type": p.get("type", ""), "age": fa_age(parts.get("age_hours", "")),
                "location": p.get("locationName") or "—",
                "caption": p.get("caption", ""),
                "comments": [{"user": c.get("ownerUsername", "?"), "text": c.get("text", "")} for c in cmts[:10]],
                "comments_total": len(cmts),
            })
        verdict, err = ai_service.analyze_top_posts(candidates, {
            "hashtags": search_terms, "scanned_count": len(all_media),
            "date_str": tehran_now().strftime("%Y-%m-%d %H:%M"),
        })
        if verdict:
            bot.delete_message(chat_id, ai_msg.message_id)
            send_long(chat_id, verdict, header="🤖 <b>تحلیل هوشمند AI</b> (اسکژول #{})\n\n".format(schedule_id))
            # کارت عکس برنده (پست اول وایرال)
            try:
                w = top3[0]
                kb = InlineKeyboardMarkup()
                if w.get("url"):
                    kb.add(InlineKeyboardButton("🔗 مشاهده پست برنده", url=w["url"]))
                if w.get("displayUrl"):
                    bot.send_photo(chat_id, w["displayUrl"],
                        caption=f"🏆 <b>پست برنده الگوریتم</b> (وایرال {w.get('viral_score')})\n@{w.get('ownerUsername', '?')} | ❤️ {fmt_num(w.get('likesCount'))}",
                        reply_markup=kb)
            except Exception:
                pass
            database.log_run(
                schedule_id, "OK", len(all_media), top3[0].get("url", ""),
                error="; ".join(pinterest_errors)[:300], search_terms=search_terms,
            )
        else:
            bot.edit_message_text(
                f"⚠️ تحلیل AI ناموفق بود: <code>{err}</code>\n\n"
                f"ولی ۳ پست برتر الگوریتم:\n" + "\n".join(
                    f"{i + 1}. 🔥 {p.get('viral_score')} — @{p.get('ownerUsername', '?')} — {p.get('url', '')}"
                    for i, p in enumerate(top3)),
                chat_id, ai_msg.message_id)
            database.log_run(
                schedule_id, "AI_FAIL", len(all_media), top3[0].get("url", ""), "; ".join(filter(None, [err, *pinterest_errors]))[:300],
                search_terms=search_terms,
            )

        send_telegram_summary(chat_id, telegram_report, telegram_error)
        warnings = []
        if keyword_error:
            warnings.append(f"⚠️ پیشنهاد AvalAI ناموفق بود؛ هشتگ‌های پایه استفاده شدند: {keyword_error[:180]}")
        warnings.extend(f"⚠️ خطای Apify ({item})" for item in apify_errors[:2])
        if warnings:
            bot.send_message(chat_id, "\n".join(html.escape(warning) for warning in warnings))
        bot.send_message(chat_id, "🎉 گزارش اسکژول کامل شد! 👇", reply_markup=main_menu())
    except Exception as e:
        print(f"SCHEDULED JOB ERROR #{schedule_id}: {e}")
        try:
            bot.send_message(chat_id, f"❌ <b>خطا در اجرای اسکژول #{schedule_id}!</b>\n<code>{str(e)[:400]}</code>")
        except Exception:
            pass
        database.log_run(
            schedule_id, "FAILED", 0, "", str(e)[:300], search_terms=search_terms
        )


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
