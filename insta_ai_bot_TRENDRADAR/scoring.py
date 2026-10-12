"""الگوریتم وایرال‌اسکور: انتخاب برترین پست‌ها برای تحلیل AI

فرمول (امتیاز ۰ تا ۱۰۰):
  30% قدرت بازدید (reach): لایک نسبت به کل بچ (لگاریتمی)
  25% قدرت گفتگو (conversation): کامنت + نسبت کامنت به لایک
  20% قدرت ویو (views): بازدید ویدیو/ریلز (اگه عکس باشه صفر)
  15% تازگی (freshness): هرچی تازه‌تر، امتیاز بیشتر (نیمه‌عمر ۴۸ ساعت)
  10% کیفیت کپشن (content): طول ایده‌آل کپشن + تعداد هشتگ

نکته: امتیازها «نسبی» هستن (نسبت به همین بچ اسکرپ)، پس
روی هر هشتگی - کوچیک یا بزرگ - درست کار می‌کنه.
"""
import math
from datetime import datetime, timezone

W_REACH = 0.30
W_CONV = 0.25
W_VIEWS = 0.20
W_FRESH = 0.15
W_CONTENT = 0.10


def _parse_time(ts):
    if not ts:
        return None
    try:
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        s = str(ts).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _age_hours(ts):
    dt = _parse_time(ts)
    if not dt:
        return 72.0
    try:
        h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        return max(h, 0.25)
    except Exception:
        return 72.0


def _log_norm(v, vmax):
    if vmax <= 0 or v <= 0:
        return 0.0
    return math.log10(v + 1) / math.log10(vmax + 1)


def compute_virality(items):
    """ورودی: لیست آیتم‌های خام Apify | خروجی: همون لیست + فیلد viral_score (مرتب‌شده نزولی)"""
    if not items:
        return []

    def _num(m, *keys):
        for k in keys:
            v = m.get(k)
            if isinstance(v, (int, float)) and v is not None and v >= 0:
                return v
        return 0

    max_l = max((_num(m, "likesCount", "likeCount", "likes") for m in items), default=0)
    max_c = max((_num(m, "commentsCount", "commentCount", "comments_count") for m in items), default=0)
    max_v = max((_num(m, "videoPlayCount", "igPlayCount", "videoViewCount", "playCount", "views") for m in items), default=0)

    out = []
    for m in items:
        likes = _num(m, "likesCount", "likeCount", "likes")
        comments = _num(m, "commentsCount", "commentCount", "comments_count")
        views = _num(m, "videoPlayCount", "igPlayCount", "videoViewCount", "playCount", "views")
        caption = m.get("caption") or ""
        hashtags = m.get("hashtags") or []

        reach = _log_norm(likes, max_l)
        conv = 0.7 * _log_norm(comments, max_c) + 0.3 * min(comments / max(likes, 1), 1.0)
        vw = _log_norm(views, max_v)
        age = _age_hours(m.get("timestamp"))
        fresh = 1.0 / (1.0 + age / 48.0)

        cap_len = len(caption)
        cap_score = 1.0 if 50 <= cap_len <= 600 else (cap_len / 50 if cap_len < 50 else max(0.4, 600 / max(cap_len, 1)))
        tag_n = len(hashtags) if isinstance(hashtags, list) else 0
        tag_score = 1.0 if 3 <= tag_n <= 15 else (tag_n / 3 if tag_n < 3 else max(0.5, 15 / max(tag_n, 1)))
        content = 0.6 * min(cap_score, 1.0) + 0.4 * min(tag_score, 1.0)

        score = 100 * (W_REACH * reach + W_CONV * conv + W_VIEWS * vw + W_FRESH * fresh + W_CONTENT * content)
        m2 = dict(m)
        m2["viral_score"] = round(score, 1)
        m2["_viral_parts"] = {
            "reach": round(reach * 100, 1), "conversation": round(conv * 100, 1),
            "views": round(vw * 100, 1), "freshness": round(fresh * 100, 1),
            "content": round(content * 100, 1), "age_hours": round(age, 1),
        }
        out.append(m2)

    out.sort(key=lambda x: x["viral_score"], reverse=True)
    return out


def fire_label(score):
    if score >= 80:
        return "🔥🔥 انفجاری"
    if score >= 60:
        return "🔥 داغ"
    if score >= 40:
        return "👍 خوب"
    return "😐 معمولی"
