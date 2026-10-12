"""
🧠 موتور تحلیل ترند
==================

این ماژول **هیچ چیزی** از تلگرام یا هر پیام‌رسان دیگری نمی‌داند.
فقط با `Post` و `HashtagStat` کار می‌کند. این یعنی همان منطق تحلیل
برای اینستاگرام، روبیکا، بله و ... بدون تغییر قابل استفاده است.

معماری متریک‌ها (قابل توسعه)
----------------------------
هر متریک یک کلاس با متد `compute(ctx) -> float` است که عددی بین ۰ و ۱
برمی‌گرداند. امتیاز نهایی = میانگین وزنی متریک‌ها × ۱۰۰.

اضافه‌کردن متریک جدید:

    class SentimentMetric(Metric):
        name = "sentiment"
        label = "احساسات"
        def compute(self, ctx):
            ...
            return 0.75

    analyzer = TrendAnalyzer(metrics=[..., SentimentMetric()],
                             weights={..., "sentiment": 0.1})

نرمال‌سازی
----------
چون هر جامعه‌ی هشتگ اندازه‌ی متفاوتی دارد (یک هشتگ ممکن است ۱۰ پست داشته
باشد و دیگری ۱۰۰۰)، نمرات **نسبی به همان بچ** محاسبه می‌شوند — دقیقاً
همان فلسفه‌ی `scoring.py` پروژه‌ی اینستاگرام. نتیجه: الگوریتم روی
هشتگ‌های کوچک و بزرگ به یک اندازه درست کار می‌کند.
"""
from __future__ import annotations

import logging
import math
import statistics
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .models import HashtagStat, Post, TrendSignal, utcnow

log = logging.getLogger("trendradar.analyzer")


# ═════════════════════════════════════════════════════════════
# زمینه‌ی تحلیل (بین همه‌ی متریک‌ها به اشتراک گذاشته می‌شود)
# ═════════════════════════════════════════════════════════════
@dataclass
class AnalysisContext:
    """
    همه‌ی داده‌ای که یک متریک برای تصمیم‌گیری لازم دارد.

    محاسبات سنگین (مثل max) یک‌بار این‌جا انجام می‌شوند، نه در هر متریک.
    """

    tag: str
    provider: str
    posts: List[Post]
    stat: HashtagStat
    now: datetime = field(default_factory=utcnow)

    # آمار بچ (برای نرمال‌سازی نسبی)
    batch_max_views: float = 0.0
    batch_max_posts_per_hour: float = 1.0
    batch_max_channels: float = 1.0
    batch_max_engagement_rate: float = 1.0

    # سرعت واقعی از تاریخی که ذخیره‌سازی داده (اگر باشد)
    velocity_ratio: float = 1.0

    def log_norm(self, value: float, reference: float) -> float:
        """
        نرمال‌سازی لگاریتمی به بازه‌ی ۰..۱.

        چرا لگاریتمی؟ چون بازدید ۱۰ میلیونی نسبت به ۱ میلیونی «۱۰ برابر
        بهتر» نیست، بلکه فقط ~۱.۱۵ برابر بهتر است. لگاریتم این اغراق را حذف می‌کند.
        """
        if value <= 0 or reference <= 0:
            return 0.0
        if value >= reference:
            return 1.0
        return min(1.0, math.log1p(value) / math.log1p(reference))


# ═════════════════════════════════════════════════════════════
# قرارداد متریک
# ═════════════════════════════════════════════════════════════
class Metric(ABC):
    """
    یک شاخص ترند. هر متریک باید عددی در بازه‌ی [۰، ۱] برگرداند.

    راهنما:
      • از `ctx.log_norm(value, reference)` برای نرمال‌سازی استفاده کن
      • اگر داده‌ی کافی نیست، ۰ برگردان (نه None) — امتیاز را خراب می‌کند
        ولی استثنا نمی‌دهد
    """

    name: str = "metric"
    label: str = "متریک"
    description: str = ""

    @abstractmethod
    def compute(self, ctx: AnalysisContext) -> float:
        """مقدار نرمال‌شده‌ی ۰..۱"""

    def explain(self, ctx: AnalysisContext, value: float) -> str:
        """توضیح فارسی برای نمایش به کاربر — قابل بازنویسی"""
        return f"{self.label}: {value:.2f}"


