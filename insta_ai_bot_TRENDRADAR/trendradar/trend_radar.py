"""
🎯 TrendRadar — واسط اصلی (Facade)
=================================

این کلاس تنها نقطه‌ی ورودی است که ربات موجود با آن کار می‌کند.
همه‌ی پیچیدگی async/await، رجیستری پرووایدرها و پاییپ‌لاین پشت این
واسط پنهان است و متدها **sync** هستند تا در `pyTelegramBotAPI` ساده
استفاده شوند.

نمونه:
    from trendradar import TrendRadar

    radar = TrendRadar()
    radar.register_default_providers()

    # وضعیت همه‌ی پیام‌رسان‌ها
    for h in radar.health():
        print(h.status_emoji, h.name, h.account or h.error)

    # ورود تعاملی به تلگرام
    print(radar.auth_send_code())
    print(radar.auth_sign_in("12345"))

    # اسکن
    report = radar.scan(hashtags=["ترند", "مدلینگ"], window_hours=48)
    for s in report.signals[:5]:
        print(s.rank, s.tag, s.score, s.reason)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from .analyzer import TrendAnalyzer
from .config import TrendSettings, load_settings
from .errors import TrendRadarError
from .models import ChannelRef, ProviderHealth, RunReport, SearchQuery, TrendSignal
from .pipeline import TrendPipeline
from .providers import load_builtin_providers
from .registry import get_registry
from .runtime import AsyncRuntime, shared_runtime
from .storage import TrendStorage

log = logging.getLogger("trendradar")


class TrendRadar:
    """
    واسط sync برای موتور ترند‌یابی.

    :param settings: اگر داده نشود، از متغیرهای محیطی خوانده می‌شود
    :param runtime:  اگر داده نشود، از runtime مشترک استفاده می‌کند
    """

    def __init__(self, settings: Optional[TrendSettings] = None, runtime: Optional[AsyncRuntime] = None):
        self.settings: TrendSettings = settings or load_settings()
        self.runtime: AsyncRuntime = runtime or shared_runtime()
        self.storage = TrendStorage(self.settings.db_path)
        self.analyzer = TrendAnalyzer().from_settings(self.settings.weights)
        self._providers: Dict[str, Any] = {}
        self._pipelines: Dict[str, TrendPipeline] = {}
        self._registered = False

        self._setup_logging()

    # ═════════════════════════════════════════════════════════
    # راه‌اندازی
    # ═════════════════════════════════════════════════════════
    def _setup_logging(self) -> None:
        if not logging.getLogger("trendradar").handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(
                logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)-12s | %(message)s",
                                  datefmt="%H:%M:%S")
            )
            lg = logging.getLogger("trendradar")
            lg.addHandler(handler)
            lg.setLevel(getattr(logging, self.settings.log_level.upper(), logging.INFO))

    def register_default_providers(self) -> List[str]:
        """ثبت پرووایدرهای داخلی (تلگرام و هر چه بعداً اضافه شود)"""
        names = load_builtin_providers()
        self._registered = True

        # فقط پرووایدرهای فعال در تنظیمات
        for name in self.settings.enabled_providers:
            if name not in names:
                log.warning("پرووایدر «%s» در تنظیمات فعال است ولی ثبت نشده", name)
                continue
            try:
                self._providers[name] = get_registry().create(name, self.settings)
                self._pipelines[name] = TrendPipeline(
                    self._providers[name], self.storage, self.analyzer, settings=self.settings
                )
                # بازیابی وضعیت گاردها از اجرای قبلی
                state = self.storage.load_provider_state(name)
                if state:
                    try:
                        self._providers[name].import_state(state)
                    except Exception as e:
                        log.debug("بازیابی وضعیت %s ناموفق: %s", name, e)
            except Exception as e:
                log.warning("ساخت پرووایدر «%s» ناموفق: %s", name, e)

        log.info("پرووایدرهای فعال: %s", ", ".join(self._providers) or "—")
        return list(self._providers)

    def _ensure_ready(self) -> None:
        if not self._registered:
            self.register_default_providers()

    # ═════════════════════════════════════════════════════════
    # دسترسی به پرووایدرها
    # ═════════════════════════════════════════════════════════
    def providers(self) -> Dict[str, Any]:
        self._ensure_ready()
        return dict(self._providers)

    def provider(self, name: str = "telegram") -> Any:
        self._ensure_ready()
        p = self._providers.get(name)
        if p is None:
            raise TrendRadarError(f"پرووایدر «{name}» فعال نیست")
        return p

    def health(self) -> List[ProviderHealth]:
        """وضعیت همه‌ی پرووایدرها — برای نمایش در پنل ربات"""
        self._ensure_ready()
        run = self._run_async
        out: List[ProviderHealth] = []
        for name, p in self._providers.items():
            try:
                out.append(run(p.health()))
            except Exception as e:
                out.append(ProviderHealth(
                    provider=name, name=getattr(p, "display_name", name),
                    available=True, error=f"{type(e).__name__}: {e}",
                ))
        return out

    # ═════════════════════════════════════════════════════════
    # احراز هویت تلگرام (تعاملی برای ربات)
    # ═════════════════════════════════════════════════════════
    def auth_send_code(self, phone: Optional[str] = None, provider: str = "telegram") -> str:
        """ارسال کد تایید تلگرام"""
        p = self.provider(provider)
        return self._run_async(p.auth_send_code(phone))

    def auth_sign_in(self, code: str, provider: str = "telegram") -> str:
        """ورود با کد تایید"""
        p = self.provider(provider)
        return self._run_async(p.auth_sign_in(code))

    def auth_sign_in_password(self, password: str, provider: str = "telegram") -> str:
        """ورود با رمز دو مرحله‌ای"""
        p = self.provider(provider)
        return self._run_async(p.auth_sign_in_password(password))

    def is_authorized(self, provider: str = "telegram") -> bool:
        try:
            return bool(self._run_async(self.provider(provider).is_authorized()))
        except Exception:
            return False

    # ═════════════════════════════════════════════════════════
    # اسکن اصلی
    # ═════════════════════════════════════════════════════════
    def scan(
        self,
        *,
        hashtags: Optional[Sequence[str]] = None,
        keywords: Optional[Sequence[str]] = None,
        providers: Optional[Sequence[str]] = None,
        limit_per_tag: Optional[int] = None,
        window_hours: Optional[int] = None,
        auto_join: bool = False,
        joined_only: bool = False,
        discover_channels: bool = True,
        min_members: Optional[int] = None,
        max_channels: Optional[int] = None,
    ) -> RunReport:
        """
        اجرای یک اسکن ترند‌یابی (sync).

        این متد از داخل تردهای ربات قابل صدا زدن است؛ به‌طور داخلی روی
        event loop مخصوص اجرا می‌شود.

        نمونه:
            report = radar.scan(hashtags=["ترند"], window_hours=48)
        """
        self._ensure_ready()

        query = SearchQuery(
            hashtags=list(hashtags or []),
            keywords=list(keywords or []),
            limit_per_tag=limit_per_tag or self.settings.default_limit_per_tag,
            window_hours=window_hours or self.settings.default_window_hours,
            auto_join=auto_join,
            joined_only=joined_only,
            discover_channels=discover_channels,
            min_members=min_members if min_members is not None else self.settings.min_channel_members,
            max_channels=max_channels or self.settings.max_channels_per_scan,
            exclude_channel_uids=list(self.settings.exclude_channels),
            providers=list(providers or self.settings.enabled_providers),
        )

        targets = [p for p in query.providers if p in self._pipelines] or list(self._pipelines)
        if not targets:
            return RunReport(
                run_id="no-provider", query=query,
                errors=["هیچ پرووایدر فعالی وجود ندارد. تنظیمات TG_API_ID/TG_API_HASH را چک کن."],
            )

        merged = RunReport(run_id=f"multi-{datetime.now():%H%M%S}", query=query)
        for name in targets:
            pipe = self._pipelines[name]
            try:
                rep = self._run_async(pipe.run(query))
            except Exception as e:
                log.exception("اسکن پرووایدر %s ناموفق", name)
                merged.errors.append(f"{name}: {type(e).__name__}: {e}")
                continue

            merged.signals.extend(rep.signals)
            merged.errors.extend(rep.errors)
            merged.channels_scanned += rep.channels_scanned
            merged.channels_discovered += rep.channels_discovered
            merged.channels_joined += rep.channels_joined
            merged.posts_collected += rep.posts_collected
            merged.provider_stats.update(rep.provider_stats)

        # ادغام و رتبه‌بندی مجدد بین پیام‌رسان‌ها
        merged.signals = self._merge_signals(merged.signals)
        merged.finished_at = datetime.now(merged.started_at.tzinfo) if merged.started_at else None
        return merged

    @staticmethod
    def _merge_signals(signals: List[TrendSignal]) -> List[TrendSignal]:
        """
        ادغام سیگنال‌های چند پیام‌رسان.

        اگر یک هشتگ در چند پیام‌رسان دیده شود، سیگنال‌ها ادغام می‌شوند و
        امتیازش با یک پاداش هم‌افزایی (cross-platform bonus) بالا می‌رود —
        چون ترند بودن روی دو پلتفرم مستقل، بسیار معنادارتر است.
        """
        if not signals:
            return []

        merged: Dict[str, TrendSignal] = {}
        for s in signals:
            key = s.tag
            if key not in merged:
                merged[key] = s
                continue

            base = merged[key]
            platforms = {base.provider, s.provider}
            # میانگین وزنی ساده + پاداش چندپلتفرمی
            base.score = round((base.score + s.score) / 2 * (1.0 + 0.12 * (len(platforms) - 1)), 2)
            base.top_posts = (base.top_posts + s.top_posts)[:5]
            base.top_channels = list(dict.fromkeys(base.top_channels + s.top_channels))[:4]
            base.provider = "+".join(sorted(platforms))
            base.reason = base.reason + f"\n🌍 روی {len(platforms)} پیام‌رسان دیده شد"

        out = sorted(merged.values(), key=lambda s: s.score, reverse=True)
        for i, s in enumerate(out, 1):
            s.rank = i
        return out

    # ═════════════════════════════════════════════════════════
    # کاوش و ابزارهای کمکی
    # ═════════════════════════════════════════════════════════
    def discover_channels(self, term: str, *, provider: str = "telegram", limit: int = 20,
                          min_members: int = 0) -> List[ChannelRef]:
        """پیدا کردن کانال با یک عبارت"""
        p = self.provider(provider)
        return self._run_async(p.discover_channels(term, limit=limit, min_members=min_members))

    def resolve_channel(self, identifier: str, provider: str = "telegram") -> Optional[ChannelRef]:
        """تبدیل لینک/یوزرنیم به کانال"""
        return self._run_async(self.provider(provider).resolve(identifier))

    def join_channel(self, channel: ChannelRef, provider: str = "telegram",
                     dry_run: bool = False) -> ChannelRef:
        """عضویت در کانال با گارد ضد‌بن"""
        result = self._run_async(self.provider(provider).join_channel(channel, dry_run=dry_run))
        try:
            self.storage.upsert_channel(result)
        except Exception:
            pass
        return result

    def joined_channels(self, provider: str = "telegram", limit: int = 200) -> List[ChannelRef]:
        """لیست کانال‌های عضو"""
        try:
            chans = self._run_async(self.provider(provider).list_joined(limit=limit))
            if chans:
                self.storage.upsert_channels(chans)
                return chans
        except Exception as e:
            log.debug("لیست زنده‌ی کانال‌های عضو ناموفق: %s", e)
        return self.storage.get_joined_channels(provider, limit=limit)

    def guard_stats(self, provider: str = "telegram") -> Dict[str, Any]:
        """آمار گارد ضد‌بن — مهم برای نمایش «چند عضویت امروز مانده»"""
        try:
            return self.provider(provider).guard_stats()
        except Exception as e:
            return {"error": str(e)}

    # ═════════════════════════════════════════════════════════
    # یکپارچگی با ربات فعلی (پل به world اینستاگرام)
    # ═════════════════════════════════════════════════════════
    def signals_to_instagram_terms(self, signals: Sequence[TrendSignal], top: int = 5) -> List[str]:
        """
        تبدیل ترندهای تلگرام به عبارت‌های آماده برای `scrape_hashtag` اینستاگرام.

        ⭐ این نقطه‌ی اتصال دو سیستم است: ترند‌یابی روی تلگرام →
        اسکرپ همان هشتگ در اینستاگرام. یعنی می‌توانی بفهمی هشتگی که در
        تلگرام داغ شده، در اینستاگرام چطور عمل می‌کند.
        """
        out: List[str] = []
        for s in signals[:top]:
            tag = s.tag.replace(" ", "_")
            if tag and tag not in out:
                out.append(tag)
        return out

    def stats(self) -> Dict[str, Any]:
        """آمار کلی برای پنل"""
        return {
            "storage": self.storage.stats(),
            "providers": self._providers and {
                name: p.account_label() if hasattr(p, "account_label") else "—"
                for name, p in self._providers.items()
            } or {},
            "enabled": self.settings.enabled_providers,
            "db_path": self.settings.db_path,
        }

    # ═════════════════════════════════════════════════════════
    # چرخه‌ی عمر
    # ═════════════════════════════════════════════════════════
    def shutdown(self) -> None:
        """بستن تمیز اتصال‌ها + ذخیره‌ی وضعیت گاردها"""
        for name, p in self._providers.items():
            try:
                state = p.export_state()
                if state:
                    self.storage.save_provider_state(name, state)
            except Exception:
                pass
            try:
                self._run_async(p.disconnect())
            except Exception:
                pass
        log.info("TrendRadar بسته شد")

    # ═════════════════════════════════════════════════════════
    # داخلی
    # ═════════════════════════════════════════════════════════
    def _run_async(self, coro):
        """
        اجرای یک کوروتین با مدیریت چالش «ما داخل حلقه‌ی asyncio هستیم؟».

        اگر از داخل ترد ربات (sync) صدا زده شود → پل امن `runtime.run`
        اگر از داخل خود event loop صدا زده شود → نمی‌توان بلوکه کرد؛
        پس در یک ترد کمکی با حلقه‌ی جداگانه اجرا می‌شود.
        """
        if isinstance(coro, type(None)):
            return None

        if self.runtime.in_loop_thread:
            # داخل حلقه هستیم — از deadlock پرهیز کن
            import asyncio
            import concurrent.futures

            def _in_new_loop():
                return asyncio.run(coro)

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(_in_new_loop).result()

        return self.runtime.run(coro)


# ─────────────────────────────────────────────────────────────
# نمونه‌ی مشترک (Singleton سبک)
# ─────────────────────────────────────────────────────────────
_default: Optional[TrendRadar] = None


def get_radar(settings: Optional[TrendSettings] = None) -> TrendRadar:
    """نمونه‌ی مشترک TrendRadar — برای استفاده در سراسر ربات"""
    global _default
    if _default is None:
        _default = TrendRadar(settings)
        _default.register_default_providers()
    return _default
