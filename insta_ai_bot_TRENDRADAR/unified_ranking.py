"""Heuristic cross-platform shortlist, based on within-platform ranks, not raw metrics."""
import math


def build_unified_ranking(instagram, telegram_report, pinterest):
    groups = {"Instagram": [], "Telegram": [], "Pinterest": []}
    seen = set()

    def add(platform, uid, title, url, score):
        if not uid or (platform, str(uid)) in seen:
            return
        try:
            score = float(score)
            if not math.isfinite(score):
                return
        except (TypeError, ValueError):
            return
        seen.add((platform, str(uid)))
        groups[platform].append({"platform": platform, "title": str(title or "بدون عنوان")[:160],
                                 "url": str(url or ""), "native_score": score})

    for post in instagram or []:
        add("Instagram", post.get("url") or post.get("shortCode"), post.get("caption") or post.get("ownerUsername"),
            post.get("url"), post.get("viral_score"))
    for signal in getattr(telegram_report, "signals", []) or []:
        for post in signal.top_posts:
            add("Telegram", post.uid, post.text or post.channel_title, post.url, signal.score)
    for pin in pinterest or []:
        add("Pinterest", pin.get("pin_id") or pin.get("pin_url"), pin.get("title"), pin.get("pin_url"), pin.get("score"))

    out = []
    for items in groups.values():
        items.sort(key=lambda x: -x["native_score"])
        # Equal native scores have equal percentile; a singleton gets a neutral 50.
        n = len(items)
        for i, item in enumerate(items):
            higher = sum(other["native_score"] > item["native_score"] for other in items)
            equal = sum(other["native_score"] == item["native_score"] for other in items)
            item["relative_score"] = round(100 * (1 - (higher + (equal - 1) / 2) / (n - 1)), 1) if n > 1 else 50.0
            item["platform_count"] = n
            out.append(item)
    return sorted(out, key=lambda item: (-item["relative_score"], item["platform"], item["title"]))
