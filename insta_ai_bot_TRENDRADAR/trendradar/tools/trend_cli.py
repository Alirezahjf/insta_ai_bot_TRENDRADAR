#!/usr/bin/env python3
"""
⌨️ ابزار خط فرمان TrendRadar
============================

اجرای مستقل موتور — بدون نیاز به ربات تلگرام. برای تست و اشکال‌زدایی.

نمونه‌ها
-------
    # وضعیت پرووایدرها و لاگین
    python -m trendradar.tools.trend_cli health

    # ورود تعاملی به تلگرام (کد پیامکی از خودت می‌پرسد)
    python -m trendradar.tools.trend_cli login

    # اسکن یک هشتگ
    python -m trendradar.tools.trend_cli scan ترند مدلینگ

    # فقط جستجویی سراسری، بدون کشف کانال
    python -m trendradar.tools.trend_cli scan ترند --no-discover

    # پیدا کردن کانال با یک عبارت
    python -m trendradar.tools.trend_cli find "مدلینگ"

    # لیست کانال‌های عضو
    python -m trendradar.tools.trend_cli joined

    # لیست پرووایدرهای ثبت‌شده (برای توسعه‌دهنده)
    python -m trendradar.tools.trend_cli providers
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import List, Optional

# اجازه‌ی اجرا از این مسیر
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from trendradar import TrendRadar  # noqa: E402
from trendradar.errors import TrendRadarError  # noqa: E402


def _hr(title: str = "") -> None:
    print("\n" + "═" * 72)
    if title:
        print(f"  {title}")
        print("═" * 72)


def _print_health(radar: TrendRadar) -> None:
    _hr("وضعیت پرووایدرها")
    health = radar.health()
    if not health:
        print("  ❌ هیچ پرووایدری فعال نیست. پرووایدرها را ثبت کن.")
        return
    for h in health:
        print(f"  {h.status_emoji} {h.name:12s} {h.name and ''}{h.display_name if hasattr(h,'display_name') else ''}")
        print(f"      در دسترس : {'بله' if h.available else 'خیر'}")
        print(f"      لاگین    : {'بله' if h.authorized else 'خیر'}")
        if h.account:
            print(f"      اکانت    : {h.account}")
        if h.error:
            print(f"      خطا      : {h.error}")
        if h.rate_limited_until:
            print(f"      محدود تا : {h.rate_limited_until}")
        print(f"      قابلیت‌ها: {', '.join(h.capabilities)}")


def _print_signals(signals, top: int = 10) -> None:
    _hr(f"🏆 ترندها (top {min(top, len(signals))})")
    if not signals:
        print("  چیزی پیدا نشد.")
        return
    for s in signals[:top]:
        bar = "█" * int(s.score / 5) + "░" * (20 - int(s.score / 5))
        print(f"\n  #{s.rank:2d}  #{s.tag}   {bar}  {s.score:.1f}/100")
        if s.reason:
            for line in s.reason.splitlines():
                print(f"        {line}")
        print(f"        تفکیک: " + " | ".join(f"{k}={v:.2f}" for k, v in s.breakdown.items()))
        for p in s.top_posts[:2]:
            print(f"        ↳ {p.channel_title or p.channel_uid}: {p.text[:60]}..." if p.text else "")


def cmd_health(radar: TrendRadar, args) -> int:
    _print_health(radar)
    _hr("گارد ضد‌بن")
    print(json.dumps(radar.guard_stats(), ensure_ascii=False, indent=2))
    _hr("آمار دیتابیس")
    print(json.dumps(radar.stats(), ensure_ascii=False, indent=2))
    return 0


def cmd_login(radar: TrendRadar, args) -> int:
    _hr("ورود به تلگرام")
    try:
        print(f"  {radar.auth_send_code(args.phone)}")
    except TrendRadarError as e:
        print(f"  ❌ {e}")
        return 1

    code = input("  کد تایید را وارد کن: ").strip()
    try:
        print(f"  {radar.auth_sign_in(code)}")
        return 0
    except TrendRadarError as e:
        print(f"  ⚠️ {e}")

    pw = input("  رمز دو مرحله‌ای را وارد کن: ").strip()
    try:
        print(f"  {radar.auth_sign_in_password(pw)}")
        return 0
    except TrendRadarError as e:
        print(f"  ❌ {e}")
        return 1


def cmd_scan(radar: TrendRadar, args) -> int:
    _hr(f"اسکن: {', '.join(args.terms)}")
    try:
        report = radar.scan(
            hashtags=args.terms,
            window_hours=args.hours,
            limit_per_tag=args.limit,
            auto_join=args.join,
            joined_only=args.joined_only,
            discover_channels=not args.no_discover,
            min_members=args.min_members,
        )
    except TrendRadarError as e:
        print(f"  ❌ {e}")
        return 1

    _hr("گزارش اجرا")
    print(f"  شناسه          : {report.run_id}")
    print(f"  مدت            : {report.duration_sec:.1f} ثانیه")
    print(f"  کانال‌های بررسی : {report.channels_scanned}")
    print(f"  کشف جدید       : {report.channels_discovered}")
    print(f"  عضویت جدید     : {report.channels_joined}")
    print(f"  پست‌ها          : {report.posts_collected}")
    if report.errors:
        print("  خطاها:")
        for e in report.errors[:8]:
            print(f"    ⚠️ {e}")

    _print_signals(report.signals, top=args.top)

    if args.json:
        out = f"trend_report_{report.run_id}.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
        print(f"\n  💾 گزارش کامل: {out}")

    # پیشنهاد برای اینستاگرام
    terms = radar.signals_to_instagram_terms(report.signals, top=5)
    if terms:
        _hr("🔄 آماده برای اسکن در اینستاگرام")
        print("  " + ", ".join(f"#{t}" for t in terms))
        print("  (این هشتگ‌ها را به بخش اسکرپ اینستاگرام بده)")
    return 0


def cmd_find(radar: TrendRadar, args) -> int:
    _hr(f"کشف کانال: {args.term}")
    try:
        channels = radar.discover_channels(args.term, limit=args.limit, min_members=args.min_members)
    except TrendRadarError as e:
        print(f"  ❌ {e}")
        return 1

    if not channels:
        print("  چیزی پیدا نشد.")
        return 0
    for i, c in enumerate(channels, 1):
        members = f"{c.members:,}" if c.members else "؟"
        flags = " ".join(f"[{f}]" for f in c.risk_flags)
        print(f"  {i:2d}. {c.title}  (@{c.username or '—'})  {members} عضو  {c.kind.value} {flags}")
        if c.about:
            print(f"      {c.about[:90]}")
    return 0


def cmd_joined(radar: TrendRadar, args) -> int:
    _hr("کانال‌های عضو")
    channels = radar.joined_channels(limit=args.limit)
    if not channels:
        print("  هیچ کانالی ثبت نشده.")
        return 0
    print(f"  مجموع: {len(channels)}")
    for c in channels[: args.limit]:
        members = f"{c.members:,}" if c.members else "؟"
        print(f"  • {c.title}  (@{c.username or '—'})  {members} عضو")
    return 0


def cmd_resolve(radar: TrendRadar, args) -> int:
    _hr(f"رزولوشن: {args.identifier}")
    ch = radar.resolve_channel(args.identifier)
    if ch is None:
        print("  ❌ پیدا نشد.")
        return 1
    print(json.dumps(ch.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_providers(radar: TrendRadar, args) -> int:
    from trendradar.registry import get_registry

    _hr("پرووایدرهای ثبت‌شده در رجیستری")
    for d in get_registry().describe():
        print(f"  • {d['name']:12s} ({d['display_name']})")
        print(f"      قابلیت‌ها: {', '.join(d['capabilities']) or '—'}")
    _hr("پرووایدرهای فعال")
    print("  " + ", ".join(radar.providers()) or "  —")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="trend_cli",
        description="🎯 TrendRadar — موتور ترند‌یابی چند‌پیام‌رسانی",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="لاگ کامل")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health", help="وضعیت پرووایدرها و گاردها").set_defaults(func=cmd_health)

    p = sub.add_parser("login", help="ورود تعاملی به تلگرام")
    p.add_argument("--phone", help="شماره با کد کشور، مثل +98912...")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("scan", help="اسکن ترند")
    p.add_argument("terms", nargs="+", help="هشتگ یا کلمه‌ها (بدون #)")
    p.add_argument("--hours", type=int, default=72, help="پنجره‌ی زمانی (پیش‌فرض ۷۲)")
    p.add_argument("--limit", type=int, default=50, help="حداکثر پست برای هر هشتگ")
    p.add_argument("--top", type=int, default=10, help="چند ترند نمایش داده شود")
    p.add_argument("--join", action="store_true", help="عضویت خودکار در کانال‌های جدید")
    p.add_argument("--joined-only", action="store_true", help="فقط در کانال‌های عضو")
    p.add_argument("--no-discover", action="store_true", help="کشف کانال جدید نکن")
    p.add_argument("--min-members", type=int, default=0, help="حداقل اعضای کانال")
    p.add_argument("--json", action="store_true", help="ذخیره‌ی گزارش کامل JSON")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("find", help="پیدا کردن کانال")
    p.add_argument("term")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--min-members", type=int, default=0)
    p.set_defaults(func=cmd_find)

    p = sub.add_parser("joined", help="کانال‌های عضو")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_joined)

    p = sub.add_parser("resolve", help="تبدیل لینک/یوزرنیم به کانال")
    p.add_argument("identifier")
    p.set_defaults(func=cmd_resolve)

    sub.add_parser("providers", help="لیست پرووایدرها").set_defaults(func=cmd_providers)

    args = parser.parse_args(argv)

    if args.verbose:
        logging.getLogger("trendradar").setLevel(logging.DEBUG)
    else:
        logging.getLogger("trendradar").setLevel(logging.INFO)

    radar = TrendRadar()
    radar.register_default_providers()

    try:
        return args.func(radar, args)
    except KeyboardInterrupt:
        print("\n  ⛔ متوقف شد.")
        return 130
    finally:
        radar.shutdown()


if __name__ == "__main__":
    sys.exit(main())
