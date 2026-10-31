# -*- coding: utf-8 -*-
"""
================================================================================
 Pinterest Terminal Tool  —  نسخه استخراج کامل (Full Metadata Extractor)
================================================================================

برای هر تصویر این اطلاعات استخراج می‌شود:

  ✅ لینک تصویر (original)          ✅ تعداد واکنش / لایک   (reaction_counts)
  ✅ شناسه و لینک پین               ✅ تعداد کامنت           (comment_count)
  ✅ عنوان / توضیح / alt text       ✅ تعداد ذخیره (Repin)   (repin_count)
  ✅ نام کاربر صاحب پین             ✅ تعداد اشتراک‌گذاری    (share_count)
  ✅ نام و لینک بُرد (Board)         ✅ کامنت‌ها فعال/غیرفعال (comments_disabled)
  ✅ تاریخ ایجاد                    ✅ ابعاد تصویر و رنگ غالب
  ✅ لینک مبدأ (سایت اصلی)          ✅ تگ‌ها / کلمات کلیدی پین
  ✅ ویدیویی بودن یا نبودن

  ❌ تعداد بازدید  → پینترست این عدد را به‌صورت عمومی منتشر نمی‌کند
     (فقط صاحب پین در Pinterest Analytics می‌بیند: Impressions)
  ⚠️ متن کامنت‌ها  → فقط با نشستِ لاگین‌شده (کوکی + توکن CSRF) قابل دسترسی است

نصب:   pip install -U pinscrape
اجرا:  python pinterest_full.py
"""

import csv
import json
import sys
import math
import re
import time
import threading
import argparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from urllib.parse import quote, quote_plus

try:
    from pinscrape.v2 import Pinterest
except ImportError:
    print("[!] کتابخانه pinscrape نصب نیست:  pip install -U pinscrape")
    raise ImportError("Install pinscrape to enable Pinterest search")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ==============================================================================
#  هسته استخراج
# ==============================================================================
# ساخت چند کلاینت به‌صورت همزمان فایل data/time_epoch.json را خراب می‌کند،
# پس ساخت آن‌ها پشت یک قفل سراسری انجام می‌شود (برای استفاده در ربات/نخ‌های موازی).
_CLIENT_LOCK = threading.Lock()


def new_client(sleep_time: float = 0.3) -> "PinterestExtractor":
    with _CLIENT_LOCK:
        return PinterestExtractor(sleep_time=sleep_time)


