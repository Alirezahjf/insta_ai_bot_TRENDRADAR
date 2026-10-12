"""
🗂️ رجیستری پرووایدرها
====================

الگوی Registry + Plugin: هر پیام‌رسان خودش را ثبت می‌کند، موتور
آن‌ها را با نام پیدا می‌کند. اضافه‌کردن پیام‌رسان جدید نیازی به
تغییر کد موجود ندارد (Open/Closed Principle).

نحوه‌ی استفاده
--------------
    from trendradar.registry import register_provider, get_registry

    # ۱) ثبت خودکار با دکوراتور
    @register_provider
    class WhatsAppProvider(MessengerProvider): ...

    # ۲) یا ثبت دستی
    register_provider(WhatsAppProvider)

    # ۳) گرفتن نمونه
    provider = get_registry().create("telegram", settings)
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Dict, Iterable, List, Optional, Type, TypeVar

from .errors import ProviderNotAvailable
from .providers.base import MessengerProvider

log = logging.getLogger("trendradar.registry")

P = TypeVar("P", bound=Type[MessengerProvider])


class ProviderRegistry:
    """نگه‌دارنده‌ی کلاس‌های پرووایدر و نمونه‌های ساخته‌شده"""

    def __init__(self) -> None:
        self._classes: Dict[str, Type[MessengerProvider]] = {}
        self._instances: Dict[str, MessengerProvider] = {}
        self._lock = threading.RLock()

    # ── ثبت ──────────────────────────────────────────────
    def register(self, cls: Type[MessengerProvider], *, override: bool = False) -> Type[MessengerProvider]:
        """
        ثبت یک کلاس پرووایدر.

        :param override: اگر نام تکراری بود، جایگزین کن
        """
        name = getattr(cls, "name", None)
        if not name or name == "base":
            raise ValueError(f"پرووایدر {cls.__name__} باید فیلد name داشته باشد")

        with self._lock:
            if name in self._classes and not override:
                existing = self._classes[name]
                if existing is not cls:
                    log.warning("پرووایدر «%s» قبلاً با %s ثبت شده بود — رد شد", name, existing.__name__)
                return existing
            self._classes[name] = cls
            log.debug("پرووایدر ثبت شد: %s (%s)", name, cls.__name__)
        return cls

    def unregister(self, name: str) -> None:
        with self._lock:
            self._classes.pop(name, None)
            self._instances.pop(name, None)

    # ── خواندن ───────────────────────────────────────────
    def names(self) -> List[str]:
        with self._lock:
            return sorted(self._classes)

    def has(self, name: str) -> bool:
        with self._lock:
            return name in self._classes

    def get_class(self, name: str) -> Type[MessengerProvider]:
        with self._lock:
            cls = self._classes.get(name)
        if cls is None:
            raise ProviderNotAvailable(
                f"پرووایدر «{name}» ثبت نشده. موجود: {', '.join(self.names()) or '—'}"
            )
        return cls

    def create(self, name: str, settings=None, **kwargs) -> MessengerProvider:
        """
        ساخت نمونه‌ی پرووایدر. نمونه‌ها cache می‌شوند تا اتصال دوباره
        برقرار نشود (session تلگرام گران است).
        """
        with self._lock:
            inst = self._instances.get(name)
            if inst is not None:
                return inst
            cls = self.get_class(name)
            try:
                inst = cls(settings, **kwargs)
            except TypeError:
                inst = cls(settings)
            self._instances[name] = inst
        return inst

    def create_all(self, settings=None, *, only: Optional[Iterable[str]] = None) -> Dict[str, MessengerProvider]:
        """ساخت نمونه برای چند پرووایدر — فقط آن‌هایی که واقعاً قابل استفاده‌اند"""
        wanted = list(only) if only else self.names()
        out: Dict[str, MessengerProvider] = {}
        for name in wanted:
            if not self.has(name):
                log.warning("پرووایدر خواسته‌شده «%s» ثبت نشده — رد شد", name)
                continue
            try:
                out[name] = self.create(name, settings)
            except ProviderNotAvailable as e:
                log.info("پرووایدر «%s» در دسترس نیست: %s", name, e)
        return out

    def instances(self) -> Dict[str, MessengerProvider]:
        with self._lock:
            return dict(self._instances)

    def clear_instances(self) -> None:
        with self._lock:
            self._instances.clear()

    def describe(self) -> List[dict]:
        """مشخصات همه‌ی پرووایدرهای ثبت‌شده — برای نمایش در پنل"""
        out: List[dict] = []
        with self._lock:
            for name, cls in sorted(self._classes.items()):
                out.append({
                    "name": name,
                    "display_name": getattr(cls, "display_name", name),
                    "capabilities": sorted(c.value for c in getattr(cls, "capabilities", set())),
                })
        return out


# ── نمونه‌ی سراسری ───────────────────────────────────────
_registry = ProviderRegistry()


def get_registry() -> ProviderRegistry:
    """دسترسی به رجیستری سراسری"""
    return _registry


def register_provider(cls: P) -> P:
    """
    دکوراتور ثبت پرووایدر

    >>> @register_provider
    ... class MyProvider(MessengerProvider): ...
    """
    _registry.register(cls)
    return cls


def register_all(*classes: Type[MessengerProvider]) -> None:
    for c in classes:
        _registry.register(c)


def available_providers() -> List[str]:
    return _registry.names()
