"""سرویس جست‌وجوی زمان‌بندی‌شده: گسترش AvalAI و اسکن مستقل Telegram."""

import threading
import asyncio
import os
import time

import ai_service
import database


_radar = None
_radar_lock = threading.Lock()


def _unique_terms(values):
    seen = set()
    terms = []
    for value in values or []:
        term = ai_service.normalize_search_keyword(value)
        if term and term not in seen:
            seen.add(term)
            terms.append(term)
    return terms


def generate_schedule_search_terms(schedule_id, seed_terms, *, max_keywords=5):
    """کلیدواژه‌های پایه را با پیشنهادهای تازه‌ی AvalAI گسترش و سابقه را ذخیره می‌کند.

    هشتگ‌های پایه‌ی کاربر برای پایش مستمر حفظ می‌شوند؛ فقط کلیدواژه‌های تولیدشده
    در اجراهای بعدی همان اسکژول از تکرار منع می‌شوند.
    """
    seeds = _unique_terms(seed_terms)
    history = database.get_schedule_keyword_history(schedule_id)
    generated, error = ai_service.generate_search_keywords(
        seeds,
        excluded_terms=history,
        max_keywords=max_keywords,
    )
    blocked = set(_unique_terms(seeds + history))
    candidates = [term for term in _unique_terms(generated) if term not in blocked]
    fresh = database.record_schedule_keywords(schedule_id, candidates)
    # در صورت تولید نشدن کلیدواژه، جست‌وجو با ورودی کاربر ادامه پیدا می‌کند.
    return _unique_terms(seeds + fresh), fresh, error



def generate_topic_search_terms(schedule, max_keywords=5):
    """Reserve new terms atomically across schedules of the same chat and topic."""
    topic = (schedule.get("topic") or "").strip()
    if not topic:
        # Existing schedules retain their original subject as context, not fixed
        # recurring hashtags. Import their previous history before generating.
        topic = "، ".join(schedule.get("hashtags") or [])
    if not topic:
        return [], [], "موضوع اسکژول مشخص نیست."
    key = database.topic_key(topic)
    chat_id = schedule["chat_id"]
    old = database.get_schedule_keyword_history(schedule["id"])
    old.extend(database.get_schedule_used_terms(schedule["id"]))
    database.reserve_topic_keywords(chat_id, key, _unique_terms(old))
    history = database.topic_history(chat_id, key)
    generated, error = ai_service.generate_topic_keywords(topic, excluded_terms=history, max_keywords=max_keywords)
    fresh = database.reserve_topic_keywords(chat_id, key, _unique_terms(generated))
    if not fresh:
        return [], [], error or "عبارت تازه‌ای تولید نشد؛ برای جلوگیری از تکرار جست‌وجو اجرا نشد."
    database.record_schedule_keywords(schedule["id"], fresh)
    return fresh, fresh, error

def scan_telegram_terms(terms, *, auto_join=False, progress=None):
    """همان کلیدواژه‌های جست‌وجوی Instagram را روی Telegram نیز اسکن می‌کند.

    شکست یا نبود پیکربندی Telegram ایزوله است و نباید مانع اجرای Apify شود.
    خروجی: (RunReport یا None, پیام خطا یا رشته‌ی خالی).
    """
    hashtags = _unique_terms(terms)
    if not hashtags:
        return None, "کلیدواژه‌ای برای جست‌وجوی Telegram موجود نیست."

    try:
        from trendradar.config import load_settings

        settings = load_settings()
        if not settings.telegram.configured:
            return None, "TG_API_ID و TG_API_HASH تنظیم نشده‌اند."

        settings.enabled_providers = ["telegram"]
        global _radar
        with _radar_lock:
            if _radar is None:
                from trendradar import TrendRadar

                _radar = TrendRadar(settings=settings)
            radar = _radar

        # Serialize full scans: Telethon sessions and join guards are shared.
        queue_timeout = _bounded_seconds("TG_SCAN_QUEUE_TIMEOUT", 900, 1, 7200)
        deadline = time.monotonic() + queue_timeout
        acquired = False
        while not acquired:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, f"صف Telegram پس از {queue_timeout} ثانیه به پایان نرسید؛ جست‌وجوی این اجرا انجام نشد."
            if progress:
                progress("در صف تلگرام؛ اجرای دیگری در حال انجام است")
            acquired = _telegram_scan_lock.acquire(timeout=min(5, remaining))
        try:
            join_notes = []
            if auto_join:
                join_notes = radar._run_async(_discover_and_join(radar, hashtags, progress=progress))
            if progress:
                progress("جست‌وجوی هشتگ در تلگرام")
            report = radar.scan(
            hashtags=hashtags,
            providers=["telegram"],
            limit_per_tag=20,
            window_hours=72,
            auto_join=False,
            joined_only=False,
            discover_channels=False,
            )
            report.errors.extend(join_notes)
            return report, ""
        finally:
            _telegram_scan_lock.release()
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"[:300]


_telegram_scan_lock = threading.Lock()


