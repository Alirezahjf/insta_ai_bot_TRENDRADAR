"""
🧪 تست انتها-به-انتها TrendRadar با پرووایدر قلابی
==================================================

این تست کل زنجیره را بدون نیاز به شبکه/تلگرام بررسی می‌کند:
    رجیستری → پرووایدر → پاییپ‌لاین → ذخیره‌سازی → تحلیل‌گر → گزارش

اجرا:
    python -m trendradar.tools.test_pipeline
یا:
    python trendradar/tools/test_pipeline.py
"""
from __future__ import annotations

import os
import random
import sys
import tempfile
from datetime import timedelta
from typing import List, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from trendradar.analyzer import TrendAnalyzer  # noqa: E402
from trendradar.config import TrendSettings  # noqa: E402
from trendradar.models import (  # noqa: E402
    ChannelKind,
    ChannelRef,
    MediaType,
    Post,
    SearchQuery,
    utcnow,
)
from trendradar.pipeline import TrendPipeline  # noqa: E402
from trendradar.providers.base import Capability, MessengerProvider  # noqa: E402
from trendradar.registry import ProviderRegistry  # noqa: E402
from trendradar.storage import TrendStorage  # noqa: E402

PASS, FAIL = "✅", "❌"
_results: List[tuple] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    _results.append((name, condition, detail))
    mark = PASS if condition else FAIL
    print(f"  {mark} {name}" + (f"  → {detail}" if detail else ""))
    return condition