# ═════════════════════════════════════════════════════════════
# متریک‌های داخلی
# ═════════════════════════════════════════════════════════════
class VelocityMetric(Metric):
    """
    سرعت انتشار — **مهم‌ترین شاخص ترند**.

    منطق: هشتگی که در ۶ ساعت گذشته ۵۰ پست گرفته، ترند است؛ هشتگی که در
    ۳ روز ۵۰ پست گرفته، فقط محبوب است.

    دو جزء دارد:
      1. سرعت خام (پست بر ساعت) نسبت به بچ
      2. نسبت سرعت فعلی به پایه (`velocity_ratio`) — تشخیص «در حال اوج»
    """

    name = "velocity"
    label = "سرعت انتشار"
    description = "تعداد پست در ساعت + شتاب نسبت به گذشته"

    def compute(self, ctx: AnalysisContext) -> float:
        raw = ctx.log_norm(ctx.stat.posts_per_hour, ctx.batch_max_posts_per_hour) if ctx.batch_max_posts_per_hour else 0.0

        # شتاب: نسبت به بازه‌ی قبلی، حداکثر تا ۳ برابر امتیاز کامل
        accel = 0.0
        if ctx.velocity_ratio > 1.0:
            accel = min(1.0, (ctx.velocity_ratio - 1.0) / 2.0)

        return min(1.0, raw * 0.65 + accel * 0.35)

    def explain(self, ctx: AnalysisContext, value: float) -> str:
        v = ctx.stat.posts_per_hour
        if ctx.velocity_ratio > 1.3:
            return f"🔥 در حال اوج‌گرفتن ({v:.1f} پست/ساعت، شتاب {ctx.velocity_ratio:.1f}×)"
        if ctx.velocity_ratio < 0.7:
            return f"📉 در حال افت ({v:.1f} پست/ساعت)"
        return f"➡️ سرعت ثابت ({v:.1f} پست/ساعت)"


class ReachMetric(Metric):
    """
    دامنه‌ی بازدید — چند نفر محتوا را دیدند.

    از میانه استفاده می‌کند (نه میانگین) تا یک پست پرت نتیجه را خراب نکند.
    اگر کانال بازدید نداشته باشد، اندازه‌ی اعضا به‌عنوان جانشین استفاده می‌شود.
    """

    name = "reach"
    label = "دامنه‌ی بازدید"
    description = "میانه‌ی بازدید پست‌ها (یا اندازه‌ی کانال)"

    def compute(self, ctx: AnalysisContext) -> float:
        views = [p.views or (p.channel_members or 0) for p in ctx.posts]
        views = [v for v in views if v > 0]
        if not views:
            return 0.0
        med = statistics.median(views)
        return ctx.log_norm(med, ctx.batch_max_views)

    def explain(self, ctx: AnalysisContext, value: float) -> str:
        if ctx.stat.median_views:
            return f"👁 میانه‌ی بازدید: {ctx.stat.median_views:,.0f}"
        return ""


class EngagementMetric(Metric):
    """
    نرخ تعامل — چند درصد بیننده واکنش نشان داد.

    نرخ تعامل ارزشمندتر از عدد خام است: پستی با ۱۰۰ بازدید و ۱۰ ری‌اکشن
    بهتر از پستی با ۱۰۰۰۰ بازدید و ۵ ری‌اکشن است.
    """

    name = "engagement"
    label = "نرخ تعامل"
    description = "ری‌اکشن + فوروارد + کامنت نسبت به بازدید"

    def compute(self, ctx: AnalysisContext) -> float:
        rates = [p.engagement_rate for p in ctx.posts if p.engagement_rate > 0]
        if not rates:
            return 0.0
        med = statistics.median(rates)
        return ctx.log_norm(med, ctx.batch_max_engagement_rate)

    def explain(self, ctx: AnalysisContext, value: float) -> str:
        if ctx.stat.avg_engagement:
            return f"💬 میانگین تعامل: {ctx.stat.avg_engagement:,.0f} به ازای پست"
        return ""


