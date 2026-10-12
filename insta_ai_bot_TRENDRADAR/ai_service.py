"""اتصال به AvalAI (سازگار با OpenAI) + ساخت پرامپت تحلیل وایرال"""
import config

_client = None


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