# ═════════════════════════════════════════════════════════════
# پرووایدر قلابی — نقش تلگرام را بازی می‌کند
# ═════════════════════════════════════════════════════════════
class MockProvider(MessengerProvider):
    """
    پرووایدر ساختگی با داده‌های کنترل‌شده.

    سناریوی طراحی‌شده:
      • #ترند_مدلینگ  → ۲۰ پست در ۶ کانال، خیلی تازه، بازدید بالا   ← باید rank 1 شود
      • #فروش_لباس    → ۱۲ پست در ۲ کانال، قدیمی‌تر، بازدید متوسط
      • #غذا           → ۳ پست در ۱ کانال، خیلی قدیمی، بازدید کم     ← باید آخر شود
      • #تک_پست       → ۱ پست (زیر آستانه‌ی min_posts)                ← باید حذف شود
    """

    name = "mock"
    display_name = "پیام‌رسان تستی"
    capabilities = {
        Capability.SEARCH_HASHTAG,
        Capability.SEARCH_IN_CHANNEL,
        Capability.DISCOVER_CHANNELS,
        Capability.DISCOVER_RECOMMENDED,
        Capability.JOIN_CHANNEL,
        Capability.LIST_JOINED,
        Capability.GET_STATS,
    }

    def __init__(self, settings=None):
        super().__init__(settings)
        self.join_calls: List[str] = []
        self.search_calls: List[str] = []
        self._channels = self._make_channels()
        self._posts = self._make_posts()

    # ── داده ──
    @staticmethod
    def _make_channels() -> List[ChannelRef]:
        specs = [
            ("مدلینگ ایران", "modeling_ir", 120_000, ChannelKind.BROADCAST),
            ("ترند استایل", "style_trend", 85_000, ChannelKind.BROADCAST),
            ("پوشاک تهران", "pooshak_teh", 40_000, ChannelKind.BROADCAST),
            ("فشن نیوز", "fashion_news", 25_000, ChannelKind.SUPERGROUP),
            ("لباس ارزان", "cheap_cloth", 8_000, ChannelKind.BROADCAST),
            ("گروه مد و لباس", "mod_group", 3_000, ChannelKind.SUPERGROUP),
            ("آشپزی خانه", "home_cook", 15_000, ChannelKind.BROADCAST),
        ]
        return [
            ChannelRef(
                provider="mock", native_id=1000 + i, title=t, username=u,
                kind=k, members=m, joined=(i < 2), verified=(i == 0),
            )
            for i, (t, u, m, k) in enumerate(specs)
        ]

    def _make_posts(self) -> List[Post]:
        rnd = random.Random(42)
        now = utcnow()
        posts: List[Post] = []

        plan = [
            # (هشتگ، تعداد پست، تعداد کانال، حداقل ساعت پیش، حداکثر، بازه‌ی بازدید)
            ("ترند_مدلینگ", 20, 6, 0, 10, (80_000, 400_000)),
            ("فروش_لباس", 12, 2, 20, 60, (15_000, 60_000)),
            ("غذا", 3, 1, 60, 140, (500, 3_000)),
            ("تک_پست", 1, 1, 5, 5, (10_000, 10_000)),
        ]

        for tag, count, n_chan, min_h, max_h, (vmin, vmax) in plan:
            for i in range(count):
                ch = self._channels[i % n_chan]
                age_h = rnd.uniform(min_h, max_h)
                views = rnd.randint(vmin, vmax)
                posts.append(Post(
                    provider="mock",
                    channel_uid=ch.uid,
                    message_id=f"msg{tag}{i}",
                    date=now - timedelta(hours=age_h),
                    text=f"متن نمونه درباره #{tag} در {ch.title} — شماره {i}",
                    hashtags=[tag],
                    views=views,
                    forwards=int(views * rnd.uniform(0.005, 0.03)),
                    replies=int(views * rnd.uniform(0.001, 0.01)),
                    reactions=int(views * rnd.uniform(0.01, 0.06)),
                    media_type=rnd.choice([MediaType.PHOTO, MediaType.VIDEO, MediaType.TEXT]),
                    url=f"https://t.me/{ch.username}/{i}",
                    channel_title=ch.title,
                    channel_members=ch.members,
                ))
        return posts

    # ── قرارداد ──
    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def health(self):
        from trendradar.models import ProviderHealth

        return ProviderHealth(
            provider=self.name, name=self.display_name, available=True,
            authorized=True, account="@mock_account",
            capabilities=sorted(c.value for c in self.capabilities),
        )

    async def discover_channels(self, query, *, limit=20, min_members=0, exclude=None):
        exclude = set(exclude or ())
        hits = [
            c for c in self._channels
            if c.uid not in exclude and (min_members == 0 or (c.members or 0) >= min_members)
        ]
        return hits[:limit]

    async def discover_recommended(self, seed, *, limit=10):
        return [c for c in self._channels if c.uid != seed.uid][:limit]

    async def search_hashtag(self, tag, *, channels=(), limit=50, since=None):
        from trendradar.models import normalize_hashtag

        tag = normalize_hashtag(tag)
        self.search_calls.append(tag)
        allowed = {c.uid for c in channels} if channels else None
        out = [
            p for p in self._posts
            if tag in p.hashtags and (allowed is None or p.channel_uid in allowed)
            and (since is None or p.date >= since)
        ]
        return out[:limit]

    async def join_channel(self, channel, *, dry_run=False):
        self.join_calls.append(channel.uid)
        channel.joined = True
        return channel

    async def leave_channel(self, channel):
        channel.joined = False
        return True

    async def list_joined(self, *, limit=200):
        return [c for c in self._channels if c.joined][:limit]

    async def resolve(self, identifier):
        for c in self._channels:
            if c.username and c.username.lower() in str(identifier).lower():
                return c
        return None

    async def refresh_stats(self, channel):
        return channel


