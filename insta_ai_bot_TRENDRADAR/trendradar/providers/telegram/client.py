"""
📡 کلاینت MTProto تلگرام (بر پایه‌ی Telethon 1.45 / Layer 229)
==============================================================

این لایه مسئول موارد زیر است:

  • ساخت و مدیریت `TelegramClient` و فایل session
  • احراز هویت **تعاملی** (کد پیامکی + رمز دو مرحله‌ای) — چون در یک ربات
    تلگرامی نمی‌توان از ورودی کنسول استفاده کرد، مرحله‌به‌مرحله انجام می‌شود
  • ترجمه‌ی خطاهای بومی Telethon به خطاهای دامنه‌ی `trendradar.errors`
  • اعمال گاردهای نرخ روی هر درخواست
  • تلاش مجدد هوشمند (فقط برای خطاهای گذرا، نه برای FloodWait)

متدهای واقعی Telethon که استفاده می‌شوند (تأییدشده روی سورس 1.45.0):
    TelegramClient(session, api_id, api_hash, ..., flood_sleep_threshold=60)
    client.connect() / disconnect() / is_user_authorized() / get_me()
    client.send_code_request(phone) → SentCode
    client.sign_in(phone, code) / sign_in(password=...)
    client(request)                          ← اجرای هر TL request
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional, Type, TypeVar

from ...errors import (
    AuthRequired,
    ConfigError,
    PasswordRequired,
    ProviderError,
    RateLimited,
    SessionInvalid,
)
from .guard import JoinGuard, RateLimiter

log = logging.getLogger("trendradar.telegram.client")

T = TypeVar("T")


# ─────────────────────────────────────────────────────────────
# رزولوشن تنبل Telethon (تا نبودش کل سیستم را نخواباند)
# ─────────────────────────────────────────────────────────────
def _import_telethon():
    try:
        from telethon import TelegramClient  # type: ignore
        from telethon import errors, functions, types  # type: ignore
        from telethon.tl import types as tl_types  # type: ignore

        return TelegramClient, errors, functions, tl_types
    except ImportError as e:  # pragma: no cover
        from ...errors import ProviderNotAvailable

        raise ProviderNotAvailable(
            "کتابخانه‌ی Telethon نصب نیست. اجرا کن:\n    pip install telethon"
        ) from e


class TrendTelegramClient:
    """
    پوشش (wrapper) دور `TelegramClient` با مدیریت خطا و گاردهای ضد‌بن.

    >>> c = TrendTelegramClient(settings.telegram)
    >>> await c.connect()
    >>> await c.request(GetFullChannelRequest(channel=some_channel))
    """

    def __init__(self, settings):
        self.settings = settings
        self._client: Optional[Any] = None
        self._me: Optional[Any] = None
        self._connect_lock = asyncio.Lock()
        self._pending_phone: Optional[str] = None
        self._code_hash: Optional[str] = None

        # گاردها
        self.search_limiter = RateLimiter(
            settings.search_min_interval_sec, jitter=1.2, name="telegram-search"
        )
        self.join_limiter = RateLimiter(0.0, jitter=1.0, name="telegram-join")
        self.join_guard = JoinGuard(
            min_interval=settings.join_min_interval_sec,
            max_interval=settings.join_max_interval_sec,
            daily_cap=settings.join_daily_cap,
        )
        self.search_counter = None  # در connect ساخته می‌شود

    # ── دسترسی به کلاینت خام ─────────────────────────────
    @property
    def raw(self) -> Any:
        if self._client is None:
            raise ConfigError("کلاینت ساخته نشده — اول connect() صدا بزن")
        return self._client

    @property
    def connected(self) -> bool:
        return bool(self._client and self._client.is_connected())

    @property
    def me(self) -> Optional[Any]:
        return self._me

    # ── چرخه‌ی عمر ───────────────────────────────────────
    async def ensure_client(self) -> Any:
        """ساخت کلاینت (بدون اتصال) — idempotent"""
        if self._client is not None:
            return self._client

        s = self.settings
        if not s.configured:
            raise ConfigError(
                "TG_API_ID و TG_API_HASH تنظیم نشده‌اند.\n"
                "از my.telegram.org → API development tools بگیر و در .env بگذار."
            )

        TelegramClient, _errors, _functions, _types = _import_telethon()

        # پوشه‌ی session
        try:
            if s.session_dir and s.session_dir not in (".", ""):
                os.makedirs(s.session_dir, exist_ok=True)
        except OSError as e:
            log.warning("ساخت پوشه‌ی session ناموفق: %s", e)

        kwargs = dict(
            api_id=s.api_id,
            api_hash=s.api_hash,
            device_model=s.device_model,
            system_version=s.system_version,
            app_version=s.app_version,
            lang_code="fa",
            system_lang_code="fa-IR",
            timeout=s.timeout,
            request_retries=s.request_retries,
            connection_retries=s.connection_retries,
            retry_delay=s.retry_delay,
            auto_reconnect=True,
            flood_sleep_threshold=s.flood_sleep_threshold,
        )
        if s.proxy:
            try:
                import socks  # type: ignore  # noqa: F401

                kwargs["proxy"] = self._parse_proxy(s.proxy)
            except ImportError:
                log.warning("پروکسی تنظیم شده ولی PySocks نصب نیست — نادیده گرفته شد")

        self._client = TelegramClient(s.session_path, **kwargs)
        log.info("کلاینت Telethon ساخته شد | session=%s.session", s.session_path)
        return self._client

    @staticmethod
    def _parse_proxy(url: str) -> Any:
        """تبدیل 'socks5://host:port' به دیکشنری مورد انتظار Telethon"""
        import urllib.parse as up

        p = up.urlparse(url if "://" in url else f"socks5://{url}")
        scheme = (p.scheme or "socks5").lower()
        types_map = {"socks5": 2, "socks4": 1, "http": 3, "https": 3}
        d = {
            "proxy_type": types_map.get(scheme, 2),
            "addr": p.hostname or "",
            "port": p.port or 1080,
            "rdns": True,
        }
        if p.username:
            d["username"] = up.unquote(p.username)
        if p.password:
            d["password"] = up.unquote(p.password)
        return d

    async def connect(self, *, require_auth: bool = True) -> bool:
        """
        اتصال به تلگرام.

        :param require_auth: اگر True و اکانت لاگین نباشد، `AuthRequired` می‌دهد
        :return: True اگر لاگین بود
        """
        async with self._connect_lock:
            client = await self.ensure_client()

            if not client.is_connected():
                log.info("در حال اتصال به تلگرام...")
                await client.connect()

            # شمارنده‌ی جستجو
            if self.search_counter is None:
                from .guard import DailyCounter

                self.search_counter = DailyCounter(limit=self.settings.search_daily_cap)

            authorized = await client.is_user_authorized()
            if authorized:
                try:
                    self._me = await client.get_me()
                    log.info(
                        "✅ تلگرام متصل | اکانت: %s (@%s) | id=%s",
                        getattr(self._me, "first_name", "?"),
                        getattr(self._me, "username", "—"),
                        getattr(self._me, "id", "?"),
                    )
                except Exception as e:
                    log.warning("get_me ناموفق: %s", e)
            elif require_auth:
                raise AuthRequired(
                    "اکانت تلگرام لاگین نیست.\n"
                    "برای ورود: در ربات دکمه‌ی «ورود به تلگرام» را بزن "
                    "تا کد تایید برایت ارسال شود.",
                    interactive=True,
                    provider="telegram",
                )
            return authorized

    async def disconnect(self) -> None:
        if self._client and self._client.is_connected():
            try:
                await self._client.disconnect()
                log.info("اتصال تلگرام بسته شد")
            except Exception as e:
                log.debug("خطا در بستن اتصال: %s", e)

    # ── احراز هویت تعاملی ────────────────────────────────
    async def send_code(self, phone: Optional[str] = None) -> str:
        """
        درخواست کد تایید. مراحل بعدی: `sign_in_code()` و در صورت نیاز `sign_in_password()`.

        :return: توضیح وضعیت برای نمایش به کاربر
        """
        _TelegramClient, errors, _f, _t = _import_telethon()
        client = await self.ensure_client()
        if not client.is_connected():
            await client.connect()

        phone = (phone or self.settings.phone or "").strip()
        if not phone:
            raise ConfigError("شماره تلفن تنظیم نشده (TG_PHONE)")
        self._pending_phone = phone

        try:
            sent = await client.send_code_request(phone)
            self._code_hash = getattr(sent, "phone_code_hash", None)
        except errors.FloodWaitError as e:
            raise RateLimited(
                f"تلگرام درخواست کد را محدود کرده. {e.seconds} ثانیه صبر کن.",
                retry_after=e.seconds, provider="telegram",
            ) from e

        # نوع ارسال کد را گزارش بده (برای راهنمایی کاربر)
        type_name = type(sent.type).__name__ if getattr(sent, "type", None) else "?"
        kinds = {
            "SentCodeTypeApp": "داخل اپلیکیشن تلگرام خودت",
            "SentCodeTypeSms": "پیامک",
            "SentCodeTypeCall": "تماس تلفنی",
            "SentCodeTypeFlashCall": "تماس فلش",
            "SentCodeTypeMissedCall": "تماس ناموفق (دو رقم آخر)",
            "SentCodeTypeEmailCode": "ایمیل",
        }
        where = kinds.get(type_name, "تلگرام")
        log.info("کد تایید به %s ارسال شد (%s)", phone, where)
        return f"کد تایید از طریق «{where}» ارسال شد. کد را وارد کن."

    async def sign_in_code(self, code: str) -> str:
        """
        ورود با کد تایید.

        :return: پیام موفقیت
        :raises PasswordRequired: اگر رمز دو مرحله‌ای لازم باشد
        """
        _TelegramClient, errors, _f, _t = _import_telethon()
        client = await self.ensure_client()
        code = (code or "").strip().replace(" ", "").replace("-", "")
        if not code:
            raise AuthRequired("کد خالی است")

        phone = self._pending_phone or self.settings.phone
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=self._code_hash)
        except errors.SessionPasswordNeededError as e:
            raise PasswordRequired(
                "اکانت رمز دو مرحله‌ای دارد. رمز را وارد کن (TG_2FA_PASSWORD).",
                interactive=True, provider="telegram",
            ) from e
        except errors.PhoneCodeInvalidError as e:
            raise AuthRequired("کد وارد‌شده اشتباه است. دوباره امتحان کن.", provider="telegram") from e
        except errors.PhoneCodeExpiredError as e:
            raise AuthRequired("کد منقضی شد. «ارسال کد» را دوباره بزن.", provider="telegram") from e
        except errors.FloodWaitError as e:
            raise RateLimited(f"تلگرام محدود کرد. {e.seconds} ثانیه صبر کن.",
                              retry_after=e.seconds, provider="telegram") from e

        self._me = await client.get_me()
        log.info("✅ ورود موفق: @%s", getattr(self._me, "username", "—"))
        return f"ورود موفق ✅  ({getattr(self._me, 'first_name', '')})"

    async def sign_in_password(self, password: Optional[str] = None) -> str:
        """ورود با رمز دو مرحله‌ای"""
        _TelegramClient, errors, _f, _t = _import_telethon()
        client = await self.ensure_client()
        pw = (password or self.settings.password or "").strip()
        if not pw:
            raise ConfigError("رمز دو مرحله‌ای داده نشده")
        try:
            await client.sign_in(password=pw)
        except errors.PasswordHashInvalidError as e:
            raise AuthRequired("رمز دو مرحله‌ای اشتباه است.", provider="telegram") from e

        self._me = await client.get_me()
        return f"ورود کامل شد ✅  ({getattr(self._me, 'first_name', '')})"

    # ── اجرای درخواست ────────────────────────────────────
    async def request(
        self,
        req: Any,
        *,
        rate_limit: bool = True,
        retries: int = 2,
        retry_delay: float = 2.0,
    ) -> Any:
        """
        اجرای یک TL request با مدیریت کامل خطا.

        :param rate_limit: از گارد نرخ رد شود؟ (برای عملیات حساس مثل جستجو True)
        :param retries:    تعداد تلاش مجدد برای خطاهای گذرا
        """
        _TelegramClient, errors, _f, _t = _import_telethon()

        if rate_limit:
            self.search_limiter.wait()
            if self.search_counter is not None:
                self.search_counter.check("جستجو در تلگرام")

        client = await self.ensure_client()
        if not client.is_connected():
            await self.connect()

        attempt = 0
        while True:
            attempt += 1
            try:
                return await client(req)
            except errors.FloodWaitError as e:
                # سرور صریحاً گفته چند ثانیه صبر کن
                self.search_limiter.block_for(e.seconds)
                self.join_guard.record_flood(e.seconds)
                log.warning("⏳ FloodWait: %s ثانیه", e.seconds)
                raise RateLimited(
                    f"تلگرام درخواست‌ها را محدود کرد. {e.seconds} ثانیه صبر لازم است.",
                    retry_after=e.seconds, provider="telegram",
                ) from e
            except errors.AuthKeyUnregisteredError as e:
                raise SessionInvalid(
                    "سشن تلگرام باطل شده (احتمالاً از اپ دیگر خارج شده‌ای). باید دوباره لاگین کنی.",
                    interactive=True, provider="telegram",
                ) from e
            except errors.UserDeactivatedBanError as e:
                raise SessionInvalid(
                    "این اکانت تلگرام مسدود شده است!", interactive=False, provider="telegram"
                ) from e
            except (errors.RPCError,) as e:
                # خطاهای گذرا → تلاش مجدد
                transient = isinstance(e, (errors.TimeoutError,)) or "TIMEOUT" in str(e).upper()
                if transient and attempt <= retries:
                    log.debug("خطای گذرا (تلاش %d/%d): %s", attempt, retries, e)
                    await asyncio.sleep(retry_delay * attempt)
                    continue
                raise self._translate(e) from e
            except (ConnectionError, OSError) as e:
                if attempt <= retries:
                    log.debug("خطای شبکه (تلاش %d/%d): %s", attempt, retries, e)
                    await asyncio.sleep(retry_delay * attempt)
                    continue
                raise ProviderError(f"خطای شبکه: {e}", provider="telegram", original=e) from e

        # فقط برای تکمیل نوع خروجی
        raise AssertionError("unreachable")

    @staticmethod
    def _translate(e: Exception) -> Exception:
        """ترجمه‌ی خطاهای بومی Telethon به خطاهای دامنه"""
        _TelegramClient, errors, _f, _t = _import_telethon()
        from ...errors import (
            ChannelNotAccessible,
            JoinFailed,
            PermissionDenied,
            ProviderError,
        )

        mapping: list[tuple[tuple[Type[Exception], ...], Any]] = [
            ((errors.ChannelPrivateError,), ChannelNotAccessible("کانال خصوصی است یا دسترسی نداریم")),
            ((errors.ChannelInvalidError,), ChannelNotAccessible("شناسه‌ی کانال معتبر نیست")),
            ((errors.ChatAdminRequiredError,), PermissionDenied("این عملیات نیاز به ادمین بودن دارد")),
            ((errors.ChannelsTooMuchError,), JoinFailed("اکانت در تعداد کانال بیش از حد مجاز عضو است")),
            ((errors.UsernameNotOccupiedError, errors.UsernameInvalidError),
             ChannelNotAccessible("این یوزرنیم وجود ندارد")),
            ((errors.PeerIdInvalidError,), ChannelNotAccessible("شناسه‌ی چت نامعتبر است")),
        ]
        for exc_types, mapped in mapping:
            if isinstance(e, exc_types):
                return type(mapped)(f"{mapped} ({type(e).__name__})")

        return ProviderError(f"{type(e).__name__}: {e}", provider="telegram", original=e)

    # ── وضعیت ────────────────────────────────────────────
    def guards_state(self) -> dict:
        """وضعیت گاردها برای ذخیره‌سازی بین اجراها"""
        state: dict = {"join": self.join_guard.to_state()}
        if self.search_counter is not None:
            state["search"] = self.search_counter.to_state()
        return state

    def load_guards_state(self, state: dict) -> None:
        try:
            if state.get("join"):
                self.join_guard.from_state(state["join"])
            if state.get("search") and self.search_counter is not None:
                self.search_counter.from_state(state["search"])
        except Exception as e:
            log.debug("بازیابی وضعیت گاردها ناموفق: %s", e)