class SpreadMetric(Metric):
    """
    تنوع منابع — هشتگ در چند کانال مستقل دیده شده؟

    چرا مهم است؟ اگر یک هشتگ فقط در یک کانال باشد، ممکن است ترند آن
    کانال باشد نه ترند واقعی. اگر در ۱۰ کانال مختلف باشد، ترند واقعی است.

    یک جریمه‌ی هوشمند هم دارد: اگر **همه‌ی** پست‌ها از یک کانال باشند،
    ضریب ۰.۵ می‌خورد.
    """

    name = "spread"
    label = "پراکندگی"
    description = "تعداد کانال‌های مستقل که هشتگ را استفاده کرده‌اند"

    def compute(self, ctx: AnalysisContext) -> float:
        n = ctx.stat.channels
        if n <= 0:
            return 0.0
        base = ctx.log_norm(n, max(2, ctx.batch_max_channels))
        if n == 1:
            base *= 0.5                      # تک‌منبع → سیگنال ضعیف
        return base

    def explain(self, ctx: AnalysisContext, value: float) -> str:
        n = ctx.stat.channels
        if n <= 1:
            return "⚠️ فقط یک منبع — احتمال ترند محلی"
        return f"🌐 در {n} کانال مختلف"


class FreshnessMetric(Metric):
    """
    تازگی — با نیم‌عمر نمایی.

    نیم‌عمر پیش‌فرض ۳۶ ساعت: پستی که ۳۶ ساعت پیش منتشر شده، نصفِ
    یک پست تازه امتیاز می‌گیرد.
    """

    name = "freshness"
    label = "تازگی"
    description = "هرچه جدیدتر، بهتر (نیم‌عمر نمایی)"

    def __init__(self, half_life_hours: float = 36.0):
        self.half_life_hours = max(1.0, half_life_hours)

    def compute(self, ctx: AnalysisContext) -> float:
        if not ctx.posts:
            return 0.0
        # تازگی را روی پست‌های اخیر حساب می‌کنیم (میانگین وزنی)
        scores = []
        for p in ctx.posts[:200]:
            age_h = max(0.0, (ctx.now - p.date).total_seconds() / 3600.0)
            scores.append(0.5 ** (age_h / self.half_life_hours))
        return sum(scores) / len(scores) if scores else 0.0

    def explain(self, ctx: AnalysisContext, value: float) -> str:
        if ctx.stat.last_seen:
            age = (ctx.now - ctx.stat.last_seen).total_seconds() / 3600
            if age < 2:
                return "⚡ آخرین پست: همین حالا"
            if age < 24:
                return f"🕐 آخرین پست: {age:.0f} ساعت پیش"
            return f"🕐 آخرین پست: {age/24:.0f} روز پیش"
        return ""


