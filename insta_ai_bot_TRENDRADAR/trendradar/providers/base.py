"""
📜 قرارداد پرووایدر (Provider Contract)
=======================================

هر پیام‌رسان (تلگرام، اینستاگرام، روبیکا، بله، واتساپ، ...) باید
`MessengerProvider` را پیاده کند.

قواعد طلایی
-----------
1. متدها **async** هستند (چون همه‌ی SDKهای مدرن async دارند؛ اگر SDK شما
   sync است، داخل متد از `asyncio.to_thread` استفاده کنید).
2. خطاهای بومی کتابخانه را به خطاهای `trendradar.errors` ترجمه کنید.
3. داده‌های خام را به مدل‌های `trendradar.models` تبدیل کنید — هیچ تایپ
   بومی کتابخانه‌ای نباید از مرز پرووایدر بیرون بزند.
4. اگر قابلیتی را پشتیبانی نمی‌کنید، آن را به `capabilities` اضافه نکنید
   و یک نتیجه‌ی خالی برگردانید (نه استثنا).

اضافه‌کردن پیام‌رسان جدید
------------------------
    from trendradar.providers.base import Capability, MessengerProvider

    class BaleProvider(MessengerProvider):
        name = "bale"
        display_name = "بله"
        capabilities = {Capability.SEARCH_HASHTAG, Capability.SEARCH_IN_CHANNEL}

        async def connect(self): ...
        ...

    register_provider(BaleProvider)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import List, Optional, Sequence

from ..models import ChannelRef, Post, ProviderHealth


class Capability(str, Enum):
    """
    قابلیت‌های اختیاری یک پیام‌رسان.

    پاییپ‌لاین بر اساس این‌ها تصمیم می‌گیرد کدام مرحله را اجرا کند و
    چه پیامی به کاربر نشان دهد.
    """

    SEARCH_HASHTAG = "search_hashtag"          # جستجوی هشتگ در سطح پلتفرم
    SEARCH_IN_CHANNEL = "search_in_channel"    # جستجوی هشتگ داخل یک کانال خاص
    SEARCH_KEYWORD = "search_keyword"          # جستجوی کلمه‌ی آزاد
    DISCOVER_CHANNELS = "discover_channels"    # پیدا کردن کانال جدید
    DISCOVER_RECOMMENDED = "discover_recommended"  # کانال‌های پیشنهادی مشابه
    JOIN_CHANNEL = "join_channel"              # عضویت خودکار
    LIST_JOINED = "list_joined"                # لیست کانال‌های عضو
    GET_STATS = "get_stats"                    # آمار کانال (اعضا، بازدید)
    LEAVE_CHANNEL = "leave_channel"
    POST_PERMALINK = "post_permalink"          # لینک دائمی به پست


class MessengerProvider(ABC):
    """
    کلاس پایه‌ی همه‌ی پرووایدرها.

    زیرکلاس‌ها باید حداقل این‌ها را تعریف کنند:
        name            : شناسه‌ی ماشینی یکتا ("telegram")
        display_name    : نام نمایشی فارسی ("تلگرام")
        capabilities    : مجموعه‌ی Capability
    """

    # ── فراداده ────────────────────────────────────────────
    name: str = "base"
    display_name: str = "پایه"
    capabilities: set[Capability] = set()

    def __init__(self, settings=None):
        self.settings = settings
        self._connected = False
        self._last_error: Optional[str] = None

    # ── کمکی ──────────────────────────────────────────────
    def supports(self, cap: Capability) -> bool:
        return cap in self.capabilities

    def requires(self, cap: Capability) -> None:
        """اگر قابلیت پشتیبانی نشود، استثنا می‌دهد (برای استفاده‌ی داخلی)"""
        if not self.supports(cap):
            raise NotImplementedError(
                f"پرووایدر «{self.display_name}» قابلیت «{cap.value}» را پشتیبانی نمی‌کند"
            )

    # ── چرخه‌ی عمر ─────────────────────────────────────────
    @abstractmethod
    async def connect(self) -> None:
        """برقراری اتصال/لاگین. در صورت نیاز به احراز هویت `AuthRequired` بدهد."""

    @abstractmethod
    async def disconnect(self) -> None:
        """بستن اتصال و آزادسازی منابع"""

    @abstractmethod
    async def health(self) -> ProviderHealth:
        """وضعیت سلامت — برای نمایش در پنل"""

    # ── کشف کانال ─────────────────────────────────────────
    @abstractmethod
    async def discover_channels(
        self,
        query: str,
        *,
        limit: int = 20,
        min_members: int = 0,
        exclude: Optional[Sequence[str]] = None,
    ) -> List[ChannelRef]:
        """
        پیدا کردن کانال/گروه بر اساس یک عبارت (هشتگ، کلمه، یا نام).

        :param exclude: شناسه‌هایی که نباید برگردند (قبلاً دیده‌شده‌ها)
        """

    async def discover_recommended(self, seed: ChannelRef, *, limit: int = 10) -> List[ChannelRef]:
        """
        کانال‌های مشابه یک کانال (الگوریتم پیشنهاد پلتفرم).
        اختیاری — پیش‌فرض خالی برمی‌گرداند.
        """
        return []

    # ── جستجو ─────────────────────────────────────────────
    @abstractmethod
    async def search_hashtag(
        self,
        tag: str,
        *,
        channels: Sequence[ChannelRef] = (),
        limit: int = 50,
        since: Optional[datetime] = None,
    ) -> List[Post]:
        """
        جستجوی یک هشتگ و برگرداندن پست‌های مطابق.

        :param channels: اگر خالی باشد، جستجوی سراسری انجام می‌شود؛
                         وگرنه فقط داخل همین کانال‌ها.
        :param since:    فقط پست‌های جدیدتر از این زمان
        """

    async def search_keywords(
        self,
        keywords: Sequence[str],
        *,
        channels: Sequence[ChannelRef] = (),
        limit: int = 50,
        since: Optional[datetime] = None,
    ) -> List[Post]:
        """
        جستجوی کلمه‌ی آزاد (اختیاری).
        پیش‌فرض: هر کلمه را به search_hashtag پاس می‌دهد.
        """
        out: List[Post] = []
        per = max(1, limit // max(1, len(keywords)))
        for kw in keywords:
            out.extend(await self.search_hashtag(kw, channels=channels, limit=per, since=since))
        return out

    # ── عضویت ─────────────────────────────────────────────
    @abstractmethod
    async def join_channel(self, channel: ChannelRef) -> ChannelRef:
        """
        عضویت در کانال.

        باید از گاردهای ضد‌بن عبور کند. در صورت رد شدن، `GuardRejected`
        یا `RateLimited` یا `DailyCapReached` بدهد.
        """

    @abstractmethod
    async def list_joined(self, *, limit: int = 200) -> List[ChannelRef]:
        """لیست کانال‌هایی که اکانت در آن‌ها عضو است"""

    async def leave_channel(self, channel: ChannelRef) -> bool:
        """ترک کانال (اختیاری)"""
        return False

    # ── آمار و رزولوشن ────────────────────────────────────
    @abstractmethod
    async def resolve(self, identifier: str) -> Optional[ChannelRef]:
        """
        تبدیل «@username» یا لینک به ChannelRef.

        :return: None اگر پیدا نشد
        """

    async def refresh_stats(self, channel: ChannelRef) -> ChannelRef:
        """به‌روزرسانی آمار کانال (اعضا، about). پیش‌فرض: بدون تغییر"""
        return channel

    # ── ذخیره/بازیابی وضعیت بین اجراها (اختیاری) ──────────
    def export_state(self) -> dict:
        """وضعیت قابل‌ذخیره (مثل شمارنده‌ی روزانه‌ی عضویت)"""
        return {}

    def import_state(self, state: dict) -> None:
        """بازگرداندن وضعیت ذخیره‌شده"""
        return None

    # ── نمایش ─────────────────────────────────────────────
    def __repr__(self) -> str:
        caps = ",".join(sorted(c.value for c in self.capabilities))
        return f"<{type(self).__name__} name={self.name} caps=[{caps}] connected={self._connected}>"
