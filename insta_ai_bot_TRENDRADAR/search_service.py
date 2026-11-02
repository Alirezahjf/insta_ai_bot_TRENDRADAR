"""سرویس جست‌وجوی زمان‌بندی‌شده: گسترش AvalAI و اسکن مستقل Telegram."""

import threading
import asyncio

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

def scan_telegram_terms(terms, *, auto_join=False):
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
        if not _telegram_scan_lock.acquire(blocking=False):
            return None, "اجرای دیگر Telegram در حال انجام است؛ برای حفاظت از session این اجرا رد شد."
        try:
            join_notes = []
            if auto_join:
                join_notes = radar._run_async(_discover_and_join(radar, hashtags))
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


async def _discover_and_join(radar, hashtags):
    """Discover by plain text, then join up to 10 candidates per term under guards."""
    from trendradar.errors import DailyCapReached, RateLimited
    provider = radar.provider("telegram")
    await provider.connect()
    seen, notes, joined = set(), [], 0
    stop = False
    for tag in hashtags:
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
                if wait > 0:
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
