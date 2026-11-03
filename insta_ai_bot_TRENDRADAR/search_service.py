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
    """Reserve new terms atomically and reuse only explicitly retryable terms."""
    topic = (schedule.get("topic") or "").strip()
    if not topic:
        topic = "، ".join(schedule.get("hashtags") or [])
    if not topic:
        return [], [], "موضوع اسکژول مشخص نیست."
    key = database.topic_key(topic)
    chat_id = schedule["chat_id"]
    platforms = ("instagram", "pinterest", "telegram")
    old = database.get_schedule_keyword_history(schedule["id"])
    old.extend(database.get_schedule_used_terms(schedule["id"]))
    database.reserve_topic_keywords(chat_id, key, _unique_terms(old))
    history = database.topic_history(chat_id, key)
    generated, error = ai_service.generate_topic_keywords(topic, excluded_terms=history, max_keywords=max_keywords)
    fresh = database.reserve_topic_keywords(chat_id, key, _unique_terms(generated), platforms=platforms)
    retryable = database.retryable_topic_terms(chat_id, key, platforms)
    terms = _unique_terms(fresh + [t for t in retryable if t not in fresh])
    if not terms:
        return [], [], error or "عبارت تازه یا قابل تلاش مجدد وجود ندارد؛ جست‌وجوی تکراری انجام نشد."
    if fresh:
        database.record_schedule_keywords(schedule["id"], fresh)
    return terms, fresh, error

def scan_telegram_terms(terms, *, auto_join=False, progress=None, schedule_id=None):
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
            consent_check = None
            if schedule_id is not None:
                def consent_check():
                    current = database.get_schedule(schedule_id)
                    return bool(current and not current.get("trashed") and current.get("telegram_auto_join"))
            discovery = JoinDiscoveryResult()
            try:
                discovery = radar._run_async(_discover_and_join(
                    radar, hashtags, progress=progress, auto_join=auto_join,
                    consent_check=consent_check))
            except Exception as exc:
                discovery.notes.append(f"کشف/عضویت کانال ناموفق: {type(exc).__name__}؛ جست‌وجوی عمومی ادامه یافت.")
            if progress:
                progress("جست‌وجوی هشتگ و fallback متنی در کانال‌های تلگرام")
            report = radar.scan(
                hashtags=hashtags,
                providers=["telegram"],
                limit_per_tag=20,
                window_hours=72,
                auto_join=False,
                joined_only=False,
                discover_channels=False,
                target_channels=discovery.channels,
                max_channels=max(20, min(100, len(discovery.channels) + 20)),
            )
            report.errors.extend(discovery.notes)
            report.provider_stats.setdefault("telegram", {}).update(discovery.stats)
            return report, ""
        finally:
            _telegram_scan_lock.release()
    except Exception as exc:
        return None, f"{type(exc).__name__}"[:300]


_telegram_scan_lock = threading.Lock()