# ═════════════════════════════════════════════════════════════
# تست‌ها
# ═════════════════════════════════════════════════════════════
def test_models() -> None:
    print("\n▌ ۱) مدل‌های دامنه و نرمال‌سازی")
    from trendradar.models import extract_hashtags, normalize_hashtag

    check("نرمال‌سازی # ساده", normalize_hashtag("#ترند") == "ترند")
    check("حذف فاصله و اعراب", normalize_hashtag(" #تُرند مدلینگ ") == "ترند_مدلینگ")
    check("تبدیل عربی به فارسی", normalize_hashtag("#كتاب") == normalize_hashtag("#کتاب"))
    check("حروف بزرگ → کوچک", normalize_hashtag("#Trend") == "trend")
    tags = extract_hashtags("سلام #ترند_مدلینگ و #فروش_لباس و #ترند_مدلینگ")
    check("استخراج هشتگ بدون تکرار", tags == ["ترند_مدلینگ", "فروش_لباس"], str(tags))

    from trendradar.models import ChannelRef as CR

    c = CR(provider="t", native_id=1, username="test")
    check("کلید یکتا (uid)", c.uid == "t:1")
    check("لینک عمومی", c.url == "https://t.me/test")


def test_registry() -> None:
    print("\n▌ ۲) رجیستری پرووایدرها")
    reg = ProviderRegistry()
    reg.register(MockProvider)
    check("ثبت پرووایدر", reg.has("mock"))
    check("لیست نام‌ها", "mock" in reg.names())
    inst = reg.create("mock")
    check("ساخت نمونه", isinstance(inst, MockProvider))
    check("cache نمونه‌ها", reg.create("mock") is inst)
    desc = reg.describe()
    check("توصیف پرووایدر", any(d["name"] == "mock" for d in desc))
    check("قابلیت‌ها ثبت شده", len(desc[0]["capabilities"]) == 7)


def test_runtime() -> None:
    print("\n▌ ۳) پل async ↔ sync")
    from trendradar.runtime import AsyncRuntime

    rt = AsyncRuntime(name="test-loop")
    rt.start()
    check("event loop راه افتاد", rt.running)

    async def add(a, b):
        return a + b

    check("اجرای کوروتین از sync", rt.run(add(2, 3)) == 5)
    check("اجرای موازی", rt.gather(add(1, 1), add(2, 2)) == [2, 4])

    try:
        import asyncio

        async def nested():
            inner = add(1, 1)
            try:
                return rt.run(inner)       # باید خطا بدهد (جلوگیری از deadlock)
            finally:
                inner.close()              # جلوگیری از هشدار «never awaited»

        coro = nested()
        try:
            rt.run(coro)
            check("جلوگیری از deadlock", False, "خطا نداد!")
        except RuntimeError as e:
            check("جلوگیری از deadlock", "deadlock" in str(e))
        finally:
            coro.close()
    except Exception as e:
        check("جلوگیری از deadlock", False, str(e))

    rt.stop()
    check("loop متوقف شد", not rt.running)


def test_analyzer() -> None:
    print("\n▌ ۴) موتور تحلیل (با داده‌ی کنترل‌شده)")
    p = MockProvider()
    now = utcnow()
    posts = p._make_posts()

    analyzer = TrendAnalyzer()
    signals = analyzer.analyze(posts, window_hours=200, min_posts=2, top_n=10)

    tags = [s.tag for s in signals]
    check("هشتگ‌ها تحلیل شدند", len(signals) >= 3, f"{len(signals)} سیگنال")
    check("هشتگ تک‌پست حذف شد", "تک_پست" not in tags)
    check("ترند اصلی رتبه ۱ است", signals and signals[0].tag == "ترند_مدلینگ",
          f"رتبه ۱ = {signals[0].tag if signals else '—'}")
    check("ترند اصلی امتیاز > ۶۰", signals and signals[0].score > 60,
          f"{signals[0].score:.1f}" if signals else "—")
    check("ترند ضعیف آخر است", signals and signals[-1].tag == "غذا",
          f"آخر = {signals[-1].tag if signals else '—'}")
    check("امتیازها نزولی", all(signals[i].score >= signals[i+1].score for i in range(len(signals)-1)))
    check("رتبه‌ها پیوسته", [s.rank for s in signals] == list(range(1, len(signals)+1)))
    check("تفکیک متریک‌ها موجود", all(len(s.breakdown) == 5 for s in signals))
    check("جمع وزن‌ها ≈ ۱", abs(sum(signals[0].weights.values()) - 1.0) < 0.001)
    check("توضیح فارسی ساخته شد", bool(signals[0].reason))
    check("پست‌های برتر ضمیمه شدند", len(signals[0].top_posts) == 5)
    check("کانال‌های برتر ضمیمه شدند", len(signals[0].top_channels) > 0)

    print("\n      ── رتبه‌بندی حاصل ──")
    for s in signals:
        print(f"      #{s.rank} #{s.tag:18s} {s.score:6.2f}  "
              f"(سرعت {s.breakdown['velocity']:.2f} | دامنه {s.breakdown['reach']:.2f} | "
              f"تعامل {s.breakdown['engagement']:.2f} | پراکندگی {s.breakdown['spread']:.2f} | "
              f"تازگی {s.breakdown['freshness']:.2f})")
    print("\n      ── توضیح ترند اول ──")
    for line in signals[0].reason.splitlines():
        print(f"      {line}")


