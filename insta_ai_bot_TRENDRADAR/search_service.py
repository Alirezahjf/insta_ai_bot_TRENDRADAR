"""سرویس جست‌وجوی زمان‌بندی‌شده: گسترش AvalAI و اسکن مستقل Telegram."""

import threading

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


def scan_telegram_terms(terms):
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

        global _radar
        with _radar_lock:
            if _radar is None:
                from trendradar import TrendRadar

                _radar = TrendRadar(settings=settings)
            radar = _radar

        report = radar.scan(
            hashtags=hashtags,
            limit_per_tag=20,
            window_hours=72,
            auto_join=False,
            joined_only=False,
            discover_channels=False,
        )
        return report, ""
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"[:300]