class PinterestExtractor(Pinterest):
    """نسخه توسعه‌یافته‌ی Pinterest که متادیتای کامل هر پین را برمی‌گرداند."""

    # ---------- جستجو: برگرداندن نتایج خام ----------
    def raw_search(self, query: str, page_size: int = 10) -> list:
        source_url = f"/search/pins/?q={quote(query)}&rs=typed"
        try:
            self.session.get(f"{self.BASE_URL}{source_url}",
                             headers=self.BASE_HEADERS, timeout=(5, 30))
        except Exception:
            pass

        payload = {
            "options": {
                "applied_unified_filters": None, "appliedProductFilters": "---",
                "article": None, "auto_correction_disabled": False, "corpus": None,
                "customized_rerank_type": None, "domains": None, "filters": None,
                "journey_depth": None, "page_size": f"{page_size}",
                "price_max": None, "price_min": None, "query_pin_sigs": None,
                "query": quote(query), "redux_normalize_feed": True,
                "request_params": None, "rs": "typed", "scope": "pins",
                "selected_one_bar_modules": None, "source_id": None,
                "source_module_id": None, "seoDrawerEnabled": False,
                "source_url": quote_plus(source_url),
                "top_pin_id": None, "top_pin_ids": None,
            },
            "context": {},
        }
        encoded = quote_plus(json.dumps(payload).replace(" ", ""))
        for a, b in (("%2520", "%20"), ("%252F", "%2F"), ("%253F", "%3F"),
                     ("%252520", "%2520"), ("%253D", "%3D"), ("%2526", "%26")):
            encoded = encoded.replace(a, b)

        url = (f"{self.BASE_URL}/resource/BaseSearchResource/get/"
               f"?source_url={quote_plus(source_url)}&data={encoded}&_={self.time_epoch}")
        headers = self.BASE_HEADERS.copy()
        headers["X-Pinterest-Source-Url"] = source_url

        try:
            r = self.session.get(url, headers=headers, proxies=self.proxies, timeout=(5, 30))
            r.raise_for_status()
            return r.json().get("resource_response", {}).get("data", {}).get("results", [])
        except Exception as exc:
            raise RuntimeError(f"Pinterest search failed: {exc}") from exc

    # ---------- جزئیات کامل یک پین ----------
    def pin_details(self, pin_id, field_set: str = "detailed") -> dict:
        source_url = f"/pin/{pin_id}/"
        payload = {"options": {"id": str(pin_id), "field_set_key": field_set}, "context": {}}
        encoded = quote_plus(json.dumps(payload).replace(" ", ""))
        url = (f"{self.BASE_URL}/resource/PinResource/get/"
               f"?source_url={quote_plus(source_url)}&data={encoded}&_={self.time_epoch}")
        headers = self.BASE_HEADERS.copy()
        headers["X-Pinterest-Source-Url"] = source_url
        headers["X-Pinterest-Pws-Handler"] = "www/pin/[id].js"
        try:
            r = self.session.get(url, headers=headers, proxies=self.proxies, timeout=(5, 30))
            if r.status_code != 200:
                return {}
            return r.json().get("resource_response", {}).get("data", {}) or {}
        except Exception:
            return {}

    def raw_search_urls(self, query: str, page_size: int = 10) -> list:
        """فقط لینک تصاویر (سازگار با نسخه قبلی)"""
        return [str(r["images"]["orig"]["url"]) for r in self.raw_search(query, page_size)
                if r.get("images", {}).get("orig")]


# ==============================================================================
#  تبدیل داده خام به گزارش تمیز
# ==============================================================================
def _pick_image(pin: dict) -> dict:
    """انتخاب بهترین کیفیت تصویر"""
    images = pin.get("images") or {}
    for key in ("orig", "1200x", "736x", "474x"):
        if key in images and images[key].get("url"):
            img = images[key]
            return {"url": str(img["url"]),
                    "width": img.get("width"),
                    "height": img.get("height")}
    if images:
        first = list(images.values())[0]
        return {"url": str(first.get("url", "")),
                "width": first.get("width"), "height": first.get("height")}
    return {"url": "", "width": None, "height": None}


def _reactions(reaction_counts) -> int:
    """reaction_counts مثل {'1': 565} است → مجموع همه نوع واکنش"""
    if not isinstance(reaction_counts, dict):
        return 0
    total = 0
    for v in reaction_counts.values():
        try:
            total += int(v)
        except (TypeError, ValueError):
            pass
    return total


def _s(v) -> str:
    """تبدیل امن به رشته — پینترست گاهی description را به‌صورت dict می‌فرستد"""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        for k in ("text", "value", "title", "name", "plain"):
            if isinstance(v.get(k), str):
                return v[k].strip()
        try:
            return " ".join(str(x) for x in v.values() if isinstance(x, str)).strip()
        except Exception:
            return ""
    try:
        return str(v).strip()
    except Exception:
        return ""


def _age_days(created_at: str) -> float:
    """سن پین به روز"""
    if not created_at:
        return 365.0
    try:
        dt = datetime.strptime(str(created_at).split(" +")[0], "%a, %d %b %Y %H:%M:%S")
        return max(1.0, (datetime.utcnow() - dt).total_seconds() / 86400.0)
    except Exception:
        return 365.0


