"""
پرووایدرهای پیام‌رسان.

هر پیام‌رسان در زیرپوشه‌ی خودش قرار می‌گیرد:

    providers/
        base.py          ← قرارداد (این را پیاده کن)
        telegram/        ← اولین پرووایدر (MTProto / Telethon)
        instagram/       ← آینده
        bale/            ← آینده
        rubika/          ← آینده

ثبت خودکار
----------
`load_builtin_providers()` پرووایدرهای موجود را با **import تنبل** ثبت
می‌کند: اگر کتابخانه‌ی یک پیام‌رسان نصب نباشد، بقیه بدون خطا کار می‌کنند.
"""
from __future__ import annotations

import importlib
import logging
from typing import List, Tuple

from .base import Capability, MessengerProvider

log = logging.getLogger("trendradar.providers")

# (نام پرووایدر، مسیر ماژول، آیا اجباری است؟)
_BUILTIN: List[Tuple[str, str, bool]] = [
    ("telegram", "trendradar.providers.telegram", False),
    # پیام‌رسان‌های بعدی را این‌جا اضافه کن:
    # ("instagram", "trendradar.providers.instagram", False),
    # ("bale",      "trendradar.providers.bale",      False),
    # ("rubika",    "trendradar.providers.rubika",    False),
]

_loaded = False


def load_builtin_providers(force: bool = False) -> List[str]:
    """
    ثبت پرووایدرهای داخلی (idempotent).

    :return: نام پرووایدرهایی که با موفقیت ثبت شدند
    """
    global _loaded
    from ..registry import get_registry

    if _loaded and not force:
        return get_registry().names()

    reg = get_registry()
    for name, module_path, required in _BUILTIN:
        if reg.has(name):
            continue
        try:
            importlib.import_module(module_path)
            log.debug("پرووایدر «%s» بارگذاری شد", name)
        except ImportError as e:
            # کتابخانه‌ی پیام‌رسان نصب نیست → بقیه دست‌نخورده ادامه می‌دهند
            if required:
                raise
            log.info("پرووایدر «%s» بارگذاری نشد (کتابخانه نصب نیست؟): %s", name, e)
        except Exception as e:  # pragma: no cover
            log.warning("خطا در بارگذاری پرووایدر «%s»: %s", name, e)

    _loaded = True
    return reg.names()


__all__ = [
    "Capability",
    "MessengerProvider",
    "load_builtin_providers",
]
