"""
🔭 کشف کانال و گروه در تلگرام
============================

چهار منبع کشف، همه با متدهای واقعی Telethon 1.45.0:

┌────────────────────────────────────────┬──────────────────────────────────┐
│ contacts.SearchRequest [0x5f58d0f]     │ جستجوی یوزرنیم/عنوان — منبع اصلی │
│ channels.GetChannelRecommendations     │ کانال‌های مشابه (الگوریتم تلگرام)│
│ client.iter_dialogs()                  │ کانال‌هایی که خودمان عضوییم      │
│ channels.GetFullChannelRequest         │ آمار دقیق (اعضا، آنلاین، about)   │
└────────────────────────────────────────┴──────────────────────────────────┘

استراتژی کشف مؤثر:
  1. جستجوی مستقیم عبارت (هشتگ/کلمه) با `contacts.Search`
  2. از هر کانال پیدا‌شده، «کانال‌های مشابه» را بگیر (خوشه‌ی موضوعی)
  3. نتیجه‌ها را فیلتر و امتیازدهی کن
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from ...errors import ChannelNotAccessible, ProviderError, RateLimited
from ...models import ChannelKind, ChannelRef
from .mapper import channel_to_ref, user_to_ref

log = logging.getLogger("trendradar.telegram.discovery")

# الگوی لینک/یوزرنیم تلگرام
_TME_RE = re.compile(
    r"^(?:https?://)?(?:t\.me|telegram\.me|telegram\.dog)/(?:s/)?(?P<name>[A-Za-z0-9_]{4,64})/?$",
    re.IGNORECASE,
)
_USERNAME_RE = re.compile(r"^@?([A-Za-z0-9_]{4,64})$")


def parse_identifier(raw: str) -> Optional[str]:
    """
    استخراج یوزرنیم از ورودی کاربر.

    >>> parse_identifier("https://t.me/trend_ir")
    'trend_ir'
    >>> parse_identifier("@trend_ir")
    'trend_ir'
    >>> parse_identifier("trend_ir")
    'trend_ir'
    >>> parse_identifier("سلام")   # فارسی → None (یوزرنیم تلگرام فقط لاتین)
    """
    if not raw:
        return None
    s = str(raw).strip()
    m = _TME_RE.match(s)
    if m:
        name = m.group("name")
        # مسیرهای رزروشده‌ی تلگرام
        if name.lower() in ("joinchat", "addstickers", "share", "s", "iv"):
            return None
        return name
    m = _USERNAME_RE.match(s)
    return m.group(1) if m else None


class TelegramDiscovery:
    """کشف کانال در تلگرام"""

    def __init__(self, tg_client):
        self.tg = tg_client
        self._stats_cache: Dict[str, tuple] = {}

    # ═════════════════════════════════════════════════════════
    # ۱) جستجوی کانال با عبارت
    #    متد واقعی: contacts.SearchRequest(q, limit, broadcasts=None, bots=None)
    # ═════════════════════════════════════════════════════════
    async def search_channels(
        self,
        query: str,
        *,
        limit: int = 20,
        min_members: int = 0,
        only_channels: bool = True,
        exclude: Optional[Iterable[str]] = None,
    ) -> List[ChannelRef]:
        """
        پیدا کردن کانال/گروه با یک عبارت.

        :param only_channels: اگر True، فقط کانال‌های broadcast (نه گروه و نه کاربر)
        """
        functions, types = _import()
        query = (query or "").strip()
        if not query:
            return []

        exclude_set: Set[str] = set(exclude or ())

        try:
            result = await self.tg.request(
                functions.contacts.SearchRequest(
                    q=query,
                    limit=min(100, max(5, limit * 2)),
                    broadcasts=True if only_channels else None,
                )
            )
        except RateLimited:
            raise
        except Exception as e:
            log.debug("contacts.Search «%s» ناموفق: %s", query, e)
            return []

        out: List[ChannelRef] = []
        for ch in getattr(result, "chats", None) or []:
            ref = channel_to_ref(ch, joined=getattr(ch, "left", None) is False)
            if ref is None:
                continue
            if self._accept(ref, min_members=min_members, exclude=exclude_set):
                out.append(ref)

        # کاربرهای نتیجه را هم اضافه کن (بعضی «کانال»ها در واقع کاربرند)
        if not only_channels:
            for u in getattr(result, "users", None) or []:
                ref = user_to_ref(u)
                if ref and ref.username and self._accept(ref, min_members=0, exclude=exclude_set):
                    out.append(ref)

        log.debug("کشف «%s» → %d کانال", query, len(out))
        return out[:limit]

    # ═════════════════════════════════════════════════════════
    # ۲) کانال‌های مشابه
    #    متد واقعی: channels.GetChannelRecommendationsRequest(channel=None)
    #    خروجی: messages.Chats(chats=[...])
    # ═════════════════════════════════════════════════════════
    async def get_similar_channels(
        self,
        seed: ChannelRef,
        *,
        limit: int = 10,
        min_members: int = 0,
        exclude: Optional[Iterable[str]] = None,
    ) -> List[ChannelRef]:
        """
        کانال‌های مشابه یک کانال — بر اساس خوشه‌بندی موضوعی خود تلگرام.

        این روش برد خوبی دارد چون معیارش «موضوع» است، نه تطابق نام.
        """
        functions, _types = _import()
        exclude_set: Set[str] = set(exclude or ())
        exclude_set.add(seed.uid)

        try:
            entity = await self.tg.raw.get_input_entity(seed.native_id)
        except Exception as e:
            log.debug("get_input_entity برای %s ناموفق: %s", seed.uid, e)
            return []

        try:
            result = await self.tg.request(
                functions.channels.GetChannelRecommendationsRequest(channel=entity)
            )
        except RateLimited:
            raise
        except Exception as e:
            log.debug("پیشنهاد مشابه برای %s ناموفق: %s", seed.title, e)
            return []

        out: List[ChannelRef] = []
        for ch in getattr(result, "chats", None) or []:
            ref = channel_to_ref(ch)
            if ref and self._accept(ref, min_members=min_members, exclude=exclude_set):
                out.append(ref)

        log.debug("مشابه‌های «%s» → %d کانال", seed.title, len(out))
        return out[:limit]

    # ═════════════════════════════════════════════════════════
    # ۳) کانال‌هایی که عضو هستیم
    #    متد واقعی: client.iter_dialogs()
    # ═════════════════════════════════════════════════════════
    async def list_joined(
        self,
        *,
        limit: int = 200,
        kinds: Optional[Sequence[ChannelKind]] = None,
    ) -> List[ChannelRef]:
        """
        لیست کانال/گروه‌هایی که اکانت در آن‌ها عضو است.

        این‌ها ارزشمندترین منبع‌اند چون:
          • نیازی به عضویت جدید نداریم (بی‌خطر برای اکانت)
          • می‌توانیم در تاریخ‌شان جستجوی عمیق بزنیم
        """
        out: List[ChannelRef] = []
        want = set(kinds) if kinds else {ChannelKind.BROADCAST, ChannelKind.SUPERGROUP, ChannelKind.GROUP}

        try:
            async for dialog in self.tg.raw.iter_dialogs(limit=limit):
                entity = getattr(dialog, "entity", None)
                if entity is None:
                    continue
                if not getattr(dialog, "is_channel", False) and not getattr(dialog, "is_group", False):
                    continue
                ref = channel_to_ref(entity, joined=True)
                if ref and ref.kind in want:
                    out.append(ref)
        except RateLimited:
            raise
        except Exception as e:
            log.warning("خواندن دیالوگ‌ها ناموفق: %s", e)

        log.info("کانال‌های عضو: %d", len(out))
        return out[:limit]

    # ═════════════════════════════════════════════════════════
    # ۴) آمار کامل کانال
    #    متد واقعی: channels.GetFullChannelRequest(channel) → ChannelFull
    # ═════════════════════════════════════════════════════════
    async def enrich_stats(self, channel: ChannelRef, *, max_age_sec: int = 3600) -> ChannelRef:
        """
        پر کردن آمار دقیق کانال (اعضا، آنلاین، about).

        نتیجه cache می‌شود تا درخواست تکراری نزنیم.
        """
        import time

        key = channel.uid
        cached = self._stats_cache.get(key)
        if cached and (time.time() - cached[0]) < max_age_sec:
            return cached[1]

        functions, _types = _import()
        try:
            entity = await self.tg.raw.get_input_entity(channel.native_id)
            full = await self.tg.request(functions.channels.GetFullChannelRequest(channel=entity))
            full_chat = getattr(full, "full_chat", None)
            chats = getattr(full, "chats", None) or []
            base = chats[0] if chats else None
            enriched = channel_to_ref(base if base is not None else _FallbackChannel(channel), full=full)
            if enriched is not None:
                enriched.joined = channel.joined
                self._stats_cache[key] = (time.time(), enriched)
                return enriched
        except RateLimited:
            raise
        except ChannelNotAccessible as e:
            log.debug("آمار %s در دسترس نیست: %s", channel.uid, e)
        except Exception as e:
            log.debug("enrich_stats برای %s ناموفق: %s", channel.uid, e)

        self._stats_cache[key] = (time.time(), channel)
        return channel

    # ═════════════════════════════════════════════════════════
    # ۵) رزولوشن شناسه به ChannelRef
    # ═════════════════════════════════════════════════════════
    async def resolve(self, identifier: str) -> Optional[ChannelRef]:
        """
        تبدیل لینک/یوزرنیم/شناسه به ChannelRef.

        >>> await d.resolve("https://t.me/trend_ir")
        >>> await d.resolve("@trend_ir")
        >>> await d.resolve("1234567890")
        """
        if not identifier:
            return None

        s = str(identifier).strip()

        # شناسه‌ی عددی
        if s.lstrip("-").isdigit():
            try:
                entity = await self.tg.raw.get_entity(int(s))
                return channel_to_ref(entity)
            except Exception as e:
                log.debug("رزولوشن شناسه‌ی عددی %s ناموفق: %s", s, e)
                return None

        username = parse_identifier(s)
        if not username:
            return None

        try:
            entity = await self.tg.raw.get_entity(username)
        except Exception as e:
            log.debug("رزولوشن @%s ناموفق: %s", username, e)
            return None

        ref = channel_to_ref(entity)
        if ref is None:
            ref = user_to_ref(entity)
        return ref

    # ═════════════════════════════════════════════════════════
    # کمکی‌ها
    # ═════════════════════════════════════════════════════════
    @staticmethod
    def _accept(ref: ChannelRef, *, min_members: int, exclude: Set[str]) -> bool:
        """آیا این کانال قابل قبول است؟ فیلترهای ایمنی این‌جا اعمال می‌شوند"""
        if ref.uid in exclude:
            return False
        if not ref.title:
            return False
        # کانال‌های اسکم/فیک هرگز
        if ref.is_scam or ref.is_fake:
            return False
        # کانال‌های بدون یوزرنیم را نمی‌توان در هشتگ سراسری جستجو کرد
        if not ref.username and ref.extra.get("forbidden"):
            return False
        if min_members and ref.members is not None and ref.members < min_members:
            return False
        return True

    @staticmethod
    def dedupe(channels: Iterable[ChannelRef]) -> List[ChannelRef]:
        """حذف تکراری بر اساس uid"""
        seen: Set[str] = set()
        out: List[ChannelRef] = []
        for c in channels:
            if c.uid in seen:
                continue
            seen.add(c.uid)
            out.append(c)
        return out

    @staticmethod
    def sort_by_relevance(channels: List[ChannelRef], query: str = "") -> List[ChannelRef]:
        """
        مرتب‌سازی بر اساس «ارتباط»: کانال بزرگ‌تر و تاییدشده‌تر اولویت دارد،
        و اگر عبارت جستجو در عنوان باشد امتیاز بیشتری می‌گیرد.
        """
        q = (query or "").lower().replace("_", " ").strip()

        def score(c: ChannelRef) -> float:
            s = 0.0
            if q and q in (c.title or "").lower():
                s += 100
            if q and c.username and q.replace(" ", "") in c.username.lower():
                s += 60
            if c.verified:
                s += 25
            if c.members:
                import math

                s += min(50.0, math.log10(max(1, c.members)) * 12)
            if c.kind == ChannelKind.BROADCAST:
                s += 8          # کانال برای ترند‌یابی بهتر از گروه است
            if c.join_request:
                s -= 15
            return -s               # منفی برای مرتب‌سازی صعودی

        return sorted(channels, key=score)


def _import():
    from telethon import functions  # type: ignore
    from telethon.tl import types  # type: ignore

    return functions, types


class _FallbackChannel:
    """
    شیء جانشین وقتی `GetFullChannelRequest` خود شیء کانال را برنگرداند.

    فقط فیلدهایی که `channel_to_ref` می‌خواند را دارد.
    """

    __slots__ = ("id", "title", "username", "access_hash", "participants_count", "broadcast", "megagroup")

    def __init__(self, ref: ChannelRef):
        self.id = ref.native_id
        self.title = ref.title
        self.username = ref.username
        self.access_hash = ref.access_hash
        self.participants_count = ref.members
        self.broadcast = ref.kind == ChannelKind.BROADCAST
        self.megagroup = ref.kind in (ChannelKind.SUPERGROUP, ChannelKind.GROUP)