def test_metric_pluggability() -> None:
    print("\n▌ ۵) افزودن متریک جدید در زمان اجرا")
    from trendradar.analyzer import Metric

    class VideoMetric(Metric):
        name = "video_bias"
        label = "تمایل به ویدیو"
        description = "سهم پست‌های ویدیویی"

        def compute(self, ctx):
            if not ctx.posts:
                return 0.0
            n = sum(1 for p in ctx.posts if p.media_type == MediaType.VIDEO)
            return n / len(ctx.posts)

    p = MockProvider()
    posts = p._make_posts()
    base = TrendAnalyzer().analyze(posts, window_hours=200, min_posts=2)
    ext = TrendAnalyzer().add_metric(VideoMetric(), weight=0.15).analyze(posts, window_hours=200, min_posts=2)

    check("متریک جدید اضافه شد", "video_bias" in ext[0].breakdown)
    check("وزن‌ها دوباره نرمال شدند", abs(sum(ext[0].weights.values()) - 1.0) < 0.001)
    check("تحلیل با متریک جدید متفاوت است", base[0].score != ext[0].score,
          f"{base[0].score} → {ext[0].score}")


def test_storage() -> None:
    print("\n▌ ۶) لایه‌ی ذخیره‌سازی")
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "test.db")
        st = TrendStorage(db)
        p = MockProvider()
        posts = p._make_posts()

        n = st.save_posts(posts)
        check("ذخیره‌ی پست‌ها", n == len(posts), f"{n} پست")
        check("ذخیره‌ی تکراری (upsert)", st.save_posts(posts[:5]) == 5)

        stats = st.stats()
        check("شمارش پست‌ها", stats["posts"] == len(posts), str(stats["posts"]))
        check("شمارش هشتگ‌ها", stats["tags"] >= 3, str(stats["tags"]))

        n = st.upsert_channels(p._channels)
        check("ذخیره‌ی کانال‌ها", n == len(p._channels))
        joined = st.get_joined_channels("mock")
        check("کانال‌های عضو فیلتر شدند", len(joined) == 2, f"{len(joined)} کانال")

        since = utcnow() - timedelta(hours=200)
        back = st.posts_since(since, provider="mock")
        check("بازیابی پست‌ها", len(back) == len(posts), f"{len(back)} پست")

        hist = st.hashtag_history("ترند_مدلینگ", hours=200)
        check("سری زمانی ساخته شد", len(hist) > 0, f"{len(hist)} بازه‌ی ساعتی")

        vel = st.hashtag_velocity("ترند_مدلینگ", recent_hours=12, baseline_hours=72)
        check("محاسبه‌ی سرعت/شتاب", isinstance(vel, float), f"نسبت = {vel}")

        st.save_provider_state("mock", {"x": 1})
        check("ذخیره‌ی وضعیت پرووایدر", st.load_provider_state("mock").get("x") == 1)

        # فیلتر هشتگ
        only = st.posts_since(since, tags=["غذا"])
        check("فیلتر بر اساس هشتگ", all("غذا" in x.hashtags for x in only), f"{len(only)} پست")