# ═════════════════════════════════════════════════════════════
# تحلیل‌گر
# ═════════════════════════════════════════════════════════════
class TrendAnalyzer:
    """
    تحلیل‌گر ترند — از لیست پست‌ها، رتبه‌بندی هشتگ‌ها را می‌سازد.

    نمونه:
        analyzer = TrendAnalyzer()
        signals = analyzer.analyze(posts, window_hours=72)
        for s in signals[:5]:
            print(s.tag, s.score, s.reason)
    """

    def __init__(
        self,
        metrics: Optional[Sequence[Metric]] = None,
        weights: Optional[Dict[str, float]] = None,
    ):
        self.metrics: List[Metric] = list(metrics) if metrics else self.default_metrics()
        self.weights: Dict[str, float] = dict(weights) if weights else {
            "velocity": 0.30, "reach": 0.22, "engagement": 0.20,
            "spread": 0.15, "freshness": 0.13,
        }
        self._normalize_weights()

    # ── پیکربندی ─────────────────────────────────────────
    @staticmethod
    def default_metrics() -> List[Metric]:
        """متریک‌های پیش‌فرض — برای شروع کافی‌اند"""
        return [VelocityMetric(), ReachMetric(), EngagementMetric(),
                SpreadMetric(), FreshnessMetric()]

    def _normalize_weights(self) -> None:
        """نرمال‌سازی وزن‌ها به جمع ۱ + حذف وزن متریک‌های ناموجود"""
        names = {m.name for m in self.metrics}
        w = {k: max(0.0, v) for k, v in self.weights.items() if k in names}
        total = sum(w.values())
        if total <= 0:
            w = {m.name: 1.0 / len(self.metrics) for m in self.metrics}
            total = 1.0
        self.weights = {k: v / total for k, v in w.items()}

    def add_metric(self, metric: Metric, weight: float = 0.1) -> "TrendAnalyzer":
        """افزودن متریک جدید در زمان اجرا (الگوی Builder)"""
        self.metrics.append(metric)
        self.weights[metric.name] = weight
        self._normalize_weights()
        return self

    def from_settings(self, weights_obj: Any) -> "TrendAnalyzer":
        """بارگذاری وزن‌ها از `ScoringWeights` تنظیمات"""
        if weights_obj is None:
            return self
        try:
            self.weights = dict(weights_obj.normalized())
            self._normalize_weights()
        except Exception as e:
            log.debug("بارگذاری وزن‌ها ناموفق: %s", e)
        return self

    # ── اجرای تحلیل ──────────────────────────────────────
    def analyze(
        self,
        posts: Sequence[Post],
        *,
        window_hours: int = 72,
        min_posts: int = 2,
        top_n: int = 20,
        velocity_lookup: Optional[Dict[str, float]] = None,
        provider_name: str = "",
    ) -> List[TrendSignal]:
        """
        تحلیل پست‌ها و برگرداندن هشتگ‌های ترند به ترتیب امتیاز.

        :param min_posts:        حداقل تعداد پست برای اینکه هشتگ بررسی شود
        :param velocity_lookup:  {tag: نسبت_شتاب} از تاریخچه‌ی ذخیره‌شده
        :param provider_name:    نام نمایشی پرووایدر (برای گزارش)
        """
        if not posts:
            return []

        now = utcnow()
        since = now - timedelta(hours=max(1, window_hours))
        recent = [p for p in posts if p.date >= since] or list(posts)

        # ۱) گروه‌بندی بر اساس هشتگ
        by_tag: Dict[str, List[Post]] = {}
        for p in recent:
            for tag in p.hashtags:
                by_tag.setdefault(tag, []).append(p)

        if not by_tag:
            return []

        # ۲) ساخت آمار خام
        stats: Dict[str, HashtagStat] = {}
        for tag, tposts in by_tag.items():
            if len(tposts) < min_posts:
                continue
            stats[tag] = self._build_stat(tag, tposts, window_hours)

        if not stats:
            return []

        # ۳) آمار بچ برای نرمال‌سازی نسبی
        batch_max_views = max((s.median_views for s in stats.values()), default=1.0) or 1.0
        batch_max_pph = max((s.posts_per_hour for s in stats.values()), default=1.0) or 1.0
        batch_max_channels = max((s.channels for s in stats.values()), default=1.0) or 1.0
        batch_max_er = max(
            (statistics.median([p.engagement_rate for p in by_tag[t]]) if by_tag[t] else 0.0
             for t in stats),
            default=1.0,
        ) or 1.0

        # ۴) امتیازدهی
        signals: List[TrendSignal] = []
        for tag, stat in stats.items():
            tposts = sorted(by_tag[tag], key=lambda p: p.views or p.channel_members or 0, reverse=True)
            ctx = AnalysisContext(
                tag=tag,
                provider=stat.provider,
                posts=tposts,
                stat=stat,
                now=now,
                batch_max_views=batch_max_views,
                batch_max_posts_per_hour=batch_max_pph,
                batch_max_channels=batch_max_channels,
                batch_max_engagement_rate=batch_max_er,
                velocity_ratio=(velocity_lookup or {}).get(tag, 1.0),
            )

            breakdown: Dict[str, float] = {}
            score = 0.0
            for m in self.metrics:
                try:
                    v = float(m.compute(ctx))
                except Exception as e:
                    log.debug("متریک %s روی #%s خطا داد: %s", m.name, tag, e)
                    v = 0.0
                v = max(0.0, min(1.0, v))
                breakdown[m.name] = v
                score += v * self.weights.get(m.name, 0.0)

            signals.append(
                TrendSignal(
                    tag=tag,
                    provider=stat.provider,
                    provider_name=provider_name,
                    score=round(score * 100, 2),
                    stat=stat,
                    breakdown=breakdown,
                    weights=dict(self.weights),
                    top_posts=tposts[:5],
                    top_channels=self._top_channels(tposts, limit=3),
                    reason=self._build_reason(ctx, breakdown),
                )
            )

        # ۵) رتبه‌بندی
        signals.sort(key=lambda s: s.score, reverse=True)
        for i, s in enumerate(signals, 1):
            s.rank = i
        return signals[:top_n]

    # ── کمکی‌ها ──────────────────────────────────────────
    @staticmethod
    def _build_stat(tag: str, posts: List[Post], window_hours: int) -> HashtagStat:
        """ساخت آمار تجمیعی از لیست پست‌های یک هشتگ"""
        views = [p.views or (p.channel_members or 0) for p in posts]
        views_pos = [v for v in views if v > 0]
        dates = [p.date for p in posts if p.date]
        channels = {p.channel_uid for p in posts}

        first = min(dates) if dates else None
        last = max(dates) if dates else None

        # پنجره‌ی مؤثر: از اولین تا آخرین پست، محدود به پنجره‌ی درخواستی
        if first and last and last > first:
            span = (last - first).total_seconds() / 3600.0
        else:
            span = float(window_hours)
        span = max(0.5, min(span, float(window_hours)))

        return HashtagStat(
            tag=tag,
            provider=posts[0].provider if posts else "",
            posts=len(posts),
            channels=len(channels),
            total_views=float(sum(views)),
            total_engagement=float(sum(p.engagement for p in posts)),
            median_views=float(statistics.median(views_pos)) if views_pos else 0.0,
            max_views=int(max(views_pos)) if views_pos else 0,
            first_seen=first,
            last_seen=last,
            window_hours=span,
            sample_post_uids=[p.uid for p in posts[:10]],
        )

    @staticmethod
    def _top_channels(posts: List[Post], limit: int = 3) -> List[str]:
        """کانال‌هایی که بیشترین پست با این هشتگ داشته‌اند"""
        counts: Dict[str, int] = {}
        for p in posts:
            key = p.channel_title or p.channel_uid
            counts[key] = counts.get(key, 0) + 1
        return [k for k, _ in sorted(counts.items(), key=lambda x: -x[1])[:limit]]

    def _build_reason(self, ctx: AnalysisContext, breakdown: Dict[str, float]) -> str:
        """ساخت توضیح فارسی از متریک‌ها — شفافیت برای کاربر"""
        parts: List[str] = []
        # متریک‌هایی که بالاترین سهم را داشتند اول می‌آیند
        order = sorted(self.metrics, key=lambda m: breakdown.get(m.name, 0) * self.weights.get(m.name, 0), reverse=True)
        for m in order[:3]:
            try:
                txt = m.explain(ctx, breakdown.get(m.name, 0.0))
            except Exception:
                txt = ""
            if txt:
                parts.append(txt)

        stat_txt = f"📊 {ctx.stat.posts} پست"
        if ctx.stat.avg_views:
            stat_txt += f" | میانگین {ctx.stat.avg_views:,.0f} بازدید"
        parts.append(stat_txt)
        return "\n".join(parts)


# ─────────────────────────────────────────────────────────────
# تابع میان‌بر
# ─────────────────────────────────────────────────────────────
def analyze_posts(
    posts: Sequence[Post],
    *,
    window_hours: int = 72,
    top_n: int = 20,
    weights: Optional[Dict[str, float]] = None,
    velocity_lookup: Optional[Dict[str, float]] = None,
) -> List[TrendSignal]:
    """تحلیل سریع بدون ساختن شیء — برای استفاده‌های ساده"""
    return TrendAnalyzer(weights=weights).analyze(
        posts,
        window_hours=window_hours,
        top_n=top_n,
        velocity_lookup=velocity_lookup,
    )
