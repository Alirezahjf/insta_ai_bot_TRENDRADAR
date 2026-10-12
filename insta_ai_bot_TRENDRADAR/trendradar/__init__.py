"""
🎯 TrendRadar — موتور ترند‌یابی چند‌پیام‌رسانی
================================================

یک لایه‌ی مستقل و قابل‌توسعه برای کشف ترند روی پیام‌رسان‌ها.

فلسفه‌ی طراحی
-------------
1. **مستقل از پیام‌رسان** — منطق ترند‌یابی (`analyzer`) هیچ چیزی از تلگرام
   یا اینستاگرام نمی‌دونه. فقط با مدل‌های دامنه (`models`) کار می‌کنه.
2. **Provider-based** — هر پیام‌رسان یک «پرووایدر» است که پروتکل مشخصی رو
   پیاده می‌کنه. اضافه‌کردن پیام‌رسان جدید = اضافه‌کردن یک پوشه، نه تغییر کد.
3. **async در باطن، sync در ظاهر** — پیام‌رسان‌ها async هستند (Telethon)،
   ولی ربات موجود sync است. پل `runtime` این دو رو به هم وصل می‌کنه.
4. **ضد‌بن** — چون از اکانت کاربری (userbot) استفاده می‌شه، همه‌ی عملیات
   پرخطر (عضویت، جستجو) از یک گارد نرخ‌محدودکننده رد می‌شن.

استفاده‌ی سریع
--------------
    from trendradar import TrendRadar

    radar = TrendRadar()
    radar.register_default_providers()
    report = radar.scan(hashtags=["ترند", "مدلینگ"], limit_per_tag=50)
"""

from .models import (
    ChannelKind,
    ChannelRef,
    HashtagStat,
    MediaType,
    Post,
    ProviderHealth,
    RunReport,
    SearchQuery,
    TrendSignal,
)
from .registry import ProviderRegistry, register_provider
from .runtime import AsyncRuntime
from .trend_radar import TrendRadar

__version__ = "0.1.0"

__all__ = [
    "AsyncRuntime",
    "ChannelKind",
    "ChannelRef",
    "HashtagStat",
    "MediaType",
    "Post",
    "ProviderHealth",
    "ProviderRegistry",
    "RunReport",
    "SearchQuery",
    "TrendRadar",
    "TrendSignal",
    "register_provider",
    "__version__",
]