def _bounded_seconds(name, default, minimum, maximum):
    try:
        return max(minimum, min(maximum, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


async def _discover_and_join(radar, hashtags, *, progress=None):
    """Discover by plain text, then join up to 10 candidates per term under guards."""
    from trendradar.errors import DailyCapReached, RateLimited
    provider = radar.provider("telegram")
    await provider.connect()
    seen, notes, joined = set(), [], 0
    # Time budget prevents a single run from monopolizing the session for hours.
    budget = _bounded_seconds("TG_JOIN_BUDGET_SECONDS", 300, 0, 7200)
    deadline = time.monotonic() + budget
    stop = False
    for tag in hashtags:
        if progress:
            progress(f"کشف کانال برای {tag}؛ {joined} عضویت انجام شده")
        try:
            channels = await provider.discover_channels(tag.replace("_", " "), limit=10, min_members=0)
        except (RateLimited, DailyCapReached) as exc:
            notes.append(f"توقف کشف کانال: {exc}")
            break
        except Exception as exc:
            notes.append(f"کشف کانال {tag}: {type(exc).__name__}")
            continue
        for channel in channels[:10]:
            if channel.uid in seen or channel.joined:
                continue
            seen.add(channel.uid)
            try:
                wait = provider.tg.join_guard.seconds_until_next_join
                if time.monotonic() + wait > deadline:
                    notes.append(f"بودجه زمانی عضویت ({budget} ثانیه) تمام شد؛ جست‌وجو ادامه یافت.")
                    stop = True
                    break
                if wait > 0:
                    if progress:
                        progress(f"رعایت فاصله ایمن عضویت؛ {joined} عضویت انجام شده")
                    await asyncio.sleep(wait + 0.1)
                result = await provider.join_channel(channel)
                radar.storage.upsert_channel(result)
                if result.joined:
                    joined += 1
                else:
                    notes.append(f"عضویت کانال {channel.title} تأیید نشد")
            except (DailyCapReached, RateLimited) as exc:
                notes.append(f"توقف عضویت برای ایمنی: {exc}")
                stop = True
                break
            except Exception as exc:
                notes.append(f"عضویت {channel.title}: {type(exc).__name__}")
            finally:
                radar.storage.save_provider_state("telegram", provider.export_state())
        if stop:
            break
    notes.append(f"عضویت خودکار: {joined} کانال در این اجرا؛ دسترسی و نتیجه تضمین نمی‌شود.")
    return notes


async def _read_telegram_media(radar, item, *, max_bytes=45 * 1024 * 1024):
    """Read an accessible, unprotected photo/video in bounded memory.

    No export from chats with Telegram forwarding restrictions. No disk cache.
    """
    from telethon import utils
    from telethon.tl.types import PeerChannel, PeerChat, PeerUser
    provider = radar.provider("telegram")
    await provider.connect()
    uid = str(item.get("channel_uid") or "")
    if not uid.startswith("telegram:"):
        return None, "شناسهٔ کانال معتبر نیست"
    try:
        native_id = int(uid.split(":", 1)[1])
        message_id = int(item["message_id"])
    except (ValueError, KeyError, TypeError):
        return None, "شناسهٔ پیام معتبر نیست"
    client = provider.tg.raw
    try:
        peer_types = {"PeerChannel": PeerChannel, "PeerChat": PeerChat, "PeerUser": PeerUser}
        peer_type = item.get("peer_type", "PeerChannel")
        if peer_type not in peer_types:
            return None, "نوع منبع پیام مشخص نیست"
        entity = await client.get_input_entity(utils.get_peer_id(peer_types[peer_type](native_id)))
        chat = await client.get_entity(entity)
        if getattr(chat, "noforwards", False):
            return None, "کانال انتقال محتوا را محدود کرده است"
        msg = await client.get_messages(entity, ids=message_id)
        if msg is None or not getattr(msg, "media", None):
            return None, "پیام یا رسانه در دسترس نیست"
        if getattr(msg, "noforwards", False):
            return None, "انتقال این پیام محدود شده است"
        file_info = getattr(msg, "file", None)
        size = getattr(file_info, "size", None)
        if size is not None and size > max_bytes:
            return None, "حجم رسانه از سقف ۴۵ مگابایت بیشتر است"
        data = bytearray()
        async for chunk in client.iter_download(msg.media, request_size=512 * 1024):
            if len(data) + len(chunk) > max_bytes:
                return None, "حجم رسانه از سقف ۴۵ مگابایت بیشتر است"
            data.extend(chunk)
        if not data:
            return None, "رسانه خالی است"
        return bytes(data), ""
    except Exception as exc:
        return None, f"دسترسی یا دریافت رسانه ناموفق ({type(exc).__name__})"


def fetch_telegram_media(item):
    """Use the shared Telethon session without overlapping a scan or join."""
    with _radar_lock:
        radar = _radar
    if radar is None:
        return None, "سشن جست‌وجوی تلگرام در دسترس نیست"
    if not _telegram_scan_lock.acquire(timeout=15):
        return None, "تلگرام مشغول اجرای دیگری است"
    try:
        return radar._run_async(asyncio.wait_for(_read_telegram_media(radar, item), timeout=90))
    except Exception as exc:
        return None, f"دریافت رسانه ناموفق ({type(exc).__name__})"
    finally:
        _telegram_scan_lock.release()
