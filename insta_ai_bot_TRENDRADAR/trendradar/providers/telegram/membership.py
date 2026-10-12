"""
🚪 عضویت در کانال — با گارد ضد‌بن
=================================

متدهای واقعی Telethon 1.45.0:
    channels.JoinChannelRequest(channel)   [0x7f6a1e22]  → Updates
    channels.LeaveChannelRequest(channel)  [0x74f7b6d4]  → Updates

خطاهایی که باید مدیریت شوند:
    UserAlreadyParticipantError → قبلاً عضو بوده‌ایم (موفقیت حساب می‌شود)
    InviteRequestSentError      → درخواست عضویت ارسال شد، منتظر تایید ادمین
    ChannelsTooMuchError        → اکانت در کانال‌های بیش از حد عضو است
    ChatAdminRequiredError      → کانال اجازه‌ی عضویت خودکار نمی‌دهد

⚠️ مهم‌ترین ریسک بن برای یک userbot، عضویت سریع در چنل‌های زیاد است.
`JoinGuard` این را کنترل می‌کند: فاصله‌ی تصادفی + سقف روزانه + خنک‌شدن
بعد از خطا.
"""
from __future__ import annotations

import logging
from typing import Optional

from ...errors import (
    ChannelNotAccessible,
    DailyCapReached,
    GuardRejected,
    JoinFailed,
    RateLimited,
)
from ...models import ChannelRef
from .mapper import channel_to_ref

log = logging.getLogger("trendradar.telegram.membership")


def _import():
    from telethon import functions  # type: ignore

    return functions


class TelegramMembership:
    """مدیریت عضویت با عبور از گاردها"""

    def __init__(self, tg_client):
        self.tg = tg_client

    # ═════════════════════════════════════════════════════════
    # عضویت
    # ═════════════════════════════════════════════════════════
    async def join(self, channel: ChannelRef, *, dry_run: bool = False) -> ChannelRef:
        """
        عضویت در کانال با رعایت همه‌ی گاردها.

        :param dry_run: فقط بررسی کن که می‌شود یا نه، بدون عضویت واقعی
        :raises GuardRejected / RateLimited / DailyCapReached / JoinFailed
        """
        functions = _import()
        guard = self.tg.join_guard

        # ── ۰) اگر قبلاً عضو هستیم، کاری نکن ──
        if channel.joined:
            log.debug("قبلاً عضو «%s» هستیم", channel.title)
            return channel

        # ── ۱) فیلترهای ایمنی ──
        if channel.is_scam or channel.is_fake:
            raise GuardRejected(f"«{channel.title}» به‌عنوان اسکم/فیک علامت خورده — عضویت رد شد")
        if channel.join_request and not self.tg.settings.join_on_request:
            raise GuardRejected(
                f"«{channel.title}» نیاز به تایید ادمین دارد — طبق تنظیمات رد شد"
            )

        # ── ۲) گارد نرخ و سقف روزانه ──
        guard.check(channel.uid)

        if dry_run:
            log.info("[dry-run] عضویت در «%s» مجاز است", channel.title)
            return channel

        # ── ۳) عضویت واقعی ──
        try:
            entity = await self.tg.raw.get_input_entity(channel.native_id)
        except Exception as e:
            raise JoinFailed(f"شناسه‌ی کانال «{channel.title}» قابل تبدیل نبود: {e}") from e

        _errors = _import_errors()
        try:
            updates = await self.tg.request(
                functions.channels.JoinChannelRequest(channel=entity),
                rate_limit=False,        # گارد عضویت خودش نرخ را کنترل می‌کند
            )
        except _errors.UserAlreadyParticipantError:
            log.info("قبلاً عضو «%s» بودیم", channel.title)
            guard.record_join(channel.uid)
            channel.joined = True
            return channel
        except _errors.InviteRequestSentError:
            log.info("درخواست عضویت «%s» ارسال شد (منتظر تایید ادمین)", channel.title)
            guard.record_join(channel.uid)
            channel.extra["pending_approval"] = True
            return channel
        except _errors.ChannelsTooMuchError as e:
            guard.record_error(channel.uid)
            raise DailyCapReached(
                "اکانت در تعداد کانال بیش از حد مجاز تلگرام عضو است. "
                "چند کانال قدیمی را ترک کن.",
                action="عضویت در کانال", cap=self.tg.settings.join_daily_cap,
            ) from e
        except _errors.FloodWaitError as e:
            guard.record_flood(e.seconds)
            raise RateLimited(
                f"تلگرام عضویت را محدود کرد. {e.seconds} ثانیه صبر لازم است.",
                retry_after=e.seconds, provider="telegram",
            ) from e
        except _errors.ChatAdminRequiredError as e:
            guard.record_error(channel.uid, permanent=True)
            raise JoinFailed(f"«{channel.title}» عضویت خودکار را نمی‌پذیرد") from e
        except _errors.ChannelPrivateError as e:
            guard.record_error(channel.uid, permanent=True)
            raise ChannelNotAccessible(f"«{channel.title}» خصوصی است") from e
        except Exception as e:
            guard.record_error(channel.uid)
            raise JoinFailed(f"عضویت در «{channel.title}» ناموفق: {type(e).__name__}: {e}") from e

        # ── ۴) ثبت و به‌روزرسانی وضعیت ──
        guard.record_join(channel.uid)
        channel.joined = True

        # شناسه‌ی جدید کانال را از خروجی updates استخراج کن (اگر ممکن باشد)
        try:
            for ch in getattr(updates, "chats", None) or []:
                if getattr(ch, "id", None) == channel.native_id:
                    fresh = channel_to_ref(ch, joined=True)
                    if fresh:
                        fresh.discovered_at = channel.discovered_at
                        fresh.extra.update(channel.extra)
                        log.info(
                            "✅ عضو شدیم: «%s» (اعضا: %s)",
                            fresh.title,
                            f"{fresh.members:,}" if fresh.members else "؟",
                        )
                        return fresh
        except Exception as e:
            log.debug("بازخوانی کانال بعد از عضویت ناموفق: %s", e)

        log.info("✅ عضو شدیم: «%s»", channel.title)
        return channel

    # ═════════════════════════════════════════════════════════
    # ترک کانال
    # ═════════════════════════════════════════════════════════
    async def leave(self, channel: ChannelRef) -> bool:
        """
        ترک کانال.

        :return: True اگر موفق بود
        """
        functions = _import()
        _errors = _import_errors()
        try:
            entity = await self.tg.raw.get_input_entity(channel.native_id)
            await self.tg.request(
                functions.channels.LeaveChannelRequest(channel=entity), rate_limit=False
            )
            channel.joined = False
            log.info("برای «%s» از کانال خارج شدیم", channel.title)
            return True
        except _errors.UserNotParticipantError:
            channel.joined = False
            log.debug("اصلاً عضو «%s» نبودیم", channel.title)
            return True
        except Exception as e:
            log.warning("ترک «%s» ناموفق: %s", channel.title, e)
            return False

    # ═════════════════════════════════════════════════════════
    # وضعیت
    # ═════════════════════════════════════════════════════════
    def stats(self) -> dict:
        """آمار گارد عضویت — برای نمایش در پنل"""
        return self.tg.join_guard.stats()


def _import_errors():
    from telethon import errors  # type: ignore

    return errors