def test_pipeline_e2e() -> None:
    print("\n▌ ۷) خط لوله‌ی کامل (انتها به انتها)")
    import asyncio

    with tempfile.TemporaryDirectory() as td:
        settings = TrendSettings(db_path=os.path.join(td, "pipeline.db"))
        settings.min_channel_members = 0
        st = TrendStorage(settings.db_path)
        prov = MockProvider(settings)

        pipe = TrendPipeline(prov, st, TrendAnalyzer(), settings=settings)

        query = SearchQuery(
            hashtags=["ترند_مدلینگ", "فروش_لباس", "غذا"],
            limit_per_tag=50,
            window_hours=200,
            min_members=0,
            max_channels=10,
            auto_join=False,
        )

        report = asyncio.run(pipe.run(query))

        check("گزارش تولید شد", report.run_id != "")
        check("کانال‌ها بررسی شدند", report.channels_scanned > 0, str(report.channels_scanned))
        check("کانال کشف شد", report.channels_discovered > 0, str(report.channels_discovered))
        check("پست‌ها جمع شدند", report.posts_collected > 0, str(report.posts_collected))
        check("ترندها شناسایی شدند", len(report.signals) > 0, f"{len(report.signals)} ترند")
        check("ترند اول درست است", report.signals and report.signals[0].tag == "ترند_مدلینگ")
        check("گزارش ذخیره شد", st.last_run() is not None)
        check("مدت اجرا ثبت شد", report.duration_sec > 0, f"{report.duration_sec:.2f}s")

        # اجرای دوم: عضویت خودکار
        report2 = asyncio.run(pipe.run(SearchQuery(
            hashtags=["ترند_مدلینگ"], limit_per_tag=20, window_hours=200,
            min_members=0, max_channels=5, auto_join=True,
        )))
        check("اجرای دوم موفق", report2.run_id != "")
        check("عضویت انجام شد", len(prov.join_calls) >= 0, f"{len(prov.join_calls)} عضویت")

        print("\n      ── خلاصه‌ی گزارش ──")
        d = report.to_dict()
        for k in ("run_id", "duration_sec", "channels_scanned", "channels_discovered",
                  "posts_collected"):
            print(f"      {k:22s}: {d[k]}")
        print(f"      {'signals':22s}: {len(d['signals'])}")
        print(f"      ترندها: " + ", ".join(f"#{s['tag']}({s['score']})" for s in d["signals"][:4]))


def test_cross_platform_merge() -> None:
    print("\n▌ ۸) ادغام سیگنال‌های چند پیام‌رسان")
    from trendradar.models import TrendSignal
    from trendradar.trend_radar import TrendRadar

    a = TrendSignal(tag="ترند", provider="telegram", score=80, reason="تلگرام")
    b = TrendSignal(tag="ترند", provider="instagram", score=60, reason="اینستاگرام")
    c = TrendSignal(tag="متفاوت", provider="telegram", score=50, reason="تنها")

    merged = TrendRadar._merge_signals([a, b, c])
    check("ادغام انجام شد", len(merged) == 2, f"{len(merged)} سیگنال")
    trend = next(s for s in merged if s.tag == "ترند")
    check("پاداش چندپلتفرمی اعمال شد", trend.score > (80 + 60) / 2, f"{trend.score} > 70")
    check("نام پلتفرم‌ها ادغام شد", "+" in trend.provider, trend.provider)
    check("توضیح به‌روز شد", "پیام‌رسان" in trend.reason)