def normalize(pin: dict) -> dict:
    img = _pick_image(pin)
    pinner = pin.get("pinner") or {}
    board = pin.get("board") or {}
    pin_id = pin.get("id")

    tags = []
    pin_join = pin.get("pin_join") or {}
    for t in (pin_join.get("visual_annotation") or []):
        if isinstance(t, str):
            tags.append(t)
    ann = pin_join.get("annotations_with_links") or {}
    for t in (ann.keys() if isinstance(ann, dict) else ann):
        if isinstance(t, str):
            tags.append(t)

    title = _s(pin.get("title")) or _s(pin.get("grid_title"))
    desc = _s(pin.get("description")) or _s(pin.get("closeup_unified_description"))
    alt = _s(pin.get("auto_alt_text")) or _s(pin.get("seo_alt_text"))
    if not title:
        title = desc or alt or "بدون عنوان"

    return {
        "pin_id": str(pin_id) if pin_id else "",
        "pin_url": f"https://www.pinterest.com/pin/{pin_id}/" if pin_id and str(pin_id).isdigit() else "",
        "image_url": img["url"],
        "image_width": img["width"],
        "image_height": img["height"],
        "title": title,
        "description": desc,
        "alt_text": alt,
        "likes": _reactions(pin.get("reaction_counts")),
        "comment_count": pin.get("comment_count") or 0,
        "repin_count": pin.get("repin_count") or 0,
        "share_count": pin.get("share_count") or 0,
        "comments_disabled": pin.get("comments_disabled"),
        "views": None,                       # پینترست بازدید عمومی ندارد
        "owner_username": pinner.get("username") or "",
        "owner_full_name": pinner.get("full_name") or "",
        "owner_followers": pinner.get("follower_count") or 0,
        "board_name": _s(board.get("name")),
        "board_url": ("https://www.pinterest.com" + board["url"]) if board.get("url") else "",
        "source_link": _s(pin.get("link")),
        "source_domain": _s(pin.get("domain")),
        "is_video": bool(pin.get("is_video")),
        "dominant_color": _s(pin.get("dominant_color")),
        "created_at": _s(pin.get("created_at")),
        "tags": list(dict.fromkeys(tags)),
    }


# ==============================================================================
#  دانلود فایل تصاویر
# ==============================================================================
def download_images(results: list, folder: str = "downloads", workers: int = 4) -> list:
    """
    تصاویر را روی دیسک دانلود می‌کند.
    خروجی: لیستی هم‌اندازهٔ results که هر خانه مسیر فایل یا None است.

    از همان محافظ‌های امنیتیِ pinscrape استفاده می‌کند:
      • فقط هاست‌های معتبر Pinterest (ضد SSRF)
      • فقط مسیرهای داخل پوشهٔ هدف (ضد Path Traversal)
      • سقف حجم فایل (ضد بمب فشرده‌سازی)
    """
    import requests
    from pathlib import Path
    from pinscrape.utils import (
        is_allowed_image_url, safe_image_path, MAX_IMAGE_BYTES, REQUEST_TIMEOUT,
    )

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    paths = [None] * len(results)

    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36 Edg/139.0.0.0")

    def work(item):
        idx, r = item
        url = r.get("image_url")
        if not url or not is_allowed_image_url(url):
            return idx, None
        safe = safe_image_path(url, folder)
        if safe is None:
            return idx, None
        # پیشوندِ شماره برای حفظ ترتیب و جلوگیری از تداخل نام
        target = folder / f"{idx + 1:02d}_{safe.name}"
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": UA, "Referer": "https://www.pinterest.com/",
                         "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"},
                timeout=REQUEST_TIMEOUT, stream=True,
            )
            if resp.status_code != 200:
                return idx, None
            data = resp.raw.read(MAX_IMAGE_BYTES + 1, decode_content=True)
            if len(data) > MAX_IMAGE_BYTES:
                return idx, None
            if not data:
                return idx, None
            target.write_bytes(data)
            return idx, str(target)
        except Exception:
            return idx, None

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for idx, path in ex.map(work, list(enumerate(results))):
            paths[idx] = path

    return paths


