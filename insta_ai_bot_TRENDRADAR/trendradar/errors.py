"""
خطاهای دامنه — مستقل از کتابخانه‌ی پیام‌رسان.

هر پرووایدر باید خطاهای بومی کتابخانه‌اش (مثل FloodWaitError تلگرام) را
به این خطاها ترجمه کند. با این کار، منطق بالادستی (`pipeline`) نیازی ندارد
بداند کدام پیام‌رسان است.
"""
from __future__ import annotations

from typing import Optional


class TrendRadarError(Exception):
    """ریشه‌ی همه‌ی خطاهای TrendRadar"""


# ─────────────────────────────────────────────────────────────
# پیکربندی و احراز هویت
# ─────────────────────────────────────────────────────────────
class ConfigError(TrendRadarError):
    """تنظیمات ناقص یا غلط (مثلاً api_id خالی)"""


class ProviderNotAvailable(TrendRadarError):
    """کتابخانه‌ی پرووایدر نصب نیست یا پلتفرم پشتیبانی نمی‌شود"""


class AuthRequired(TrendRadarError):
    """
    نیاز به لاگین. پیام شامل راهنمای اقدام است.
    `interactive` یعنی می‌شود با کد/رمز ادامه داد.
    """

    def __init__(self, message: str, *, interactive: bool = True, provider: str = ""):
        super().__init__(message)
        self.interactive = interactive
        self.provider = provider


class SessionInvalid(AuthRequired):
    """سشن منقضی یا باطل شده (AuthKeyUnregistered) — باید دوباره لاگین شود"""


class PasswordRequired(AuthRequired):
    """حساب رمز دو مرحله‌ای دارد و رمز داده نشده"""


# ─────────────────────────────────────────────────────────────
# محدودیت نرخ (ضد‌بن)
# ─────────────────────────────────────────────────────────────
class RateLimited(TrendRadarError):
    """
    پیام‌رسان ما را محدود کرده (معادل FloodWaitError تلگرام).

    `retry_after` ثانیه‌ای است که باید صبر کنیم.
    """

    def __init__(self, message: str, retry_after: float = 0, provider: str = ""):
        super().__init__(message)
        self.retry_after = float(retry_after or 0)
        self.provider = provider


class DailyCapReached(TrendRadarError):
    """سقف روزانه‌ی عملیات (مثلاً عضویت) پر شده — محافظت از اکانت"""

    def __init__(self, message: str, *, action: str = "", cap: int = 0):
        super().__init__(message)
        self.action = action
        self.cap = cap


class GuardRejected(TrendRadarError):
    """گارد امنیتی عملیات را رد کرد (چنل پرخطر، فاصله‌ی کم، ...)"""


# ─────────────────────────────────────────────────────────────
# خطاهای عملیاتی
# ─────────────────────────────────────────────────────────────
class ChannelNotAccessible(TrendRadarError):
    """کانال خصوصی است، ادمین لازم دارد، یا وجود ندارد"""


class ChannelNotFound(ChannelNotAccessible):
    """یوزرنیم/شناسه پیدا نشد"""


class JoinFailed(TrendRadarError):
    """عضویت ناموفق بود (مثلاً نیاز به تایید ادمین)"""


class PermissionDenied(TrendRadarError):
    """اکانت اجازه‌ی این کار را ندارد (مثلاً ارسال در چنل، دیدن لیست اعضا)"""


class ProviderError(TrendRadarError):
    """خطای عمومی پرووایدر — خطای بومی کتابخانه داخلش نگه داشته می‌شود"""

    def __init__(self, message: str, *, provider: str = "", original: Optional[Exception] = None):
        super().__init__(message)
        self.provider = provider
        self.original = original