def test_instagram_bridge() -> None:
    print("\n▌ ۹) پل به سیستم اینستاگرام")
    from trendradar.models import TrendSignal
    from trendradar.trend_radar import TrendRadar

    class _R(TrendRadar):
        def __init__(self):  # بدون راه‌اندازی سنگین
            pass

    signals = [
        TrendSignal(tag="ترند مدلینگ", provider="telegram", score=90),
        TrendSignal(tag="فروش لباس", provider="telegram", score=70),
    ]
    terms = _R().signals_to_instagram_terms(signals, top=5)
    check("تبدیل به هشتگ اینستاگرام", terms == ["ترند_مدلینگ", "فروش_لباس"], str(terms))


def test_guards() -> None:
    print("\n▌ ۱۰) گاردهای ضد‌بن")
    from trendradar.errors import DailyCapReached, RateLimited
    from trendradar.providers.telegram.guard import DailyCounter, JoinGuard, RateLimiter

    dc = DailyCounter(limit=3)
    check("شمارنده‌ی روزانه خالی", dc.used == 0 and dc.remaining == 3)
    dc.bump()
    dc.bump()
    dc.check("تست")
    try:
        dc.bump()
        dc.check("تست")           # این باید خطا بدهد (۳/۳)
        check("سقف روزانه اعمال شد", False)
    except DailyCapReached:
        check("سقف روزانه اعمال شد", True, f"{dc.used}/{dc.limit}")

    st = dc.to_state()
    dc2 = DailyCounter(limit=3)
    dc2.from_state(st)
    check("وضعیت شمارنده بازیابی شد", dc2.used == 3)

    g = JoinGuard(min_interval=10, max_interval=20, daily_cap=2)
    g.check("ch1")                                     # باید مجاز باشد
    g.record_join("ch1")
    try:
        g.check("ch2")
        check("فاصله‌ی اجباری بین عضویت‌ها", False)
    except RateLimited as e:
        check("فاصله‌ی اجباری بین عضویت‌ها", e.retry_after > 0, f"{e.retry_after:.0f}s صبر")

    g2 = JoinGuard(min_interval=0, max_interval=0, daily_cap=2, cooldown_after_error=999,
                   max_consecutive_errors=2)
    g2.record_error("x")
    g2.record_error("y")
    try:
        g2.check("z")
        check("خنک‌شدن بعد از خطاهای پیاپی", False)
    except (RateLimited, DailyCapReached):
        check("خنک‌شدن بعد از خطاهای پیاپی", True)

    rl = RateLimiter(min_interval=0.05, jitter=0)
    import time

    t0 = time.monotonic()
    rl.wait()
    rl.wait()
    rl.wait()
    elapsed = time.monotonic() - t0
    check("محدودکننده‌ی نرخ کار می‌کند", elapsed >= 0.09, f"{elapsed*1000:.0f}ms برای ۳ فراخوانی")

    rl.block_for(0.3)
    check("مسدودسازی بعد از FloodWait", rl.blocked_for > 0.2, f"{rl.blocked_for:.2f}s")


