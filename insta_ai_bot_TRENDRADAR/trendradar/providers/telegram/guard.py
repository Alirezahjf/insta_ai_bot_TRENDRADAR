"""
🛡️ گاردهای ضد‌بن
================

چرا حیاتی است؟
--------------
این پرووایدر با **اکانت کاربری** (userbot) کار می‌کند، نه بات رسمی.
تلگرام رفتار ربات‌گونه را تشخیص می‌دهد و اکانت را محدود یا بن می‌کند.
شایع‌ترین دلایل بن:

  • عضویت سریع و پشت‌سرهم در چنل‌های زیاد (هر ثانیه یکی = بن فوری)
  • جستجوی وسواسی با نرخ بالا
  • الگوی زمانی کاملاً منظم (بدون jitter)

این ماژول سه گارد دارد:
  1. `RateLimiter`  — فاصله‌ی حداقلی بین درخواست‌ها (token bucket)
  2. `JoinGuard`    — فاصله‌ی تصادفی + سقف روزانه برای عضویت
  3. `DailyCounter` — شمارنده‌ی روزانه با ریست خودکار بر اساس تاریخ تهران

همه‌ی گاردها thread-safe هستند و وضعیت‌شان قابل ذخیره/بازیابی است.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, Optional

from ...errors import DailyCapReached, RateLimited

log = logging.getLogger("trendradar.telegram.guard")


# ─────────────────────────────────────────────────────────────
# ۱) محدودکننده‌ی نرخ (Token Bucket)
# ─────────────────────────────────────────────────────────────
class RateLimiter:
    """
    محدودکننده‌ی نرخ با الگوی token bucket + jitter.

    >>> rl = RateLimiter(min_interval=2.5, jitter=0.8)
    >>> rl.wait()          # اگر لازم باشد می‌خوابد
    """

    def __init__(self, min_interval: float = 2.0, *, jitter: float = 0.5, name: str = ""):
        self.min_interval = max(0.0, float(min_interval))
        self.jitter = max(0.0, float(jitter))
        self.name = name or "rate"
        self._next_allowed = 0.0
        self._lock = threading.RLock()
        self._blocked_until = 0.0

    def _delay_for(self) -> float:
        with self._lock:
            now = time.monotonic()
            base = max(self._next_allowed, self._blocked_until)
            delay = max(0.0, base - now)
            extra = random.uniform(0, self.jitter)
            self._next_allowed = max(now, base) + self.min_interval + extra
            return delay

    def wait(self) -> float:
        """در صورت نیاز می‌خوابد و مدت خواب را برمی‌گرداند"""
        delay = self._delay_for()
        if delay > 0:
            log.debug("[%s] صبر %.1f ثانیه", self.name, delay)
            time.sleep(delay)
        return delay

    def block_for(self, seconds: float) -> None:
        """مسدود کردن تا N ثانیه (بعد از FloodWait)"""
        with self._lock:
            self._blocked_until = max(self._blocked_until, time.monotonic() + float(seconds))
        log.warning("[%s] تا %.0f ثانیه مسدود شد", self.name, seconds)

    @property
    def blocked_for(self) -> float:
        with self._lock:
            return max(0.0, self._blocked_until - time.monotonic())


# ─────────────────────────────────────────────────────────────
# ۲) شمارنده‌ی روزانه
# ─────────────────────────────────────────────────────────────
@dataclass
class DailyCounter:
    """
    شمارنده‌ی عملیات روزانه که سر تاریخ جدید خودش ریست می‌شود.

    از تاریخ **شمسی‌نما/تهران** استفاده نمی‌کند بلکه تاریخ UTC محلی سرور
    را مبنا می‌گیرد؛ برای جلوگیری از چند‌بار ریست، تاریخ را ذخیره می‌کند.
    """

    limit: int = 1500
    _count: int = 0
    _day: str = field(default_factory=lambda: date.today().isoformat())

    def _roll(self) -> None:
        today = date.today().isoformat()
        if today != self._day:
            log.info("شمارنده‌ی روزانه ریست شد (روز جدید: %s، قبلی: %d)", today, self._count)
            self._day = today
            self._count = 0

    @property
    def used(self) -> int:
        self._roll()
        return self._count

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def check(self, action: str = "عملیات", n: int = 1) -> None:
        """اگر سقف پر شده باشد استثنا می‌دهد"""
        self._roll()
        if self._count + n > self.limit:
            raise DailyCapReached(
                f"سقف روزانه‌ی «{action}» پر شد ({self._count}/{self.limit}). "
                f"فردا دوباره امتحان کن — این محدودیت برای محافظت از اکانت است.",
                action=action,
                cap=self.limit,
            )

    def bump(self, n: int = 1) -> int:
        self._roll()
        self._count += n
        return self._count

    def to_state(self) -> Dict[str, object]:
        self._roll()
        return {"count": self._count, "day": self._day}

    def from_state(self, state: Dict[str, object]) -> None:
        try:
            self._count = int(state.get("count", 0))          # type: ignore[arg-type]
            self._day = str(state.get("day", self._day))
        except (TypeError, ValueError):
            pass
        self._roll()
        # اگر سقف در کد عوض شده باشد، سقف پایین‌تر غالب است
        if self._count > self.limit:
            self._count = self.limit


# ─────────────────────────────────────────────────────────────
# ۳) گارد عضویت — حسّاس‌ترین بخش
# ─────────────────────────────────────────────────────────────
class JoinGuard:
    """
    گارد عضویت در کانال.

    منطق:
      1. فاصله‌ی تصادفی بین `min_interval` و `max_interval` ثانیه
         (jitter تصادفی حیاتی است: الگوی منظم = شناسایی ربات)
      2. سقف روزانه
      3. لیست سیاه کانال‌های پرخطر
      4. پنجره‌ی «سرد شدن» بعد از هر بن/خطای مکرر

    نمونه:
        >>> g = JoinGuard(min_interval=90, max_interval=240, daily_cap=20)
        >>> g.check(channel)      # اگر مشکلی بود استثنا می‌دهد
        >>> g.record_join(channel)  # بعد از موفقیت
    """

    def __init__(
        self,
        *,
        min_interval: int = 90,
        max_interval: int = 240,
        daily_cap: int = 20,
        cooldown_after_error: int = 600,
        max_consecutive_errors: int = 3,
    ):
        self.min_interval = max(1, int(min_interval))
        self.max_interval = max(self.min_interval, int(max_interval))
        self.cooldown_after_error = int(cooldown_after_error)
        self.max_consecutive_errors = int(max_consecutive_errors)

        self.counter = DailyCounter(limit=daily_cap)
        self._last_join_at: float = 0.0
        self._next_allowed_at: float = 0.0
        self._consecutive_errors = 0
        self._cooldown_until: float = 0.0
        # RLock و نه Lock: بعضی متدها (مثل stats) داخل قفل، عضو دیگری را
        # می‌خوانند که خودش احتیاج به قفل دارد → با Lock ساده deadlock می‌شد.
        self._lock = threading.RLock()
        self._joined_today: set[str] = set()
        self._blacklist: set[str] = set()

    # ── بررسی ────────────────────────────────────────────
    def check(self, channel_uid: str = "") -> None:
        """
        پیش از عضویت صدا بزن. اگر مجاز نباشد استثنا می‌دهد.

        :raises DailyCapReached / RateLimited / GuardRejected
        """
        with self._lock:
            now = time.monotonic()

            if now < self._cooldown_until:
                wait = self._cooldown_until - now
                raise RateLimited(
                    f"گارد عضویت در حالت خنک‌شدن است ({wait:.0f} ثانیه مانده). "
                    f"چند خطای پشت‌سرهم رخ داده بود.",
                    retry_after=wait,
                    provider="telegram",
                )

            if now < self._next_allowed_at:
                wait = self._next_allowed_at - now
                raise RateLimited(
                    f"برای محافظت از اکانت، فاصله‌ی بین عضویت‌ها رعایت می‌شود "
                    f"({wait:.0f} ثانیه مانده).",
                    retry_after=wait,
                    provider="telegram",
                )

            if channel_uid and channel_uid in self._joined_today:
                from ...errors import GuardRejected

                raise GuardRejected(f"امروز قبلاً در «{channel_uid}» عضو شده‌ایم")

        # سقف روزانه (خودش استثنا می‌دهد)
        self.counter.check("عضویت در کانال")

        if channel_uid and channel_uid in self._blacklist:
            from ...errors import GuardRejected

            raise GuardRejected(f"«{channel_uid}» در لیست سیاه است (خطای قبلی)")

    @property
    def seconds_until_next_join(self) -> float:
        with self._lock:
            return max(0.0, self._next_allowed_at - time.monotonic())

    # ── ثبت نتیجه ────────────────────────────────────────
    def record_join(self, channel_uid: str = "") -> None:
        """بعد از عضویت موفق"""
        with self._lock:
            self._last_join_at = time.monotonic()
            delay = random.uniform(self.min_interval, self.max_interval)
            self._next_allowed_at = self._last_join_at + delay
            self._consecutive_errors = 0
            if channel_uid:
                self._joined_today.add(channel_uid)
        self.counter.bump()
        log.info(
            "عضویت ثبت شد (%s) | امروز: %d/%d | عضویت بعدی %.0f ثانیه دیگر",
            channel_uid or "?",
            self.counter.used,
            self.counter.limit,
            delay,
        )

    def record_error(self, channel_uid: str = "", *, permanent: bool = False) -> None:
        """بعد از خطا در عضویت"""
        with self._lock:
            self._consecutive_errors += 1
            n = self._consecutive_errors
            if permanent and channel_uid:
                self._blacklist.add(channel_uid)
            if n >= self.max_consecutive_errors:
                self._cooldown_until = time.monotonic() + self.cooldown_after_error
                self._consecutive_errors = 0
                log.warning(
                    "گارد عضویت: %d خطای پشت‌سرهم → %.0f ثانیه خنک‌شدن",
                    n, self.cooldown_after_error,
                )

    def record_flood(self, seconds: float) -> None:
        """هنگام FloodWait — کل گارد را قفل می‌کند"""
        with self._lock:
            self._next_allowed_at = max(self._next_allowed_at, time.monotonic() + seconds)

    # ── وضعیت ────────────────────────────────────────────
    def stats(self) -> Dict[str, object]:
        # ⚠️ توجه: داخل این قفل نباید پراپرتی `seconds_until_next_join` صدا زده شود،
        # چون آن هم همین قفل را می‌خواهد و باعث self-deadlock می‌شود.
        # مقدار را مستقیم حساب می‌کنیم.
        with self._lock:
            return {
                "joined_today": self.counter.used,
                "daily_cap": self.counter.limit,
                "remaining_today": self.counter.remaining,
                "seconds_until_next": round(max(0.0, self._next_allowed_at - time.monotonic()), 1),
                "blacklisted": len(self._blacklist),
                "cooldown_active": time.monotonic() < self._cooldown_until,
                "consecutive_errors": self._consecutive_errors,
            }

    def to_state(self) -> Dict[str, object]:
        return {"counter": self.counter.to_state(), "blacklist": sorted(self._blacklist)}

    def from_state(self, state: Dict[str, object]) -> None:
        try:
            self.counter.from_state(state.get("counter") or {})          # type: ignore[arg-type]
            bl = state.get("blacklist") or []
            if isinstance(bl, (list, tuple, set)):
                self._blacklist = {str(x) for x in bl}
        except Exception:
            pass
