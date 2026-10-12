"""کار با Apify — سازگار با apify-client قدیمی (dict) و جدید v3 (آبجکت)"""
import re

import config

_client = None


def get_apify():
    global _client
    if _client is None:
        from apify_client import ApifyClient
        _client = ApifyClient(config.APIFY_API_TOKEN)
    return _client


def call_actor(client, actor_id, run_input):
    try:
        if config.SHOW_APIFY_LOGS:
            return client.actor(actor_id).call(run_input=run_input)
        return client.actor(actor_id).call(run_input=run_input, logger=None)
    except TypeError:
        return client.actor(actor_id).call(run_input=run_input)


def run_field(run, dict_key, obj_attr):
    if isinstance(run, dict):
        return run.get(dict_key)
    v = getattr(run, obj_attr, None)
    return getattr(v, "value", v)


def dataset_id_of(run):
    return run_field(run, "defaultDatasetId", "default_dataset_id")


def status_of(run):
    return run_field(run, "status", "status")


def scrape_media(urls, results_type, limit):
    """اسکرپ پست/ریلز هشتگ‌ها — خروجی: لیست آیتم‌ها"""
    client = get_apify()
    run = call_actor(client, "apify/instagram-scraper", {
        "directUrls": urls,
        "resultsType": results_type,
        "resultsLimit": limit,
    })
    return list(client.dataset(dataset_id_of(run)).iterate_items())


_POST_URL_RE = re.compile(
    r"^https?://(?:www\.)?instagram\.com/(?:[^/\s?]+/)?(?:p|reel|reels)/[^/\s?]+/?(?:\?.*)?$",
    re.IGNORECASE,
)
_SHORTCODE_RE = re.compile(r"^[A-Za-z0-9_-]{3,64}$")


def clean_post_url(item):
    """URL معتبر پست برای کامنت‌اسکرپر؛ اگه خراب بود از shortCode بازسازی می‌کنه؛ اگه نشد None"""
    url = str(item.get("url") or "").strip()
    if url and _POST_URL_RE.match(url):
        return url
    sc = str(item.get("shortCode") or "").strip().strip("/")
    if sc and _SHORTCODE_RE.match(sc):
        return f"https://www.instagram.com/p/{sc}/"
    return None


def valid_comment_urls(posts):
    """از لیست پست‌ها فقط URLهای معتبر و بدون تکرار"""
    out = []
    for x in posts or []:
        u = clean_post_url(x)
        if u and u not in out:
            out.append(u)
    return out


def scrape_comments(post_urls, limit_per_post):
    """اسکرپ کامنت چند پست — خروجی: لیست کامنت‌ها (اگه URL معتبری نباشه: [])"""
    valid = [u for u in (post_urls or []) if isinstance(u, str) and _POST_URL_RE.match(u.strip())]
    if not valid:
        print("⚠️ scrape_comments: no valid post URLs, skipping")
        return []
    client = get_apify()
    run = call_actor(client, "apify/instagram-comment-scraper", {
        "directUrls": valid,
        "resultsLimit": limit_per_post,
    })
    return list(client.dataset(dataset_id_of(run)).iterate_items())


def post_key_of_comment(c):
    """کلید اتصال کامنت به پست (shortCode یا URL)"""
    for k in ("postUrl", "inputUrl", "postShortCode"):
        v = c.get(k)
        if v:
            return str(v)
    return ""
