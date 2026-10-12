"""
🌉 پل async ↔ sync

مشکل: ربات موجود با `pyTelegramBotAPI` **sync** است، ولی Telethon **async**.
راه‌حل: یک event loop اختصاصی در یک ترد دائمی اجرا می‌شود و کد sync
با `run_coro()` نتیجه را می‌گیرد.

چرا این روش و نه asyncio.run() ساده؟
  • asyncio.run() هر بار یک loop جدید می‌سازد → اتصال Telethon از دست می‌رود
  • لاگین تلگرام یک session زنده لازم دارد که بین فراخوانی‌ها باقی بماند
  • این کلاس همان event loop را نگه می‌دارد، پس session پایدار می‌ماند

نکته‌ی مهم: اگر «ما» درون خود event loop باشیم (مثلاً callback تلگرام که
async است)، نباید `run_coro` صدا زده شود چون deadlock می‌شود؛ برای این
حالت `run_async()` تعبیه شده که مستقیم await می‌کند.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import Future
from typing import Any, Awaitable, Callable, Coroutine, Optional, TypeVar

log = logging.getLogger("trendradar.runtime")

T = TypeVar("T")


class AsyncRuntime:
    """
    یک event loop دائمی در ترد جدا.

    >>> rt = AsyncRuntime()
    >>> rt.start()
    >>> result = rt.run( some_async_func() )      # از کد sync
    >>> rt.stop()
    """

    def __init__(self, name: str = "trendradar-loop", debug: bool = False):
        self._name = name
        self._debug = debug
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._started = threading.Event()
        self._stopping = False

    # ── چرخه‌ی عمر ──────────────────────────────────────────
    @property
    def loop(self) -> Optional[asyncio.AbstractEventLoop]:
        return self._loop

    @property
    def running(self) -> bool:
        return bool(self._loop and self._loop.is_running() and not self._stopping)

    @property
    def in_loop_thread(self) -> bool:
        """آیا کد فعلی داخل همین event loop اجرا می‌شود؟"""
        return self._loop is not None and threading.current_thread() is self._thread

    def start(self, timeout: float = 10.0) -> "AsyncRuntime":
        """راه‌اندازی loop در ترد پس‌زمینه (idempotent)"""
        if self.running:
            return self
        self._stopping = False
        self._started.clear()

        def _runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            self._started.set()
            try:
                loop.run_forever()
            except Exception:
                log.exception("event loop با خطا متوقف شد")
            finally:
                try:
                    # بستن تسک‌های باقی‌مانده تا هشدار نده
                    pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                    for t in pending:
                        t.cancel()
                    if pending:
                        loop.run_until_complete(
                            asyncio.gather(*pending, return_exceptions=True)
                        )
                    loop.run_until_complete(loop.shutdown_asyncgens())
                except Exception:
                    pass
                finally:
                    loop.close()
                    self._loop = None

        self._thread = threading.Thread(target=_runner, name=self._name, daemon=True)
        self._thread.start()
        if not self._started.wait(timeout):
            raise RuntimeError("event loop در زمان مقرر بالا نیامد")
        log.debug("AsyncRuntime راه‌اندازی شد (%s)", self._name)
        return self

    def stop(self, timeout: float = 5.0) -> None:
        """توقف تمیز loop"""
        self._stopping = True
        loop, thread = self._loop, self._thread
        if loop and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None
        log.debug("AsyncRuntime متوقف شد")

    def __enter__(self) -> "AsyncRuntime":
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.stop()

    # ── اجرای کوروتین ───────────────────────────────────────
    def run(self, coro: Coroutine[Any, Any, T], timeout: Optional[float] = None) -> T:
        """
        اجرای یک کوروتین از کد **sync** و برگرداندن نتیجه.

        :raises RuntimeError: اگر داخل خود loop صدا زده شود (deadlock)
        :raises TimeoutError: اگر از timeout بگذرد
        """
        if self.in_loop_thread:
            raise RuntimeError(
                "run() از داخل خود event loop صدا زده شد → deadlock می‌شود. "
                "از run_async() استفاده کن یا این تابع را در ترد دیگری اجرا کن."
            )
        if not self.running:
            self.start()

        assert self._loop is not None
        future: Future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout)
        except TimeoutError:
            future.cancel()
            raise

    async def run_async(self, coro: Coroutine[Any, Any, T]) -> T:
        """برای کدهایی که خودشان async هستند — بدون پل زدن"""
        return await coro

    def call_soon(self, fn: Callable[..., Any], *args: Any) -> None:
        """فراخوانی یک تابع sync داخل ترد loop"""
        if self.running:
            assert self._loop is not None
            self._loop.call_soon_threadsafe(fn, *args)

    # ── کمک‌کننده برای پاییپ‌لاین‌های async ──
    def gather(self, *aws: Awaitable[T]) -> list[T]:
        """اجرای موازی چند کوروتین از کد sync"""
        async def _all() -> list[T]:
            return list(await asyncio.gather(*aws))

        return self.run(_all())


# ─────────────────────────────────────────────────────────────
# یک نمونه‌ی مشترک برای کل برنامه (تک‌تا بودن loop = تک‌تا بودن session)
# ─────────────────────────────────────────────────────────────
_shared: Optional[AsyncRuntime] = None
_shared_lock = threading.Lock()


def shared_runtime() -> AsyncRuntime:
    """Runtime مشترک کل برنامه — برای اینکه session تلگرام تک‌نسخه بماند"""
    global _shared
    if _shared is None or not _shared.running:
        with _shared_lock:
            if _shared is None or not _shared.running:
                _shared = AsyncRuntime(name="trendradar-shared")
                _shared.start()
    return _shared
