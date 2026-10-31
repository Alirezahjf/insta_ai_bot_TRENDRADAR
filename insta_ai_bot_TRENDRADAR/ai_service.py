"""اتصال به AvalAI (سازگار با OpenAI) + تحلیل و گسترش کلیدواژه‌های جست‌وجو"""
import json
import re

import config

_client = None

_TERM_SEPARATORS = re.compile(r"[\s-]+")
_TERM_INVALID = re.compile(r"[^\w\u0600-\u06FF\u0750-\u077F]+", re.UNICODE)
_AR_TO_FA = str.maketrans({
    "ي": "ی", "ك": "ک", "ﻻ": "لا", "ة": "ه",
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ؤ": "و", "ئ": "ی",
})


def normalize_search_keyword(value):
    """یک عبارت را به شکل مناسب هشتگ Instagram/Telegram نرمال می‌کند."""
    if not isinstance(value, str):
        return ""
    value = value.strip().lstrip("#").casefold().translate(_AR_TO_FA)
    value = value.replace("\u200c", " ").replace("\u200f", " ").replace("\u200e", " ")
    value = _TERM_SEPARATORS.sub("_", value)
    value = _TERM_INVALID.sub("", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value if 2 <= len(value) <= 64 else ""


def _unique_search_keywords(values, *, excluded=(), limit=5):
    blocked = {normalize_search_keyword(x) for x in excluded}
    blocked.discard("")
    out = []
    for value in values or []:
        term = normalize_search_keyword(value)
        if not term or term in blocked:
            continue
        blocked.add(term)
        out.append(term)
        if len(out) >= max(0, int(limit)):
            break
    return out


def _parse_keyword_response(text):
    """خواندن JSON پاسخ مدل؛ یک fallback برای پاسخ‌های دارای code fence هم دارد."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    candidates = [text]
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start >= 0 and end > start:
            candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("keywords", "hashtags", "terms"):
                if isinstance(payload.get(key), list):
                    return payload[key]
    return []


def generate_search_keywords(seed_terms, *, excluded_terms=(), max_keywords=5, model=None):
    """
    گسترش هشتگ‌های پایه با AvalAI بر اساس موضوع ورودی کاربر.

    این تابع «جست‌وجوی زنده» انجام نمی‌دهد؛ فقط عبارت‌های مناسب جست‌وجو
    تولید می‌کند. هر عبارت تکراری، هشتگ پایه، یا عبارت موجود در سابقه حذف می‌شود.
    خروجی: (فهرست عبارت‌های تازه، خطا یا None).
    """
    seeds = _unique_search_keywords(seed_terms, limit=100)
    excluded = list(seeds) + list(excluded_terms or [])
    if max_keywords <= 0:
        return [], None
    if not config.AVALAI_API_KEY:
        return [], "AVALAI_API_KEY تنظیم نشده؛ از هشتگ‌های ثبت‌شده‌ی اسکژول استفاده می‌شود."

    system_prompt = (
        "تو متخصص تحقیق موضوع و تولید عبارت جست‌وجو هستی. تنها بر اساس هشتگ‌های پایه "
        "موضوع کاربر را استنباط کن و پیشنهادهای مرتبط و متنوع برای همان موضوع بده. "
        "اگر موضوع مبهم بود عمومی‌ترین معنی آن را حفظ کن و حوزه نامرتبط نساز. "
        "مثلاً python برنامه‌نویسی است نه پوشاک؛ اگر هشتگ‌ها مربوط به لباس و مدلینگ باشند "
        "پوشاک کژوال را در نظر بگیر. عبارت‌ها می‌توانند فارسی، انگلیسی یا ترکی باشند. "
        "از ادعای داده زنده و ترند قطعی خودداری کن. خروجی فقط JSON با ساختار "
        '{"keywords": ["...", "..."]} باشد.'
    )
    user_prompt = (
        f"هشتگ‌های پایه: {json.dumps(seeds, ensure_ascii=False)}\n"
        f"عبارت‌های استفاده‌شده که نباید تکرار شوند: "
        f"{json.dumps([normalize_search_keyword(x) for x in (excluded_terms or [])][-100:], ensure_ascii=False)}\n"
        f"دقیقاً حداکثر {int(max_keywords)} عبارت تازه و غیرتکراری تولید کن. "
        "هر مورد باید برای جست‌وجوی هشتگ مناسب باشد؛ پیشوند # نگذار و توضیح اضافه نده."
    )
    try:
        response = get_client().chat.completions.create(
            model=model or config.AVALAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.35,
            max_tokens=400,
            timeout=30.0,
        )
        content = response.choices[0].message.content or ""
        keywords = _parse_keyword_response(content)
        return _unique_search_keywords(keywords, excluded=excluded, limit=max_keywords), None
    except Exception as e:
        return [], str(e)[:300]


def get_client():
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI(api_key=config.AVALAI_API_KEY, base_url=config.AVALAI_BASE_URL)
    return _client


SYSTEM_PROMPT = """تو یک متخصص ارشد رشد اینستاگرام و تحلیل محتوای وایرال هستی.
فارسی، دقیق، کاربردی و بدون کلی‌گویی جواب بده. فقط بر اساس «داده‌هایی که داده شده» تحلیل کن؛ حدس بی‌پایه نزن.
اعداد و فکت‌ها رو از داده‌ها نقل کن. خروجی باید تمیز و با ایموجی و تیتر باشه (متن تلگرام، بدون مارک‌داون پیچیده)."""


def _clip(s, n):
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[:n] + "…"


def build_analysis_prompt(candidates, context):
    """candidates: لیست ۳ پست برتر، هر کدوم dict با metrics + caption + comments[]
    context: dict با hashtags, scanned_count, date_str"""
    lines = [
        f"هشتگ‌ها: {', '.join('#' + h for h in context.get('hashtags', []))}",
        f"تعداد پست‌های بررسی‌شده در این اسکرپ: {context.get('scanned_count', '?')}",
        f"تاریخ: {context.get('date_str', '')} (به وقت تهران)",
        "",
        "سه پست برتر بر اساس الگوریتم وایرال‌اسکور (۳۰٪ بازدید/لایک، ۲۵٪ گفتگو/کامنت، ۲۰٪ ویو، ۱۵٪ تازگی، ۱۰٪ کیفیت کپشن):",
    ]
    for i, c in enumerate(candidates, 1):
        lines += [
            "",
            f"━━━ پست {i} ━━━",
            f"لینک: {c.get('url', '')}",
            f"پیج: @{c.get('owner', '')} | وایرال‌اسکور: {c.get('viral_score', '?')} از ۱۰۰",
            f"❤️ لایک: {c.get('likes', 0)} | 💬 کامنت: {c.get('comments_count', 0)} | 👁️ ویو: {c.get('views', '—')} | نوع: {c.get('type', '')}",
            f"🕐 سن پست: {c.get('age', '')} | 📍 لوکیشن: {c.get('location', '—')}",
            f"کپشن: {_clip(c.get('caption', ''), 500)}",
        ]
        cmts = c.get("comments", [])[:10]
        if cmts:
            lines.append(f"نمونه کامنت‌ها ({len(cmts)} از {c.get('comments_total', len(cmts))}):")
            for cm in cmts:
                lines.append(f"  - @{cm.get('user', '?')}: {_clip(cm.get('text', ''), 150)}")
        else:
            lines.append("کامنتی ثبت نشده.")
    lines += [
        "",
        "━━━ دستور ━━━",
        "۱) بین این ۳ پست، «تک و تاپ‌ترین» برای وایرال شدن/الگوبرداری رو انتخاب کن و لینکش رو بده.",
        "۲) دلیل انتخابت رو با ارجاع به عددها و کامنت‌ها بگو (۳ تا ۵ بولت).",
        "۳) روش تحلیل خودت رو خلاصه بگو (به چه معیارهایی وزن دادی و چرا).",
        "۴) جدول مقایسه سریع هر ۳ پست (نقاط قوت کلیدی هر کدوم در یک خط).",
        "۵) نقاط قوت ✅ و ضعف/ریسک‌های ❌ پست برنده.",
        "۶) راهنمای قدم‌به‌قدم «پست‌گذاری به همین سبک»: موضوع، هوک ۳ ثانیه اول، ساختار کپشن، هشتگ‌ها، ساعت انتشار، فرمت (ریلز/کاروسل/عکس).",
        "۷) در آخر: ۵ هشتگ پیشنهادی + بهترین ساعت انتشار برای این سبک محتوا.",
        "",
        "قالب خروجی (دقیقاً همین تیترها):",
        "🏆 برنده نهایی",
        "💡 چرا این پست؟",
        "🧪 روش تحلیل من",
        "⚖️ مقایسه ۳ پست",
        "✅ نقاط قوت | ❌ نقاط ضعف",
        "📝 راهنمای پست‌گذاری به این سبک",
        "⏰ ساعت و هشتگ پیشنهادی",
    ]
    return "\n".join(lines)


def analyze_top_posts(candidates, context, model=None, temperature=0.7, max_tokens=2200):
    """تحلیل ۳ پست برتر با AI — خروجی: (متن تحلیل | None, خطا | None)"""
    try:
        client = get_client()
        prompt = build_analysis_prompt(candidates, context)
        resp = client.chat.completions.create(
            model=model or config.AVALAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            return None, "پاسخ خالی از AI برگشت"
        return text, None
    except Exception as e:
        return None, str(e)[:300]


def test_connection(model=None):
    """تست سریع اتصال به AvalAI"""
    try:
        client = get_client()
        resp = client.chat.completions.create(
            model=model or config.AVALAI_MODEL,
            messages=[{"role": "user", "content": "فقط بگو: اوکی"}],
            max_tokens=20,
        )
        return True, (resp.choices[0].message.content or "").strip()[:100]
    except Exception as e:
        return False, str(e)[:300]
