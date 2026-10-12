"""
🇮🇷 پرووایدر تلگرام (MTProto / Telethon)

ساختار داخلی:
    client.py      ← مدیریت اتصال، session، احراز هویت، ترجمه‌ی خطا
    guard.py       ← گاردهای ضد‌بن (نرخ، سقف روزانه، فاصله‌ی عضویت)
    mapper.py      ← تبدیل اشیای Telethon به مدل‌های دامنه
    search.py      ← جستجوی هشتگ (۳ استراتژی)
    discovery.py   ← کشف کانال (۴ منبع)
    membership.py  ← عضویت/ترک با عبور از گارد
    provider.py    ← پیاده‌سازی قرارداد MessengerProvider
"""
from __future__ import annotations

from ...registry import register_provider
from .provider import TelegramProvider

# ثبت خودکار در رجیستری هنگام import
register_provider(TelegramProvider)

__all__ = ["TelegramProvider"]
