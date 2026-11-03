"""Bounded Instagram media delivery from real Apify post fields.

Media is sent by URL to Telegram; the bot does not permanently download or cache
Instagram files. Bot API fetch/size errors are reported and the original post
link remains available. Carousel groups are capped to avoid flood.
"""
from __future__ import annotations

import html
import time
from urllib.parse import urlsplit

MAX_POSTS = 3
MAX_MEDIA_PER_POST = 3
MAX_TOTAL_MEDIA = 6


def valid_media_url(url):
    if not isinstance(url, str) or not 8 <= len(url) <= 4096:
        return False
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        return (parts.scheme == "https" and not parts.username and not parts.password
                and (host.endswith(".cdninstagram.com") or host.endswith(".fbcdn.net")
                     or host == "cdninstagram.com"))
    except ValueError:
        return False


def _first_url(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("url") or value.get("src")
    return None


def extract_media(post, max_items=MAX_MEDIA_PER_POST):
    """Extract photo/video URLs from Image/Video/Sidecar Apify shapes."""
    media, seen = [], set()

    def add(kind, url, field, cover=False):
        if len(media) >= max_items or not valid_media_url(url) or url in seen:
            return
        seen.add(url)
        media.append({"type": kind, "url": url, "source_field": field, "cover_only": cover})

    post_type = str(post.get("type") or post.get("productType") or "").lower()
    children = post.get("childPosts") if isinstance(post.get("childPosts"), list) else []
    if post_type in ("sidecar", "carousel_container", "carousel") or children:
        for index, child in enumerate(children[:max_items]):
            if not isinstance(child, dict):
                continue
            child_type = str(child.get("type") or "").lower()
            if child_type == "video" or child.get("videoUrl"):
                add("video", child.get("videoUrl"), f"childPosts[{index}].videoUrl")
            else:
                add("photo", _first_url(child.get("displayUrl")) or _first_url((child.get("images") or [None])[0] if isinstance(child.get("images"), list) else None),
                    f"childPosts[{index}].displayUrl")
        if not media and isinstance(post.get("images"), list):
            for index, image in enumerate(post["images"][:max_items]):
                add("photo", _first_url(image), f"images[{index}]")
        if not media:
            add("photo", post.get("displayUrl"), "displayUrl", cover=True)
        return media
    if post_type == "video" or post.get("videoUrl"):
        add("video", post.get("videoUrl"), "videoUrl")
        if not media:
            add("photo", post.get("displayUrl"), "displayUrl", cover=True)
        return media
    add("photo", post.get("displayUrl"), "displayUrl")
    if not media and isinstance(post.get("images"), list):
        for index, image in enumerate(post["images"][:max_items]):
            add("photo", _first_url(image), f"images[{index}]")
    return media


def _caption(record, rank, analysis, relevance):
    flag = (relevance or {}).get(record["id"])
    status = "مرتبط" if flag is True else "نامشخص" if flag is None else "نامرتبط"
    owner = html.escape(str((record.get("raw") or {}).get("ownerUsername") or record.get("owner") or "نامشخص"))
    metrics = []
    for label, key in (("❤️", "likes"), ("💬", "comments_count"), ("👁️", "views")):
        value = record.get(key)
        metrics.append(f"{label} {value if value is not None else 'نامشخص'}")
    reason = ((analysis or {}).get("analysis_details", {}).get(record["id"], {}).get("reason")
              or (analysis or {}).get("analyses", {}).get(record["id"], ""))
    link = html.escape(record.get("url") or "", quote=True)
    text = (f"📷 <b>Instagram #{rank}</b> | {status}\n"
            f"👤 @{owner} | {' | '.join(metrics)}\n"
            f"🔗 {link}\n"
            f"📝 {html.escape(str(reason)[:350])}")
    return text[:1000]


def _error_status(exc):
    message = str(exc).lower()
    if "too large" in message or "413" in message or "request entity too large" in message:
        return "حجم رسانه برای ارسال ربات بیش از حد مجاز است"
    if "failed to get" in message or "url" in message or "http" in message:
        return "دریافت رسانه توسط تلگرام ناموفق بود؛ ممکن است لینک منقضی یا غیرقابل‌دسترس باشد"
    return f"ارسال رسانه ناموفق بود ({type(exc).__name__})"


def send_instagram_media(bot, chat_id, records, analysis, *, max_posts=MAX_POSTS,
                         max_media_per_post=MAX_MEDIA_PER_POST,
                         max_total_media=MAX_TOTAL_MEDIA, pause=1.0):
    """Send at most three top Instagram posts, with bounded media per post."""
    from telebot.types import InputMediaPhoto, InputMediaVideo
    deliveries, remaining = [], max_total_media
    for rank, record in enumerate(records[:max_posts], 1):
        caption = _caption(record, rank, analysis, analysis.get("relevance", {}))
        raw = record.get("raw") or {}
        media = extract_media(raw, max_items=min(max_media_per_post, remaining))
        entry = {"id": record["id"], "rank": rank, "url": record.get("url", ""),
                 "media_count": len(media), "status": "sent", "details": []}
        if not media:
            entry["status"] = "no_media"
            entry["details"].append("رسانه معتبر در داده Apify موجود نبود؛ لینک اصلی ارسال شد.")
            try:
                bot.send_message(chat_id, caption + "\n⚠️ رسانه در دسترس نیست.", disable_web_page_preview=True)
            except Exception as exc:
                entry["status"] = "send_failed"
                entry["details"].append(_error_status(exc))
            deliveries.append(entry)
            continue
        try:
            if len(media) == 1:
                item = media[0]
                if item["type"] == "video":
                    bot.send_video(chat_id, item["url"], caption=caption)
                else:
                    bot.send_photo(chat_id, item["url"], caption=caption)
            else:
                group = []
                for index, item in enumerate(media):
                    cls = InputMediaVideo if item["type"] == "video" else InputMediaPhoto
                    group.append(cls(item["url"], caption=caption if index == 0 else None,
                                     parse_mode="HTML" if index == 0 else None))
                bot.send_media_group(chat_id, group)
            remaining -= len(media)
            entry["details"].append("رسانه با لینک اصلی ارسال شد.")
        except Exception as exc:
            entry["status"] = "send_failed"
            entry["details"].append(_error_status(exc))
            try:
                bot.send_message(chat_id, caption + "\n⚠️ " + html.escape(entry["details"][-1]),
                                 disable_web_page_preview=True)
            except Exception:
                entry["details"].append("ارسال پیام لینک جایگزین نیز ناموفق بود.")
        deliveries.append(entry)
        if pause and rank < min(len(records), max_posts):
            time.sleep(pause)
        if remaining <= 0:
            break
    return deliveries