def _bounded_seconds(name, default, minimum, maximum):
    try:
        return max(minimum, min(maximum, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


class JoinDiscoveryResult:
    def __init__(self):
        self.notes = []
        self.channels = []
        self.stats = {"discovered": 0, "joined": 0, "deferred": 0, "pending_approval": 0}


async def _discover_and_join(radar, hashtags, *, progress=None, auto_join=False,
                             consent_check=None):
    """Discover every run; join only with opt-in and under persistent guards.

    Deferred candidates remain in TrendStorage and are reconsidered on later runs.
    Discovery is bounded per term and per run; joins additionally obey the daily
    cap, randomized interval, FloodWait/cooldown and the run time budget.
    """
    from trendradar.errors import DailyCapReached, RateLimited, GuardRejected
    from search_identity import plain_search_text
    provider = radar.provider("telegram")
    await provider.connect()
    result = JoinDiscoveryResult()
    notes, channels = result.notes, result.channels
    per_term = _bounded_seconds("TG_DISCOVER_CHANNELS_PER_TERM", 10, 1, 20)
    candidate_cap = _bounded_seconds("TG_JOIN_CANDIDATES_PER_RUN", 50, 1, 100)
    budget = _bounded_seconds("TG_JOIN_BUDGET_SECONDS", 300, 0, 7200)
    deadline = time.monotonic() + budget
    seen_uid, seen_username = set(), set()

    def add(channel, term=None):
        if channel.uid in seen_uid:
            if term and channel.extra is not None:
                terms = channel.extra.setdefault("scheduled_terms", [])
                if term not in terms:
                    terms.append(term)
            return False
        username = (getattr(channel, "username", None) or "").lower()
        if username and username in seen_username:
            return False
        seen_uid.add(channel.uid)
        if username:
            seen_username.add(username)
        if term:
            if not hasattr(channel, "extra") or channel.extra is None:
                channel.extra = {}
            channel.extra.setdefault("scheduled_terms", []).append(term)
        channels.append(channel)
        return True

    # Reconsider previously discovered, non-joined channels first (oldest first).
    try:
        pending = radar.storage.get_channels(provider="telegram", limit=200)
        pending = sorted((c for c in pending if not c.joined), key=lambda c: c.discovered_at)
        for channel in pending:
            if len(channels) >= candidate_cap:
                break
            if not getattr(channel, "is_scam", False) and not getattr(channel, "is_fake", False) and not (getattr(channel, "extra", {}) or {}).get("forbidden"):
                add(channel)
    except Exception as exc:
        notes.append(f"بارگذاری کانال‌های معوق: {type(exc).__name__}")
    for tag in hashtags:
        if progress:
            progress(f"کشف کانال برای {tag}")
        try:
            found = await provider.discover_channels(plain_search_text(tag), limit=per_term, min_members=0)
        except (RateLimited, DailyCapReached) as exc:
            notes.append(f"توقف کشف کانال: {exc}")
            break
        except Exception as exc:
            notes.append(f"کشف کانال {tag}: {type(exc).__name__}")
            continue
        for channel in found[:per_term]:
            if len(channels) >= candidate_cap:
                notes.append(f"سقف کاندیدای کشف در اجرا ({candidate_cap}) پر شد؛ بقیه اجراهای بعد بررسی می‌شوند.")
                break
            if add(channel, tag):
                result.stats["discovered"] += 1
                try:
                    radar.storage.upsert_channel(channel)
                except Exception:
                    notes.append("ذخیره کانال کشف‌شده ناموفق بود؛ عضویت معوق ممکن است حفظ نشود.")
    if not auto_join:
        notes.append("عضویت خودکار غیرفعال است؛ کشف کانال بدون عضویت انجام شد.")
        return result
    stop = False
    for channel in channels[:candidate_cap]:
        if stop:
            result.stats["deferred"] += 1
            continue
        if channel.joined:
            continue
        if consent_check is not None and not consent_check():
            notes.append("عضویت متوقف شد: رضایت جاری اسکژول غیرفعال است.")
            stop = True
            result.stats["deferred"] += 1
            continue
        if getattr(channel, "is_scam", False) or getattr(channel, "is_fake", False) or (getattr(channel, "extra", {}) or {}).get("forbidden"):
            notes.append(f"عضویت {getattr(channel, 'title', '') or channel.uid} رد شد: کانال پرخطر یا غیرقابل‌دسترس.")
            continue
        try:
            guard = provider.tg.join_guard
            if guard.counter.remaining <= 0:
                notes.append("سقف روزانه عضویت پر است؛ کانال‌های باقی‌مانده به اجرای بعد موکول شدند.")
                stop = True
                result.stats["deferred"] += 1
                continue
            wait = guard.seconds_until_next_join
            if time.monotonic() + wait > deadline:
                notes.append(f"بودجه زمانی عضویت ({budget} ثانیه) تمام شد؛ کانال‌های باقی‌مانده معوق ماندند.")
                stop = True
                result.stats["deferred"] += 1
                continue
            if wait > 0:
                if progress:
                    progress(f"رعایت فاصله ایمن عضویت؛ {result.stats['joined']} عضویت انجام شده")
                await asyncio.sleep(wait + 0.1)
            joined = await provider.join_channel(channel)
            radar.storage.upsert_channel(joined)
            if joined.joined:
                result.stats["joined"] += 1
            elif joined.extra.get("pending_approval"):
                result.stats["pending_approval"] += 1
                notes.append(f"درخواست عضویت {channel.title or channel.uid} ارسال شد؛ هنوز عضو نیست.")
            else:
                notes.append(f"عضویت {channel.title or channel.uid} تأیید نشد.")
        except (DailyCapReached, RateLimited) as exc:
            notes.append(f"توقف عضویت برای ایمنی: {exc}")
            stop = True
            result.stats["deferred"] += 1
        except GuardRejected as exc:
            notes.append(f"عضویت {channel.title or channel.uid} رد شد: {type(exc).__name__}")
        except Exception as exc:
            notes.append(f"عضویت {channel.title or channel.uid}: {type(exc).__name__}")
        finally:
            try:
                radar.storage.save_provider_state("telegram", provider.export_state())
            except Exception:
                notes.append("ذخیره وضعیت گارد عضویت ناموفق بود.")
    notes.append(f"عضویت خودکار: {result.stats['joined']} عضویت موفق، "
                 f"{result.stats['pending_approval']} در انتظار تأیید، "
                 f"{result.stats['deferred']} معوق؛ دسترسی و نتیجه تضمین نمی‌شود.")
    return result


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