def extract_pin_by_id(pin_id: str) -> dict:
    """استخراج کاملِ یک پینِ مشخص (برای وقتی که کاربر لینک پین را می‌فرستد)"""
    d = new_client(sleep_time=0.3).pin_details(pin_id)
    if not d:
        return {}
    return normalize(d)


# ==============================================================================
#  استخراج دسته‌ای (چند نخی)
# ==============================================================================
def extract(query: str, count: int = 5, workers: int = 5, detailed: bool = True) -> list:
    """جستجو + (اختیاری) دریافت آمار کامل برای هر پین"""
    client = new_client(sleep_time=1)
    print(f"\n[*] در حال جستجوی '{query}' ...")
    raw = client.raw_search(query, page_size=max(count, 10))
    raw = [r for r in raw if (r.get("images") or {}).get("orig")]
    if not raw:
        print("[x] هیچ نتیجه‌ای یافت نشد.")
        return []

    seen, pins = set(), []
    for r in raw:
        if r.get("id") in seen:
            continue
        seen.add(r.get("id"))
        pins.append(r)
        if len(pins) >= count:
            break

    print(f"[*] {len(pins)} پین یافت شد" + (" — در حال دریافت آمار هر پین ..." if detailed else ""))

    if not detailed:
        return [normalize(p) for p in pins]

    # ساخت کلاینت‌ها در نخ اصلی (ساخت همزمان آن‌ها فایل time_epoch.json را خراب می‌کند)
    workers = max(1, min(workers, len(pins)))
    clients = [new_client() for _ in range(workers)]
    chunks = [pins[i::workers] for i in range(workers)]

    def enrich_chunk(args_):
        chunk, client = args_
        out = []
        for p in chunk:
            d = client.pin_details(p.get("id"))
            merged = {**p, **{k: v for k, v in d.items() if v not in (None, "", [], {})}}
            out.append(normalize(merged))
        return out

    with ThreadPoolExecutor(max_workers=workers) as ex:
        parts = list(ex.map(enrich_chunk, zip(chunks, clients)))

    return [item for part in parts for item in part]


