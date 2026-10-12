"""
🏭 خط لوله‌ی ترند‌یابی
====================

مسیر کامل یک اسکن:

    ۱. فهرست کانال‌های هدف
       ├─ کانال‌هایی که عضو هستیم (از دیتابیس یا از خود پیام‌رسان) ← بی‌خطر
       └─ کشف کانال‌های جدید با عبارت جستجو                        ← اگر خواسته شده

    ۲. (اختیاری) عضویت در کانال‌های جدید
       └─ عبور از گارد ضد‌بن: فاصله‌ی تصادفی + سقف روزانه

    ۳. جمع‌آوری پست‌ها
       ├─ جستجوی هشتگ داخل هر کانال (دقیق‌ترین)
       ├─ جستجوی سراسری (بی‌نیاز از عضو بودن)
       └─ جستجو در تاریخ کانال‌های عضو (برای کانال‌های خصوصی)

    ۴. ذخیره در دیتابیس + به‌روزرسانی سری زمانی

    ۵. تحلیل: امتیازدهی هشتگ‌ها با متریک‌ها

    ۶. گزارش نهایی

نکته‌ی مهم درباره‌ی خطاها
-------------------------
هیچ خطای یک کانال نباید کل اسکن را متوقف کند. هر خطا در `report.errors`
ثبت می‌شود و بقیه ادامه می‌یابند. فقط خطاهای سطح‌اکانت (RateLimited،
DailyCapReached، AuthRequired) جریان را متوقف می‌کنند.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Set

from .analyzer import TrendAnalyzer
from .errors import (
    AuthRequired,
    DailyCapReached,
    ProviderError,
    RateLimited,
    TrendRadarError,
)
from .models import ChannelRef, Post, RunReport, SearchQuery, utcnow
from .providers.base import Capability, MessengerProvider
from .storage import TrendStorage

log = logging.getLogger("trendradar.pipeline")


class TrendPipeline:
    """
    ارکستریتور یک اسکن کامل.

    نمونه:
        pipeline = TrendPipeline(provider, storage, analyzer)
        report = await pipeline.run(SearchQuery(hashtags=["ترند"]))
    """

    def __init__(
        self,
        provider: MessengerProvider,
        storage: TrendStorage,
        analyzer: Optional[TrendAnalyzer] = None,
        *,
        settings=None,
    ):
        self.provider = provider
        self.storage = storage
        self.analyzer = analyzer or TrendAnalyzer()
        self.settings = settings

    # ═════════════════════════════════════════════════════════
    # اجرای کامل
    # ═════════════════════════════════════════════════════════
    async def run(self, query: SearchQuery) -> RunReport:
        """یک اسکن کامل ترند"""

        report = RunReport(run_id=uuid.uuid4().hex[:12], query=query)
        tags = query.normalized_hashtags()
        since = utcnow() - timedelta(hours=max(1, query.window_hours))
        provider_name = self.provider.name

        log.info(
            "🔍 شروع اسکن [%s] | هشتگ‌ها: %s | پنجره: %dh",
            provider_name, ", ".join(f"#{t}" for t in tags) or "—", query.window_hours,
        )

        try:
            # ── مرحله ۱: اتصال ──
            await self.provider.connect()
        except AuthRequired as e:
            report.errors.append(f"احراز هویت لازم است: {e}")
            report.finished_at = utcnow()
            return report
        except Exception as e:
            report.errors.append(f"اتصال ناموفق: {type(e).__name__}: {e}")
            report.finished_at = utcnow()
            return report

        # ── مرحله ۲: تعیین کانال‌های هدف ──
        try:
            channels = await self._resolve_target_channels(query, report)
        except (RateLimited, DailyCapReached) as e:
            report.errors.append(str(e))
            channels = self._safe_fallback_channels(provider_name)
        except Exception as e:
            report.errors.append(f"تعیین کانال‌های هدف ناموفق: {e}")
            channels = []

        report.channels_scanned = len(channels)
        log.info("📡 کانال‌های هدف: %d", len(channels))

        # ── مرحله ۳: جمع‌آوری پست‌ها ──
        posts = await self._collect(query, channels, tags, since, report)
        report.posts_collected = len(posts)
        log.info("📥 پست‌های جمع‌آوری‌شده: %d", len(posts))

        if not posts:
            report.errors.append(
                "هیچ پستی پیدا نشد. دلایل محتمل: هشتگ کم‌کاربرد است، کانال‌ها "
                "دسترسی ندارند، یا سقف روزانه‌ی جستجو پر شده."
            )
            report.finished_at = utcnow()
            self.storage.save_run(report)
            return report

        # ── مرحله ۴: ذخیره ──
        try:
            self.storage.save_posts(posts)
        except Exception as e:
            report.errors.append(f"ذخیره‌سازی ناموفق: {e}")

        # ── مرحله ۵: تحلیل ──
        try:
            velocity_lookup = self._velocity_lookup(tags, provider_name)
            report.signals = self.analyzer.analyze(
                posts,
                window_hours=query.window_hours,
                top_n=20,
                velocity_lookup=velocity_lookup,
                provider_name=self.provider.display_name,
            )
            log.info("🏆 ترندهای شناسایی‌شده: %d", len(report.signals))
        except Exception as e:
            log.exception("تحلیل ناموفق")
            report.errors.append(f"تحلیل ناموفق: {e}")

        # ── مرحله ۶: ذخیره‌ی وضعیت و گزارش ──
        report.finished_at = utcnow()
        report.provider_stats[provider_name] = {
            "channels": report.channels_scanned,
            "discovered": report.channels_discovered,
            "joined": report.channels_joined,
            "posts": report.posts_collected,
        }
        try:
            self.storage.save_run(report)
            self._persist_provider_state()
        except Exception as e:
            log.debug("ذخیره‌ی گزارش ناموفق: %s", e)

        log.info(
            "✅ اسکن تمام شد (%.1fs) | %d کانال | %d پست | %d ترند",
            report.duration_sec, report.channels_scanned,
            report.posts_collected, len(report.signals),
        )
        return report

    # ═════════════════════════════════════════════════════════
    # مرحله ۲: کانال‌های هدف
    # ═════════════════════════════════════════════════════════
    async def _resolve_target_channels(self, query: SearchQuery, report: RunReport) -> List[ChannelRef]:
        """
        تعیین کانال‌هایی که باید در آن‌ها جستجو کنیم.

        اولویت:
          1. کانال‌های عضو (بی‌خطر، سریع)
          2. کانال‌های کشف‌شده از اجراهای قبلی
          3. کشف جدید با عبارات جستجو
        """
        out: List[ChannelRef] = []
        seen: Set[str] = set()

        def add(ch: ChannelRef) -> bool:
            if ch.uid in seen or ch.uid in query.exclude_channel_uids:
                return False
            if ch.is_scam or ch.is_fake:
                return False
            if query.min_members and ch.members is not None and ch.members < query.min_members:
                return False
            seen.add(ch.uid)
            out.append(ch)
            return True

        # ── ۱) کانال‌های عضو ──
        joined: List[ChannelRef] = []
        try:
            if self.provider.supports(Capability.LIST_JOINED):
                joined = await self.provider.list_joined(limit=query.max_channels * 4)
                self.storage.upsert_channels(joined)
        except (RateLimited, DailyCapReached):
            raise
        except Exception as e:
            log.debug("لیست کانال‌های عضو ناموفق: %s", e)

        # اگر از پیام‌رسان چیزی نیامد، از دیتابیس بخوان
        if not joined:
            joined = self.storage.get_joined_channels(self.provider.name, limit=200)

        for ch in joined:
            add(ch)

        # اگر فقط کانال‌های عضو را می‌خواهیم، همین‌جا تمام
        if query.joined_only:
            return out[: query.max_channels]

        # ── ۲) کشف جدید ──
        if query.discover_channels and self.provider.supports(Capability.DISCOVER_CHANNELS):
            terms = list(query.hashtags) + list(query.keywords)
            for term in terms[:5]:                       # حداکثر ۵ عبارت برای صرفه‌جویی
                if len(out) >= query.max_channels * 2:
                    break
                try:
                    found = await self.provider.discover_channels(
                        term,
                        limit=max(5, query.max_channels),
                        min_members=query.min_members,
                        exclude=list(seen),
                    )
                except (RateLimited, DailyCapReached):
                    raise
                except Exception as e:
                    report.errors.append(f"کشف «{term}» ناموفق: {e}")
                    continue

                new_ones = [c for c in found if add(c)]
                report.channels_discovered += len(new_ones)
                if new_ones:
                    self.storage.upsert_channels(new_ones)

        # ── ۳) کشف خوشه‌ای: کانال‌های مشابه بهترین‌ها ──
        if (query.discover_channels
                and self.provider.supports(Capability.DISCOVER_RECOMMENDED)
                and out):
            seeds = sorted([c for c in out if c.members], key=lambda c: -(c.members or 0))[:2]
            for seed in seeds:
                if len(out) >= query.max_channels * 3:
                    break
                try:
                    similar = await self.provider.discover_recommended(
                        seed, limit=max(3, query.max_channels // 2)
                    )
                    new_ones = [c for c in similar if add(c)]
                    report.channels_discovered += len(new_ones)
                    if new_ones:
                        self.storage.upsert_channels(new_ones)
                except (RateLimited, DailyCapReached):
                    raise
                except Exception as e:
                    log.debug("مشابه‌های %s ناموفق: %s", seed.title, e)

        # ── ۴) انتخاب بهترین‌ها و (اختیاری) عضویت ──
        if self.settings is not None:
            out = self._rank_and_limit(out, query)

        if query.auto_join and self.provider.supports(Capability.JOIN_CHANNEL):
            out = await self._join_targets(out, report, query)

        # ── ۵) پرکردن آمار کانال‌ها (فقط تعداد محدود، برای امان) ──
        if self.provider.supports(Capability.GET_STATS):
            enriched: List[ChannelRef] = []
            for ch in out[: min(10, len(out))]:
                try:
                    ch2 = await self.provider.refresh_stats(ch)
                    enriched.append(ch2 if ch2 else ch)
                except RateLimited:
                    enriched.append(ch)
                    break
                except Exception:
                    enriched.append(ch)
            enriched.extend(out[len(enriched):])
            out = enriched
            self.storage.upsert_channels(out)

        return out[: query.max_channels]

    def _rank_and_limit(self, channels: List[ChannelRef], query: SearchQuery) -> List[ChannelRef]:
        """
        مرتب‌سازی کانال‌ها بر اساس ارزش و محدود کردن تعداد.

        معیار ارزش: عضو بودن > اندازه > عمومی بودن (یوزرنیم داشتن)
        """
        def value(ch: ChannelRef) -> float:
            v = 0.0
            if ch.joined:
                v += 1000                       # عضو بودن = بی‌خطر بودن
            if ch.username:
                v += 200                        # قابل جستجو در سراسری
            if ch.members:
                import math

                v += min(300.0, math.log10(max(1, ch.members)) * 60)
            if ch.verified:
                v += 60
            if ch.kind.value == "broadcast":
                v += 40
            if ch.join_request:
                v -= 50
            return -v

        return sorted(channels, key=value)

    async def _join_targets(
        self, channels: List[ChannelRef], report: RunReport, query: SearchQuery
    ) -> List[ChannelRef]:
        """عضویت در کانال‌های هدف — با گارد و مدیریت خطا"""
        out = list(channels)
        to_join = [c for c in channels if not c.joined]

        for ch in to_join:
            try:
                joined = await self.provider.join_channel(ch)
                report.channels_joined += 1
                # در لیست جایگزین کن
                for i, c in enumerate(out):
                    if c.uid == ch.uid:
                        out[i] = joined
                        break
                self.storage.upsert_channel(joined)
            except DailyCapReached as e:
                report.errors.append(f"سقف عضویت: {e}")
                break
            except RateLimited as e:
                report.errors.append(f"محدودیت نرخ در عضویت: {e}")
                break
            except TrendRadarError as e:
                log.info("عضویت در «%s» ممکن نشد: %s", ch.title, e)
            except Exception as e:
                log.debug("خطای عضویت %s: %s", ch.uid, e)

        return out

    def _safe_fallback_channels(self, provider_name: str) -> List[ChannelRef]:
        """کانال‌های امن از دیتابیس — وقتی کشف شکست می‌خورد"""
        try:
            return self.storage.get_joined_channels(provider_name, limit=20)
        except Exception:
            return []

    # ═════════════════════════════════════════════════════════
    # مرحله ۳: جمع‌آوری پست‌ها
    # ═════════════════════════════════════════════════════════
    async def _collect(
        self,
        query: SearchQuery,
        channels: List[ChannelRef],
        tags: List[str],
        since: datetime,
        report: RunReport,
    ) -> List[Post]:
        """
        جمع‌آوری پست‌ها با سه استراتژی، به ترتیب اولویت.

        بودجه‌ی زمانی: هر هشتگ سهم محدودی از `limit_per_tag` می‌گیرد تا
        یک هشتگ پرحجم بقیه را گرسنه نگذارد.
        """
        posts: List[Post] = []
        seen: Set[str] = set()

        def extend(new_posts: Iterable[Post]) -> None:
            for p in new_posts:
                if p.uid not in seen:
                    seen.add(p.uid)
                    posts.append(p)

        # ── الف) جستجوی هشتگ داخل کانال‌ها ──
        if channels and self.provider.supports(Capability.SEARCH_IN_CHANNEL):
            for tag in tags:
                try:
                    found = await self.provider.search_hashtag(
                        tag, channels=channels, limit=query.limit_per_tag, since=since
                    )
                    extend(found)
                    log.info("  #%s → %d پست", tag, len(found))
                except (RateLimited, DailyCapReached) as e:
                    report.errors.append(f"جستجوی #{tag} متوقف شد: {e}")
                    break
                except Exception as e:
                    report.errors.append(f"جستجوی #{tag} ناموفق: {type(e).__name__}: {e}")

        # ── ب) جستجو در تاریخ کانال‌های عضو (اگر نتیجه کم بود) ──
        joined = [c for c in channels if c.joined]
        search_history = getattr(self.provider, "search_in_channels_history", None)
        if joined and callable(search_history) and len(posts) < query.limit_per_tag * len(tags) // 2:
            for tag in tags:
                try:
                    found = await search_history(tag, joined[:10], limit=query.limit_per_tag // 2, since=since)
                    extend(found)
                except (RateLimited, DailyCapReached):
                    break
                except Exception as e:
                    log.debug("جستجوی تاریخ برای #%s ناموفق: %s", tag, e)

        return posts

    # ═════════════════════════════════════════════════════════
    # مرحله ۵: تاریخچه برای محاسبه‌ی شتاب
    # ═════════════════════════════════════════════════════════
    def _velocity_lookup(self, tags: List[str], provider: str) -> Dict[str, float]:
        """
        محاسبه‌ی نسبت سرعت فعلی به پایه برای هر هشتگ.

        این چیزی است که «ترند در حال اوج گرفتن» را از «اوج گذشته» جدا می‌کند.
        """
        out: Dict[str, float] = {}
        for tag in tags:
            try:
                out[tag] = self.storage.hashtag_velocity(tag, recent_hours=12, baseline_hours=72)
            except Exception as e:
                log.debug("محاسبه‌ی سرعت #%s ناموفق: %s", tag, e)
        return out

    def _persist_provider_state(self) -> None:
        """ذخیره‌ی وضعیت گاردها (سقف روزانه) بین اجراها"""
        try:
            state = self.provider.export_state()
            if state:
                self.storage.save_provider_state(self.provider.name, state)
        except Exception as e:
            log.debug("ذخیره‌ی وضعیت پرووایدر ناموفق: %s", e)