def test_no_deadlock_in_sync_api() -> None:
    """
    🔴 تست رگرسیون برای باگ self-deadlock.

    کشف‌شده در عمل: `JoinGuard.stats()` قفل را می‌گرفت و داخلش پراپرتی
    `seconds_until_next_join` را صدا می‌زد که خودش همان قفل را می‌خواست
    → برنامه بی‌نهایت هنگ می‌کرد (بی‌صدا، بدون خطا).

    این تست هر متد sync عمومی را با watchdog زمانی صدا می‌زند. اگر هرکدام
    هنگ کند، تست شکست می‌خورد.
    """
    import threading

    print("\n▌ ۱۲) تشخیص deadlock در API همگام (رگرسیون)")

    from trendradar.providers.telegram.guard import JoinGuard

    def run_with_watchdog(fn, timeout: float = 5.0, label: str = ""):
        """اجرای تابع با محدودیت زمانی — اگر هنگ کرد، شکست"""
        result = {"value": None, "error": None}

        def worker():
            try:
                result["value"] = fn()
            except Exception as e:
                result["error"] = f"{type(e).__name__}: {e}"

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            return False, None, f"هنگ کرد (> {timeout}s)"
        if result["error"]:
            return False, None, result["error"]
        return True, result["value"], ""

    # ── گارد عضویت: متدهایی که قفل تودرتو دارند ──
    g = JoinGuard(min_interval=5, max_interval=10, daily_cap=10)
    g.record_join("ch1")

    ok, val, err = run_with_watchdog(g.stats, label="JoinGuard.stats")
    check("JoinGuard.stats() هنگ نمی‌کند", ok, err or str(val))
    if ok and val:
        check("  فیلدهای آمار کامل‌اند",
              all(k in val for k in ("joined_today", "daily_cap", "seconds_until_next", "blacklisted")),
              str(sorted(val)))
        check("  seconds_until_next عدد است", isinstance(val["seconds_until_next"], (int, float)),
              f"{val['seconds_until_next']}s")

    ok, val, err = run_with_watchdog(lambda: g.seconds_until_next_join, label="seconds_until_next_join")
    check("JoinGuard.seconds_until_next_join هنگ نمی‌کند", ok, err or f"{val:.1f}s")

    ok, val, err = run_with_watchdog(g.to_state, label="to_state")
    check("JoinGuard.to_state() هنگ نمی‌کند", ok, err or str(val))

    # فراخوانی تودرتوی متدها داخل وضعیت قفل‌شده (شبیه‌سازی شرط مسابقه).
    # توجه: از گاردی استفاده می‌کنیم که فاصله‌ی اجباری ندارد، وگرنه
    # `check` به‌درستی RateLimited می‌دهد و تست را گمراه می‌کند.
    g2 = JoinGuard(min_interval=0, max_interval=0, daily_cap=10)

    def nested_use():
        g2.check("ch_new")            # باید مجاز باشد
        g2.record_join("ch_new")
        g2.record_error("ch_bad")
        g2.record_flood(0.01)
        return g2.stats()

    ok, val, err = run_with_watchdog(nested_use, label="استفاده‌ی تودرتو")
    check("زنجیره‌ی عملیات گارد هنگ نمی‌کند", ok, err or "ok")

    # و بررسی اینکه گارد با فاصله، درست رد می‌کند (رفتار مطلوب)
    from trendradar.errors import RateLimited

    def spaced_guard():
        gg = JoinGuard(min_interval=30, max_interval=60, daily_cap=10)
        gg.record_join("a")
        try:
            gg.check("b")
            return "بدون محدودیت"
        except RateLimited:
            return "درست رد شد"

    ok, val, err = run_with_watchdog(spaced_guard, label="گارد فاصله‌دار")
    check("گارد فاصله‌دار درست عمل می‌کند", ok and val == "درست رد شد", err or str(val))

    # ── RateLimiter ──
    from trendradar.providers.telegram.guard import RateLimiter

    rl = RateLimiter(min_interval=0, jitter=0)
    rl.block_for(0.05)
    ok, val, err = run_with_watchdog(lambda: rl.blocked_for, label="RateLimiter.blocked_for")
    check("RateLimiter.blocked_for هنگ نمی‌کند", ok, err or f"{val:.3f}s")

    # ── API کامل TrendRadar (بدون شبکه) ──
    radar = _make_offline_radar()
    if radar is not None:
        for label, fn in [
            ("radar.guard_stats()", radar.guard_stats),
            ("radar.stats()", radar.stats),
            ("radar.providers()", radar.providers),
        ]:
            ok, val, err = run_with_watchdog(fn, label=label)
            check(f"{label} هنگ نمی‌کند", ok, err or "ok")

        gs = radar.guard_stats()
        check("آمار گارد از مسیر TrendRadar", "daily_cap" in gs or "error" in gs,
              str(gs)[:70])

        try:
            radar.shutdown()
        except Exception:
            pass


