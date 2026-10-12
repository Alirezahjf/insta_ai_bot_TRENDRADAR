"""
🔎 جستجوی هشتگ در تلگرام
========================

دو مسیر جستجو، هر کدام با متد واقعی Telethon:

┌──────────────────────┬────────────────────────────────┬─────────────────────┐
│ استراتژی             │ متد                            │ کاربرد              │
├──────────────────────┼────────────────────────────────┼─────────────────────┤
│ جستجوی سراسری        │ messages.SearchGlobalRequest   │ بی‌نیاز از کشف/عضویت│
│ جستجوی تاریخ چنل     │ client.iter_messages(search=)  │ تکمیل نتایج چنل‌های │
│                      │                                │ عضو                 │
└──────────────────────┴────────────────────────────────┴─────────────────────┘

جستجوی هشتگ در اسکژول ابتدا سراسری است؛ جستجوی تاریخ کانال‌های عضو فقط
وقتی نتیجه کم باشد به‌عنوان مسیر تکمیلی استفاده می‌شود. هیچ عضویتی خودکار
انجام نمی‌شود.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, Sequence

from ...errors import DailyCapReached, RateLimited
from ...models import ChannelRef, Post, normalize_hashtag
from .mapper import messages_to_posts

log = logging.getLogger("trendradar.telegram.search")

# حداکثر صفحه‌بندی برای جلوگیری از حلقه‌ی بی‌پایان
_MAX_PAGES = 10


def _import():
    from telethon import functions  # type: ignore
    from telethon.tl import types  # type: ignore

    return functions, types


class TelegramSearch:
    """
    انجام جستجوهای تلگرام روی یک کلاینت.

    نمونه:
        s = TelegramSearch(tg_client)
        posts = await s.search_hashtag_in_channels("ترند", channels, limit=50)
    """

    def __init__(self, tg_client):
        self.tg = tg_client

    # ═════════════════════════════════════════════════════════
    # ۱) جستجوی هشتگ دقیق داخل کانال‌های مشخص
    #    متد واقعی: client.iter_messages(search=...)
    # ═════════════════════════════════════════════════════════
    async def search_hashtag_in_channel(
        self,
        channel: ChannelRef,
        tag: str,
        *,
        limit: int = 50,
        since: Optional[datetime] = None,
        max_pages: int = _MAX_PAGES,
    ) -> List[Post]:
        """جست‌وجوی یک هشتگ دقیقاً در تاریخ همان چت/کانال."""
        tag = normalize_hashtag(tag)
        if not tag or limit <= 0:
            return []
        posts = await self.search_in_chat_history(
            channel, tag, limit=limit, since=since
        )
        exact = [post for post in posts if tag in post.hashtags]
        log.debug("  #%s در «%s» → %d پست", tag, channel.title, len(exact))
        return exact[:limit]

    async def search_hashtag_in_channels(
        self,
        tag: str,
        channels: Sequence[ChannelRef],
        *,
        limit: int = 50,
        since: Optional[datetime] = None,
        concurrency: int = 3,
    ) -> List[Post]:
        """
        جستجوی یک هشتگ در چند کانال به‌صورت ترتیبی تا گارد نرخ و سقف روزانه
        رعایت شوند. آرگومان concurrency برای سازگاری API حفظ شده است.
        """
        if not channels or limit <= 0:
            return []

        per = max(1, limit // len(channels) + 1)
        out: List[Post] = []
        for channel in channels:
            try:
                out.extend(await self.search_hashtag_in_channel(
                    channel, tag, limit=per, since=since
                ))
            except (RateLimited, DailyCapReached):
                raise
            except Exception as exc:
                log.debug("خطا در جستجوی %s: %s", channel.uid, exc)
            if len(out) >= limit:
                break
        return self._dedupe(out)[:limit]

    # ═════════════════════════════════════════════════════════
    # ۲) جستجوی **سراسری** — بدون نیاز به عضو بودن
    #    متد واقعی: messages.SearchGlobalRequest  [id=0x6126a43c]
    # ═════════════════════════════════════════════════════════
    async def search_global(
        self,
        term: str,
        *,
        limit: int = 50,
        since: Optional[datetime] = None,
        only_groups: bool = False,
        only_channels: bool = False,
        max_pages: int = _MAX_PAGES,
    ) -> List[Post]:
        """
        جستجوی سراسری در همه‌ی چت‌های عمومی تلگرام.

        مزیت بزرگ: **لازم نیست در کانال عضو باشی.**
        عیب: نتیجه‌ها محدودتر از جستجوی داخل کانال است.

        :param only_channels: فقط کانال‌ها (broadcasts_only=True)
        :param only_groups:   فقط گروه‌ها (groups_only=True)
        """
        functions, types = _import()

        term = (term or "").strip()
        if not term or limit <= 0:
            return []

        collected: List[Post] = []
        offset_rate = 0
        offset_id = 0
        offset_peer = types.InputPeerEmpty()

        for _page in range(max_pages):
            try:
                req_kwargs: dict = dict(
                    q=term,
                    filter=types.InputMessagesFilterEmpty(),
                    min_date=since,
                    max_date=None,
                    offset_rate=offset_rate,
                    offset_peer=offset_peer,
                    offset_id=offset_id,
                    limit=min(100, max(10, limit - len(collected))),
                )
                if only_groups:
                    req_kwargs["groups_only"] = True
                if only_channels:
                    req_kwargs["broadcasts_only"] = True

                result = await self.tg.request(functions.messages.SearchGlobalRequest(**req_kwargs))
            except (RateLimited, DailyCapReached):
                raise
            except Exception as e:
                log.debug("جستجوی سراسری «%s» ناموفق: %s", term, e)
                break

            msgs = getattr(result, "messages", None) or []
            # کانال هر پیام را از chats نتیجه پیدا کن
            chat_index = self._chat_index(getattr(result, "chats", None) or [])
            for m in msgs:
                ch = self._channel_of_message(m, chat_index)
                if ch is None:
                    continue
                p = messages_to_posts([m], channel=ch)[:1]
                if p:
                    collected.extend(self._filter_window(p, since))

            next_rate = getattr(result, "next_rate", None)
            if not next_rate or not msgs or len(collected) >= limit:
                break
            offset_rate = next_rate
            offset_id = getattr(msgs[-1], "id", 0) or 0
            await asyncio.sleep(0.5)

        return self._dedupe(collected)[:limit]

    # ═════════════════════════════════════════════════════════
    # ۳) جستجو در تاریخ یک چت
    #    متد واقعی: client.iter_messages(search=...) که داخلاً
    #    messages.SearchRequest  [id=0x29ee847a] صدا می‌زند
    # ═════════════════════════════════════════════════════════
    async def search_in_chat_history(
        self,
        channel: ChannelRef,
        term: str,
        *,
        limit: int = 50,
        since: Optional[datetime] = None,
    ) -> List[Post]:
        """
        جستجوی متنی/هشتگی در تاریخ یک چت با `iter_messages`.

        برای کانال‌هایی کاربرد دارد که پست‌هایشان در ایندکس هشتگ
        تلگرام نیست (کانال‌های خصوصی، گروه‌های بسته).
        """
        term = (term or "").strip().lstrip("#")
        if not term:
            return []

        out: List[Post] = []
        if self.tg.search_counter is not None:
            self.tg.search_counter.check("جستجو در تلگرام")
            self.tg.search_counter.bump()
        await self.tg.search_limiter.wait_async()
        try:
            entity = await self.tg.raw.get_input_entity(channel.native_id)
        except Exception as e:
            log.debug("get_input_entity ناموفق: %s", e)
            return []

        try:
            async for m in self.tg.raw.iter_messages(
                entity, limit=limit, search=term, offset_date=since, reverse=False
            ):
                batch = messages_to_posts([m], channel=channel)
                out.extend(self._filter_window(batch, since))
                if len(out) >= limit:
                    break
                await asyncio.sleep(0.15)     # نرم‌کردن نرخ درخواست
        except (RateLimited, DailyCapReached):
            raise
        except Exception as e:
            log.debug("جستجو در تاریخ %s ناموفق: %s", channel.title, e)

        return self._dedupe(out)[:limit]

    # ═════════════════════════════════════════════════════════
    # کمکی‌ها
    # ═════════════════════════════════════════════════════════
    @staticmethod
    def _filter_window(posts: List[Post], since: Optional[datetime]) -> List[Post]:
        """فیلتر پنجره‌ی زمانی"""
        if since is None:
            return posts
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        return [p for p in posts if p.date >= since]

    @staticmethod
    def _dedupe(posts: List[Post]) -> List[Post]:
        """حذف تکراری بر اساس uid"""
        seen: set = set()
        out: List[Post] = []
        for p in posts:
            if p.uid in seen:
                continue
            seen.add(p.uid)
            out.append(p)
        return out

    @staticmethod
    def _chat_index(chats: Sequence[Any]) -> dict:
        """ساخت نقشه‌ی id → شیء چت برای پیدا کردن کانال هر پیام"""
        idx: dict = {}
        for c in chats:
            cid = getattr(c, "id", None)
            if cid is not None:
                idx[cid] = c
                # شناسه‌ی کانال در peer_id با پیشوند -100 می‌آید
                idx[-1000000000000 - cid] = c
        return idx

    def _channel_of_message(self, msg: Any, chat_index: dict) -> Optional[ChannelRef]:
        """پیدا کردن ChannelRef مربوط به یک پیام، از روی peer_id"""
        from .mapper import channel_to_ref

        peer = getattr(msg, "peer_id", None)
        if peer is None:
            return None
        for attr in ("channel_id", "chat_id", "user_id"):
            cid = getattr(peer, attr, None)
            if cid is None:
                continue
            ch = chat_index.get(cid) or chat_index.get(-1000000000000 - cid)
            if ch is not None:
                is_joined = getattr(ch, "left", None) is False
                return channel_to_ref(ch, joined=is_joined)
        return None


def default_since(hours: int) -> datetime:
    """پنجره‌ی زمانی پیش‌فرض"""
    return datetime.now(timezone.utc) - timedelta(hours=max(1, hours))
