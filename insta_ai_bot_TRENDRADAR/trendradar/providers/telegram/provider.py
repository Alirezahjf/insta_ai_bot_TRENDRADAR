"""
🇮🇷 پرووایدر تلگرام — پیاده‌سازی قرارداد `MessengerProvider`
==========================================================

این کلاس همه‌ی قطعات تلگرام (client / search / discovery / membership) را
به یک واسط یکسان برای موتور ترند‌یابی تبدیل می‌کند.

نکته‌ی معماری: موتور بالادستی (`pipeline`) هیچ‌وقت مستقیم با Telethon کار
نمی‌کند و نمی‌داند این پرووایدر چگونه پیاده شده. اگر فردا بخواهی همین
کار را با Pyrogram یا کتابخانه‌ی دیگری انجام بدهی، فقط این پوشه عوض می‌شود.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from ...errors import AuthRequired, ConfigError, ProviderNotAvailable
from ...models import ChannelKind, ChannelRef, Post, ProviderHealth, utcnow
from ..base import Capability, MessengerProvider
from .client import TrendTelegramClient
from .discovery import TelegramDiscovery, parse_identifier
from .membership import TelegramMembership
from .search import TelegramSearch

log = logging.getLogger("trendradar.telegram")


class TelegramProvider(MessengerProvider):
    """
    پرووایدر تلگرام بر پایه‌ی MTProto (Telethon).

    راه‌اندازی:
        p = TelegramProvider(settings)     # settings = TrendSettings
        await p.connect()                  # در صورت نیاز AuthRequired می‌دهد
        # اگر لاگین نبود:
        await p.auth_send_code()
        await p.auth_sign_in("12345")
        # و در صورت داشتن رمز دو مرحله‌ای:
        await p.auth_sign_in_password("mypassword")
    """

    name = "telegram"
    display_name = "تلگرام"
    capabilities = {
        Capability.SEARCH_HASHTAG,
        Capability.SEARCH_IN_CHANNEL,
        Capability.SEARCH_KEYWORD,
        Capability.DISCOVER_CHANNELS,
        Capability.DISCOVER_RECOMMENDED,
        Capability.JOIN_CHANNEL,
        Capability.LIST_JOINED,
        Capability.GET_STATS,
        Capability.LEAVE_CHANNEL,
        Capability.POST_PERMALINK,
    }

    def __init__(self, settings=None):
        # settings می‌تواند TrendSettings یا TelegramSettings باشد
        tg_settings = getattr(settings, "telegram", None) or settings
        super().__init__(tg_settings)
        if tg_settings is None:
            raise ConfigError("تنظیمات تلگرام داده نشده")
        self.tg = TrendTelegramClient(tg_settings)
        self.search_ops = TelegramSearch(self.tg)
        self.discovery_ops = TelegramDiscovery(self.tg)
        self.membership_ops = TelegramMembership(self.tg)

    # ═════════════════════════════════════════════════════════
    # چرخه‌ی عمر
    # ═════════════════════════════════════════════════════════
    async def connect(self) -> None:
        try:
            await self.tg.connect(require_auth=True)
            self._connected = True
            self._last_error = None
        except AuthRequired as e:
            self._connected = False
            self._last_error = str(e)
            raise
        except Exception as e:
            self._connected = False
            self._last_error = f"{type(e).__name__}: {e}"
            raise

    async def disconnect(self) -> None:
        await self.tg.disconnect()
        self._connected = False

    async def health(self) -> ProviderHealth:
        caps = sorted(c.value for c in self.capabilities)
        h = ProviderHealth(
            provider=self.name,
            name=self.display_name,
            available=True,
            capabilities=caps,
        )
        if not self.tg.settings.configured:
            h.available = False
            h.error = "TG_API_ID / TG_API_HASH تنظیم نشده"
            return h
        try:
            authorized = await self.tg.connect(require_auth=False)
            h.authorized = bool(authorized)
            if authorized and self.tg.me is not None:
                uname = getattr(self.tg.me, "username", None)
                h.account = f"@{uname}" if uname else str(getattr(self.tg.me, "id", "?"))
        except ProviderNotAvailable as e:
            h.available = False
            h.error = str(e)
        except AuthRequired as e:
            h.authorized = False
            h.error = str(e)
        except Exception as e:
            h.error = f"{type(e).__name__}: {e}"

        blocked = self.tg.search_limiter.blocked_for
        if blocked > 0:
            from datetime import timedelta

            h.rate_limited_until = utcnow() + timedelta(seconds=blocked)
        return h

    # ═════════════════════════════════════════════════════════
    # احراز هویت تعاملی (برای پنل ربات)
    # ═════════════════════════════════════════════════════════
    async def auth_send_code(self, phone: Optional[str] = None) -> str:
        return await self.tg.send_code(phone)

    async def auth_sign_in(self, code: str) -> str:
        msg = await self.tg.sign_in_code(code)
        self._connected = True
        return msg

    async def auth_sign_in_password(self, password: Optional[str] = None) -> str:
        msg = await self.tg.sign_in_password(password)
        self._connected = True
        return msg

    async def is_authorized(self) -> bool:
        return await self.tg.connect(require_auth=False)

    # ═════════════════════════════════════════════════════════
    # کشف کانال
    # ═════════════════════════════════════════════════════════
    async def discover_channels(
        self,
        query: str,
        *,
        limit: int = 20,
        min_members: int = 0,
        exclude: Optional[Sequence[str]] = None,
    ) -> List[ChannelRef]:
        ex = list(exclude or [])
        found = await self.discovery_ops.search_channels(
            query, limit=limit, min_members=min_members, exclude=ex
        )

        # لینک/یوزرنیم مستقیم هم پشتیبانی می‌شود
        if not found and parse_identifier(query):
            ref = await self.discovery_ops.resolve(query)
            if ref is not None:
                found = [ref]

        return self.discovery_ops.sort_by_relevance(found, query)[:limit]

    async def discover_recommended(self, seed: ChannelRef, *, limit: int = 10) -> List[ChannelRef]:
        return await self.discovery_ops.get_similar_channels(seed, limit=limit)

    # ═════════════════════════════════════════════════════════
    # جستجو
    # ═════════════════════════════════════════════════════════
    async def search_hashtag(
        self,
        tag: str,
        *,
        channels: Sequence[ChannelRef] = (),
        limit: int = 50,
        since: Optional[datetime] = None,
    ) -> List[Post]:
        """
        جستجوی هشتگ به‌صورت سراسری؛ به کشف کانال یا عضویت وابسته نیست.

        اگر نتیجه کم باشد، مرحله‌ی تکمیلی تاریخ کانال‌های عضو در pipeline اجرا
        می‌شود تا یک جستجو در چند لایه دوباره‌کاری نشود.
        """
        return await self.search_ops.search_global(f"#{tag}", limit=limit, since=since)

    async def search_keywords(
        self,
        keywords: Sequence[str],
        *,
        channels: Sequence[ChannelRef] = (),
        limit: int = 50,
        since: Optional[datetime] = None,
    ) -> List[Post]:
        """جستجوی کلمه‌ی آزاد (بدون #) — هم در کانال‌ها هم سراسری"""
        if not keywords:
            return []
        per = max(5, limit // len(keywords) + 1)
        out: List[Post] = []
        for kw in keywords:
            if channels:
                out.extend(
                    await self.search_ops.search_in_channels_history(
                        kw, channels, limit=per, since=since
                    )
                )
            if len(out) < per:
                out.extend(await self.search_ops.search_global(kw, limit=per, since=since))
        return out[:limit]

    async def search_in_channels_history(
        self,
        term: str,
        channels: Sequence[ChannelRef],
        *,
        limit: int = 50,
        since: Optional[datetime] = None,
    ) -> List[Post]:
        """
        جستجو در تاریخ کانال‌ها (روش سوم) — برای کانال‌های خصوصی یا
        پست‌هایی که در ایندکس هشتگ تلگرام نیستند.
        """
        out: List[Post] = []
        per = max(5, limit // max(1, len(channels)) + 1)
        for ch in channels:
            try:
                out.extend(
                    await self.search_ops.search_in_chat_history(
                        ch, term, limit=per, since=since
                    )
                )
            except Exception as e:
                log.debug("تاریخ %s ناموفق: %s", ch.uid, e)
            if len(out) >= limit:
                break
        return out[:limit]

    # ═════════════════════════════════════════════════════════
    # عضویت
    # ═════════════════════════════════════════════════════════
    async def join_channel(self, channel: ChannelRef, *, dry_run: bool = False) -> ChannelRef:
        return await self.membership_ops.join(channel, dry_run=dry_run)

    async def leave_channel(self, channel: ChannelRef) -> bool:
        return await self.membership_ops.leave(channel)

    async def list_joined(self, *, limit: int = 200) -> List[ChannelRef]:
        return await self.discovery_ops.list_joined(limit=limit)

    # ═════════════════════════════════════════════════════════
    # رزولوشن و آمار
    # ═════════════════════════════════════════════════════════
    async def resolve(self, identifier: str) -> Optional[ChannelRef]:
        return await self.discovery_ops.resolve(identifier)

    async def refresh_stats(self, channel: ChannelRef) -> ChannelRef:
        return await self.discovery_ops.enrich_stats(channel)

    # ═════════════════════════════════════════════════════════
    # وضعیت
    # ═════════════════════════════════════════════════════════
    def export_state(self) -> dict:
        return self.tg.guards_state()

    def import_state(self, state: dict) -> None:
        self.tg.load_guards_state(state or {})

    def guard_stats(self) -> Dict[str, Any]:
        """آمار گاردهای ضد‌بن — برای نمایش در پنل ربات"""
        return self.membership_ops.stats()

    def account_label(self) -> str:
        """نام اکانت متصل برای نمایش"""
        me = self.tg.me
        if me is None:
            return "—"
        uname = getattr(me, "username", None)
        name = getattr(me, "first_name", "") or ""
        return f"{name} (@{uname})" if uname else f"{name} ({getattr(me, 'id', '?')})"
