"""Shared, conservative normalization and stable post identities.

Normalization is intentionally lexical, not semantic: it merges spelling and
hashtag-format variants, but does not claim to detect synonyms. Post identities
are platform-specific and never invent a permalink for a private source.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

_AR_TO_FA = str.maketrans({
    "ي": "ی", "ك": "ک", "ﻻ": "لا", "ة": "ه",
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ؤ": "و", "ئ": "ی",
})
_DIACRITICS = re.compile(r"[\u064B-\u065F\u0670\u0640]")
_CONTROLS = re.compile(r"[\u200b\u200c\u200d\u200e\u200f\u202a-\u202e\u2066-\u2069]")
_SEPARATORS = re.compile(r"[\s_\-]+")
_INVALID_TERM = re.compile(r"[^\w\u0600-\u06FF\u0750-\u077F]+", re.UNICODE)
_SHORTCODE = re.compile(r"^[A-Za-z0-9_-]{3,64}$")
_IG_PATH = re.compile(r"^/(?:[A-Za-z0-9_.]+/)?(?:p|reel|reels|tv)/([A-Za-z0-9_-]{3,64})/?$")
_PIN_PATH = re.compile(r"^/pin/(\d+)/?$")
_TG_PATH = re.compile(r"^/(?:([A-Za-z][A-Za-z0-9_]{3,})|c/(\d+))/(\d+)/?$")
_TG_UID = re.compile(r"^telegram:(-?\d+)$")


def normalize_search_keyword(value):
    """Canonical Instagram/Telegram hashtag form used for reservations."""
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFKC", value).strip()
    value = re.sub(r"^#+\s*", "", value)
    value = _CONTROLS.sub(" ", value).translate(_AR_TO_FA)
    value = _DIACRITICS.sub("", value).casefold()
    value = _SEPARATORS.sub("_", value)
    value = _INVALID_TERM.sub("", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value if 2 <= len(value) <= 64 else ""


def plain_search_text(value):
    """Natural text equivalent of a hashtag, preserving useful punctuation."""
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFKC", value).strip()
    value = re.sub(r"^#+\s*", "", value)
    value = _CONTROLS.sub(" ", value).translate(_AR_TO_FA)
    value = _DIACRITICS.sub("", value)
    value = value.replace("_", " ")
    return " ".join(value.split())[:160]


def _https_url(url, allowed_hosts):
    if not isinstance(url, str) or len(url) > 2048:
        return None
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
        if parts.scheme not in ("http", "https") or parts.username or parts.password:
            return None
        if not any(host == h or host.endswith("." + h) for h in allowed_hosts):
            return None
        return urlunsplit(("https", host, parts.path, "", ""))
    except ValueError:
        return None


def post_identity(platform, *, url=None, short_code=None, pin_id=None,
                  channel_uid=None, message_id=None):
    """Return a stable primary key, canonical public URL and lookup aliases."""
    platform = (platform or "").lower()
    aliases = []
    if platform == "instagram":
        code = None
        clean = _https_url(url, ("instagram.com",))
        if clean:
            match = _IG_PATH.match(urlsplit(clean).path)
            if match:
                code = match.group(1)
        if not code and isinstance(short_code, str) and _SHORTCODE.fullmatch(short_code.strip()):
            code = short_code.strip()
        if not code:
            return None
        canonical = f"https://www.instagram.com/p/{code}/"
        key = f"instagram:shortcode:{code}"
        aliases = [key, f"instagram:url:{canonical}"]
        return {"key": key, "url": canonical, "aliases": aliases}
    if platform == "pinterest":
        identifier = str(pin_id).strip() if pin_id is not None else ""
        clean = _https_url(url, ("pinterest.com",))
        if clean:
            match = _PIN_PATH.match(urlsplit(clean).path)
            if match:
                identifier = match.group(1)
        if not identifier.isdigit():
            return None
        canonical = f"https://www.pinterest.com/pin/{identifier}/"
        key = f"pinterest:pin:{identifier}"
        return {"key": key, "url": canonical, "aliases": [key, f"pinterest:url:{canonical}"]}
    if platform == "telegram":
        canonical = ""
        clean = _https_url(url, ("t.me", "telegram.me"))
        if clean:
            match = _TG_PATH.match(urlsplit(clean).path)
            if match:
                username, internal, mid = match.groups()
                canonical = (f"https://t.me/{username}/{mid}" if username
                             else f"https://t.me/c/{internal}/{mid}")
        native = _TG_UID.match(str(channel_uid or ""))
        try:
            mid = int(message_id)
        except (TypeError, ValueError):
            mid = 0
        if native and mid > 0:
            key = f"telegram:message:{native.group(1)}:{mid}"
            aliases = [key]
            if canonical:
                aliases.append(f"telegram:url:{canonical}")
            return {"key": key, "url": canonical, "aliases": aliases}
        if canonical:
            key = f"telegram:url:{canonical}"
            return {"key": key, "url": canonical, "aliases": [key]}
    return None


def record_identity(record):
    platform = record.get("platform", "")
    raw = record.get("raw") or {}
    return post_identity(
        platform,
        url=record.get("url") or raw.get("url") or raw.get("pin_url"),
        short_code=raw.get("shortCode") or raw.get("shortcode") or record.get("shortCode"),
        pin_id=raw.get("pin_id") or record.get("pin_id"),
        channel_uid=record.get("channel_uid") or raw.get("channel_uid"),
        message_id=record.get("message_id") or raw.get("message_id"),
    )


def dedupe_records(records):
    """Merge equivalent posts inside one run before any AI call."""
    out, alias_to_index = [], {}
    duplicates = 0
    for position, record in enumerate(records):
        identity = record_identity(record)
        aliases = identity["aliases"] if identity else [f"unidentified:{position}"]
        target = next((alias_to_index[a] for a in aliases if a in alias_to_index), None)
        if target is None:
            item = dict(record)
            item["post_key"] = identity["key"] if identity else ""
            item["canonical_url"] = identity["url"] if identity else record.get("url", "")
            if identity and identity["url"]:
                item["url"] = identity["url"]
            item["duplicate_count"] = 0
            item["search_terms"] = list(record.get("search_terms") or [])
            target = len(out)
            out.append(item)
            for alias in aliases:
                alias_to_index[alias] = target
        else:
            duplicates += 1
            item = out[target]
            item["duplicate_count"] += 1
            for term in record.get("search_terms") or []:
                if term not in item["search_terms"]:
                    item["search_terms"].append(term)
            # Preserve the richest media/comment payload without discarding the first source.
            for key in ("comments", "media"):
                if record.get(key) and not item.get(key):
                    item[key] = record[key]
    return out, duplicates