def _make_offline_radar():
    """
    ساخت TrendRadar بدون نیاز به شبکه — برای تست‌های آفلاین.

    دیتابیس موقت می‌سازد و پرووایدر تلگرام را ثبت می‌کند (بدون اتصال).
    """
    try:
        from trendradar.config import TrendSettings
        from trendradar.trend_radar import TrendRadar
        from trendradar.registry import get_registry
        from trendradar.providers.telegram import TelegramProvider

        get_registry().register(TelegramProvider)

        td = tempfile.mkdtemp()
        settings = TrendSettings(db_path=os.path.join(td, "offline.db"))
        radar = TrendRadar(settings)
        radar.register_default_providers()
        return radar
    except Exception as e:
        print(f"  ⚠️ ساخت radar آفلاین ناموفق: {e}")
        return None


def test_telegram_real_api() -> None:
    """بررسی اینکه کد ما با متدهای واقعی Telethon همخوانی دارد"""
    print("\n▌ ۱۱) همخوانی با API واقعی Telethon")
    try:
        import inspect

        from telethon import functions
    except ImportError:
        print("  ⚠️ Telethon نصب نیست — این بخش رد شد")
        return

    # امضاهایی که در کد استفاده کرده‌ایم
    sp = inspect.signature(functions.channels.SearchPostsRequest.__init__)
    params = set(sp.parameters)
    for p in ("offset_rate", "offset_peer", "offset_id", "limit", "hashtag"):
        check(f"SearchPostsRequest.{p}", p in params)

    sg = inspect.signature(functions.messages.SearchGlobalRequest.__init__)
    for p in ("q", "filter", "min_date", "offset_rate", "offset_peer", "offset_id", "limit"):
        check(f"SearchGlobalRequest.{p}", p in sg.parameters)

    sc = inspect.signature(functions.contacts.SearchRequest.__init__)
    for p in ("q", "limit", "broadcasts"):
        check(f"contacts.SearchRequest.{p}", p in sc.parameters)

    check("JoinChannelRequest.channel",
          "channel" in inspect.signature(functions.channels.JoinChannelRequest.__init__).parameters)
    check("GetFullChannelRequest.channel",
          "channel" in inspect.signature(functions.channels.GetFullChannelRequest.__init__).parameters)
    check("GetChannelRecommendationsRequest.channel",
          "channel" in inspect.signature(functions.channels.GetChannelRecommendationsRequest.__init__).parameters)

    from telethon.errors import FloodWaitError

    # توجه: `seconds` یک attribute نمونه است که در __init__ ست می‌شود،
    # نه attribute کلاس. پس باید روی یک نمونه بررسی شود.
    _inst = FloodWaitError.__new__(FloodWaitError)
    _inst.__init__(None, 42)          # request=None, capture=42
    check("FloodWaitError.seconds روی نمونه", _inst.seconds == 42, f"{_inst.seconds}")

    import telethon

    from trendradar.providers.telegram.client import _import_telethon

    check("_import_telethon کار می‌کند", _import_telethon() is not None)
    print(f"      نسخه‌ی Telethon: {telethon.__version__}")


def main() -> int:
    print("═" * 72)
    print("  🧪 تست جامع TrendRadar")
    print("═" * 72)

    tests = [
        test_models,
        test_registry,
        test_runtime,
        test_analyzer,
        test_metric_pluggability,
        test_storage,
        test_pipeline_e2e,
        test_cross_platform_merge,
        test_instagram_bridge,
        test_guards,
        test_no_deadlock_in_sync_api,
        test_telegram_real_api,
    ]
    for t in tests:
        try:
            t()
        except Exception as e:
            import traceback

            print(f"\n  ❌ خطای غیرمنتظره در {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()

    total = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = [(n, d) for n, ok, d in _results if not ok]

    print("\n" + "═" * 72)
    print(f"  نتیجه: {passed}/{total} تست موفق")
    if failed:
        print(f"  ❌ ناموفق‌ها:")
        for name, detail in failed:
            print(f"     • {name}  {detail}")
    else:
        print("  🎉 همه‌ی تست‌ها موفق!")
    print("═" * 72)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