# ==============================================================================
#  نمایش
# ==============================================================================
def _fmt(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "—"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def show(results: list, query: str):
    if not results:
        return
    print("\n" + "=" * 78)
    print(f"  گزارش کامل برای «{query}» — {len(results)} تصویر")
    print("=" * 78)

    for i, r in enumerate(results, 1):
        print(f"\n┌─ [{i}] {r['title'][:70]}")
        print(f"│  🖼  لینک تصویر    : {r['image_url']}")
        print(f"│  🔗 لینک پین      : {r['pin_url']}")
        size = f"{r['image_width']}×{r['image_height']}" if r['image_width'] else "—"
        print(f"│  📐 ابعاد / رنگ   : {size}   |   {r['dominant_color'] or '—'}"
              f"   |   {'ویدیو' if r['is_video'] else 'تصویر'}")
        print(f"│  ── آمار تعامل " + "─" * 44)
        print(f"│  ❤️  لایک / واکنش  : {_fmt(r['likes'])}  ({r['likes']})")
        print(f"│  💬 تعداد کامنت   : {_fmt(r['comment_count'])}  ({r['comment_count']})"
              + ("   [کامنت‌ها غیرفعال]" if r['comments_disabled'] else ""))
        print(f"│  📌 ذخیره (Repin) : {_fmt(r['repin_count'])}  ({r['repin_count']})")
        print(f"│  🔄 اشتراک‌گذاری  : {_fmt(r['share_count'])}  ({r['share_count']})")
        print(f"│  👁  تعداد بازدید : در دسترس نیست (پینترست آن را عمومی نمی‌کند)")
        print(f"│  ── مالک / بُرد " + "─" * 45)
        owner = r['owner_full_name'] or r['owner_username'] or "—"
        print(f"│  👤 صاحب پین      : {owner}  (@{r['owner_username'] or '—'})"
              f"  — دنبال‌کننده: {_fmt(r['owner_followers'])}")
        print(f"│  📋 بُرد          : {r['board_name'] or '—'}")
        if r['board_url']:
            print(f"│                    {r['board_url']}")
        print(f"│  🌐 مبدأ          : {r['source_domain'] or '—'}"
              + (f"  →  {r['source_link']}" if r['source_link'] else ""))
        print(f"│  📅 تاریخ ایجاد   : {r['created_at'] or '—'}")
        if r['description'] and r['description'] != r['title']:
            print(f"│  📝 توضیح         : {r['description'][:120]}")
        if r['alt_text']:
            print(f"│  🏷  alt text     : {r['alt_text'][:120]}")
        if r['tags']:
            print(f"│  🔖 تگ‌ها         : {', '.join(r['tags'][:8])}")
        print("└" + "─" * 77)

    # خلاصه
    tot = lambda k: sum(int(r[k] or 0) for r in results)  # noqa: E731
    print("\n" + "=" * 78)
    print("  خلاصه:")
    print(f"    مجموع لایک‌ها        : {tot('likes'):,}")
    print(f"    مجموع کامنت‌ها       : {tot('comment_count'):,}")
    print(f"    مجموع ذخیره‌ها       : {tot('repin_count'):,}")
    print(f"    مجموع اشتراک‌گذاری‌ها : {tot('share_count'):,}")
    top = max(results, key=lambda r: r['likes'])
    print(f"    محبوب‌ترین (لایک)    : {top['title'][:45]}  →  {_fmt(top['likes'])} ❤️")
    print("=" * 78)


# ==============================================================================
#  خروجی فایل
# ==============================================================================
def save(results: list, query: str, basepath: str = "pinterest_report"):
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in query)[:40]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    jpath = f"{basepath}_{safe}_{stamp}.json"
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump({"query": query, "extracted_at": datetime.now().isoformat(),
                   "count": len(results), "results": results},
                  f, ensure_ascii=False, indent=2)

    cpath = f"{basepath}_{safe}_{stamp}.csv"
    headers = ["pin_id", "title", "image_url", "pin_url", "likes", "comment_count",
               "repin_count", "share_count", "views", "owner_username", "owner_full_name",
               "owner_followers", "board_name", "board_url", "source_domain", "source_link",
               "image_width", "image_height", "is_video", "dominant_color", "created_at",
               "description", "alt_text", "tags", "comments_disabled"]
    with open(cpath, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        w.writeheader()
        for r in results:
            row = dict(r)
            row["tags"] = ", ".join(r["tags"])
            row["views"] = ""
            w.writerow(row)

    print(f"\n[✓] ذخیره شد:\n    JSON → {jpath}\n    CSV  → {cpath}")
    return jpath, cpath


# ==============================================================================
#  رابط ترمینال
# ==============================================================================
def main():
    ap = argparse.ArgumentParser(description="Pinterest Full Metadata Extractor")
    ap.add_argument("query", nargs="?", help="کلمه کلیدی")
    ap.add_argument("-n", "--count", type=int, default=None, help="تعداد تصویر (پیش‌فرض: پرسیده می‌شود / 5)")
    ap.add_argument("-w", "--workers", type=int, default=5, help="تعداد نخ (پیش‌فرض 5)")
    ap.add_argument("--urls-only", action="store_true", help="فقط لینک تصویر، بدون آمار")
    ap.add_argument("--no-save", action="store_true", help="عدم ذخیره فایل خروجی")
    args, _ = ap.parse_known_args()

    query = args.query or input("\n[?] کلمه کلیدی برای جستجو: ").strip()
    if not query:
        print("[!] کلمه کلیدی نمی‌تواند خالی باشد.")
        return

    cnt = args.count
    if cnt is None:
        try:
            cnt = int(input("[?] تعداد تصویر (پیش‌فرض 5): ").strip() or 5)
        except (ValueError, EOFError):
            cnt = 5
    cnt = max(1, cnt)

    results = extract(query, cnt, workers=args.workers, detailed=not args.urls_only)

    if not results:
        return

    if args.urls_only:
        print(f"\n--- {len(results)} لینک ---")
        for r in results:
            print(r["image_url"])
    else:
        show(results, query)

    if not args.no_save:
        ans = input("\n[?] ذخیره در فایل JSON/CSV؟ (y/N): ").strip().lower()
        if ans in ("y", "yes", "بله"):
            save(results, query)


if __name__ == "__main__":
    main()


# ==============================================================================
#  سیستم امتیازدهی و رتبه‌بندی
# ==============================================================================
# چرا این فرمول؟
#   پینترست «تعداد بازدید» را عمومی نمی‌کند، پس باید کیفیت را از روی
#   رفتارِ واقعیِ کاربران سنجید. هر عمل «هزینه»ی متفاوتی دارد:
#
#     لایک     → ارزان‌ترین واکنش              → وزن ۱
#     ذخیره    ← کاربر می‌خواهد بعداً برگردد    ← وزن ۲
#     کامنت    ← نیاز به وقت و درگیری دارد      ← وزن ۳
#     اشتراک   ← آبروی کاربر در میان است        ← وزن ۴
#
#   بعد دو بُعد دیگر هم وارد می‌شود:
#     • سرعت: تعامل به‌ازای هر روز عمرِ پین (پین تازه با تعاملِ برابر، بهتر است)
#     • نرخ ذخیره: ذخیره تقسیم بر لایک (هرچه بالاتر، محتوا «کاربردی‌تر» است
#       تا صرفاً «جذاب»)
#
#   و در نهایت دو نمره ترکیب می‌شوند:
#     • نمرهٔ مطلق  → قابل مقایسه با هر پین دیگر در پینترست
#     • نمرهٔ نسبی  → رتبه در همین مجموعهٔ نتایج
# ==============================================================================

WEIGHTS = {"like": 1.0, "repin": 2.0, "comment": 3.0, "share": 4.0}
GRADE_TABLE = [(85, "S"), (70, "A"), (55, "B"), (35, "C"), (0, "D")]


def engagement(r: dict) -> float:
    """تعاملِ وزن‌دار"""
    return (WEIGHTS["like"] * int(r.get("likes") or 0)
            + WEIGHTS["comment"] * int(r.get("comment_count") or 0)
            + WEIGHTS["repin"] * int(r.get("repin_count") or 0)
            + WEIGHTS["share"] * int(r.get("share_count") or 0))


def _norm(values: list) -> list:
    """نرمال‌سازی Min-Max به بازهٔ ۰..۱؛ اگر همه برابر بودند → ۰.۵"""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if abs(hi - lo) < 1e-9:
        return [0.5] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def score_pins(results: list) -> list:
    """
    به هر پین امتیاز ۰..۱۰۰، رتبه و درجه می‌دهد و فهرست را
    بر اساس امتیاز مرتب می‌کند (بهترین اول).
    """
    if not results:
        return results

    recs = []
    for r in results:
        e = engagement(r)
        age = _age_days(r.get("created_at"))
        vel = e / age                                   # سرعت: تعامل در روز
        sr = int(r.get("repin_count") or 0) / (int(r.get("likes") or 0) + 1)
        recs.append({
            "r": r, "engagement": round(e, 1), "age_days": round(age, 1),
            "velocity": round(vel, 2), "save_rate": round(sr, 3),
            "_lv": math.log10(e + 1.0),
            "_vv": math.log10(vel + 1.0),
            "_sv": math.log10(sr + 0.01),
        })

    nv, nvel, nsr = _norm([x["_lv"] for x in recs]), \
                    _norm([x["_vv"] for x in recs]), \
                    _norm([x["_sv"] for x in recs])

    for i, x in enumerate(recs):
        rel = 100.0 * (0.45 * nv[i] + 0.35 * nvel[i] + 0.20 * nsr[i])
        # نمرهٔ مطلق: log10(E+1) با ضریب ۲۰ → ۱k≈۶۰ · ۱۰k≈۸۰ · ۱۰۰k≈۱۰۰
        absolute = min(100.0, 20.0 * x["_lv"])
        score = round(0.5 * absolute + 0.5 * rel, 1)

        r = x["r"]
        r["engagement"] = x["engagement"]
        r["age_days"] = x["age_days"]
        r["velocity"] = x["velocity"]
        r["save_rate"] = x["save_rate"]
        r["score_absolute"] = round(absolute, 1)
        r["score_relative"] = round(rel, 1)
        r["score"] = score
        r["grade"] = next(g for th, g in GRADE_TABLE if score >= th)

    results.sort(key=lambda r: (-float(r.get("score") or 0),
                                -float(r.get("engagement") or 0)))
    for i, r in enumerate(results, 1):
        r["rank"] = i
    return results


def leaderboard(results: list, query: str = "") -> str:
    """جدول رتبه‌بندی متنی"""
    out = [f"🏆  <b>رتبه‌بندی — {esc_html(query)}</b>" if query else "🏆  <b>رتبه‌بندی</b>",
           LINE_TXT, ""]
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    for r in results:
        medal = medals.get(r.get("rank"), f"{r.get('rank')}.")
        out.append(
            f"{medal}  <b>{esc_html((r.get('title') or '—')[:48])}</b>\n"
            f"     امتیاز <code>{r.get('score')}</code>/۱۰۰  "
            f"（{r.get('grade')}）  ·  تعامل <code>{num_txt(r.get('engagement'))}</code>\n"
            f"     ❤️ {num_txt(r.get('likes'))}   💬 {num_txt(r.get('comment_count'))}   "
            f"📌 {num_txt(r.get('repin_count'))}   🔄 {num_txt(r.get('share_count'))}\n"
            f"     ⚡ {num_txt(r.get('velocity'))}/روز   ·   "
            f"📅 {r.get('age_days')} روزه\n"
        )
    out += [LINE_TXT,
            "راهنما: ❤️×۱ + 📌×۲ + 💬×۳ + 🔄×۴ = تعامل",
            "امتیاز = ۵۰٪ مطلق + ۵۰٪ نسبی（سرعت و نرخ ذخیره）"]
    return "\n".join(out)


def esc_html(x) -> str:
    import html as _h
    return _h.escape(str(x if x is not None else ""), quote=False)


def num_txt(n) -> str:
    try:
        return f"{int(float(n or 0)):,}"
    except (TypeError, ValueError):
        return "0"


LINE_TXT = "━━━━━━━━━━━━━━━━━━━━━━"


# ==============================================================================
#  جستجوی چندکلمه‌ای / هشتگ (همهٔ واژه‌ها با هم — AND)
# ==============================================================================
def pin_blob(pin: dict) -> str:
    """متنِ قابل جستجوی یک پین خام"""
    parts = [pin.get("title"), pin.get("grid_title"), pin.get("description"),
             pin.get("auto_alt_text"), pin.get("seo_alt_text"),
             pin.get("domain")]
    pj = pin.get("pin_join") or {}
    parts += list(pj.get("visual_annotation") or [])
    ann = pj.get("annotations_with_links") or {}
    parts += list(ann.keys()) if isinstance(ann, dict) else list(ann or [])
    board = pin.get("board") or {}
    parts.append(board.get("name"))
    return " ".join(str(p) for p in parts if p).lower()


def result_blob(r: dict) -> str:
    """متنِ قابل جستجوی یک پینِ نرمال‌شده"""
    return " ".join([r.get("title", ""), r.get("description", ""),
                     r.get("alt_text", ""), r.get("board_name", ""),
                     r.get("source_domain", "")] +
                    list(r.get("tags") or [])).lower()


def _term_ok(blob: str, term: str) -> bool:
    t = term.strip().lstrip("#").lower()
    if not t:
        return True
    if " " in t:                      # عبارت چندکلمه‌ای داخل یک ترم
        return t in blob
    # تطبیق به‌صورت واژهٔ کامل (نه زیررشته)
    return re.search(rf"(?<![a-z0-9]){re.escape(t)}", blob) is not None


def extract_multi(terms: list, count: int = 10, workers: int = 5,
                  detailed: bool = True, client=None) -> list:
    """
    جستجوی AND: فقط پین‌هایی که «همهٔ» واژه‌ها/هشتگ‌ها در آن‌ها باشد.

    راهبرد:
      ۱) جستجوی ترکیبیِ همهٔ واژه‌ها  → فیلتر AND روی متادیتا
      ۲) اگر کافی نبود → اشتراکِ (Intersection) نتایجِ جستجوی تکیِ هر واژه
    """
    import re as _re
    terms = [t.strip() for t in (terms or []) if t and t.strip()]
    if not terms:
        return []
    if len(terms) == 1:
        return extract(terms[0], count, workers, detailed)

    own = client is None
    if own:
        client = new_client(sleep_time=0.3)

    def blob_match(raw_pin) -> bool:
        blob = pin_blob(raw_pin)
        return all(_term_ok(blob, t) for t in terms)

    # ---- مرحله ۱: جستجوی ترکیبی ----
    pool = client.raw_search(" ".join(terms), max(count * 6, 60))
    matched, seen = [], set()
    for p in pool:
        if not (p.get("images") or {}).get("orig"):
            continue
        if p.get("id") in seen:
            continue
        if blob_match(p):
            seen.add(p.get("id"))
            matched.append(p)

    # ---- مرحله ۲: اشتراکِ جستجوهای تکی ----
    if len(matched) < count:
        per_term = []
        for t in terms:
            rs = client.raw_search(t, max(count * 5, 50))
            per_term.append({r.get("id"): r for r in rs if r.get("id")})
        if per_term:
            common = set(per_term[0])
            for d in per_term[1:]:
                common &= set(d)
            for pid in common:
                if pid in seen:
                    continue
                p = per_term[0][pid]
                if (p.get("images") or {}).get("orig") and blob_match(p):
                    seen.add(pid)
                    matched.append(p)

    matched = matched[:count]
    if not matched:
        return []

    if not detailed:
        return score_pins([normalize(p) for p in matched])

    workers = max(1, min(workers, len(matched)))
    clients = [new_client() for _ in range(workers)]
    chunks = [matched[i::workers] for i in range(workers)]

    def enrich(arg):
        chunk, cl = arg
        out = []
        for p in chunk:
            d = cl.pin_details(p.get("id"))
            out.append(normalize({**p, **{k: v for k, v in d.items()
                                          if v not in (None, "", [], {})}}))
        return out

    with ThreadPoolExecutor(max_workers=workers) as ex:
        parts = list(ex.map(enrich, zip(chunks, clients)))

    return score_pins([x for part in parts for x in part])


def parse_terms(text: str) -> list:
    """
    تبدیل ورودی کاربر به فهرست واژه‌ها.
      "wedding dress lace"  → ["wedding", "dress", "lace"]
      "#modeling #fashion"  → ["#modeling", "#fashion"]
      "modeling, fashion"   → ["modeling", "fashion"]
    پشتیبانی از نقل‌قول برای عبارت‌های چندکلمه‌ای:
      '"wedding dress" lace' → ["wedding dress", "lace"]
    """
    import shlex as _shlex
    text = (text or "").strip()
    if not text:
        return []
    try:
        parts = _shlex.split(text)
    except ValueError:
        parts = text.split()
    out = []
    for p in parts:
        p = p.strip().strip(",").strip()
        if p:
            out.append(p)
    return out
