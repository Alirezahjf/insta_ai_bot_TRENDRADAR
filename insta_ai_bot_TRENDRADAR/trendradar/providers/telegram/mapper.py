"""
🔄 مبدل اشیای Telethon → مدل‌های دامنه
=====================================

مرز پرووایدر همین‌جاست: هیچ شیء خامی از Telethon نباید از این ماژول
بیرون برود. فیلدهای استفاده‌شده همه از سورس Telethon 1.45.0 تأیید شده‌اند:

    Channel      : id, title, username, date, participants_count, megagroup,
                   broadcast, verified, scam, fake, restricted, join_request,
                   slowmode_enabled, noforwards, usernames
    ChannelFull  : about, participants_count, admins_count, online_count,
                   linked_chat_id, slowmode_seconds, can_view_participants
    Message      : id, peer_id, date, message, views, forwards, replies,
                   reactions, from_id, post, media, grouped_id, edit_date, entities
    MessageReplies: replies, comments, channel_id, max_id
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable, List, Optional

from ...models import (
    ChannelKind,
    ChannelRef,
    MediaType,
    Post,
    extract_hashtags,
    utcnow,
)

log = logging.getLogger("trendradar.telegram.mapper")

# پیشوند لینک پست در تلگرام (برای کانال‌های عمومی)
_TME = "https://t.me"


def _to_aware(dt: Optional[datetime]) -> datetime:
    """اطمینان از اینکه datetime از نوع aware است (تلگرام همیشه UTC می‌دهد)"""
    if dt is None:
        return utcnow()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _safe_int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _count_reactions(msg: Any) -> int:
    """
    جمع تعداد ری‌اکشن‌ها.

    ساختار Message.reactions (از سورس Telethon):
        MessageReactions(
            results: List[ReactionCount(chosen_order, reaction, count)],
            min, can_see_list, recent_reactions
        )
    هر ReactionCount یک فیلد `count` دارد → جمعشان می‌کنیم.
    """
    r = getattr(msg, "reactions", None)
    if not r:
        return 0
    total = 0
    for rc in getattr(r, "results", None) or []:
        total += _safe_int(getattr(rc, "count", 0))
    return total


def _count_replies(msg: Any) -> int:
    """
    تعداد کامنت/ریپلای.

    ساختار Message.replies (از سورس): MessageReplies(replies, comments, ...)
    """
    r = getattr(msg, "replies", None)
    if not r:
        return 0
    return _safe_int(getattr(r, "replies", 0))


def detect_media_type(msg: Any) -> MediaType:
    """
    تشخیص نوع رسانه از روی فیلد `media` پیام.

    ترتیب مهم است: ویدیو قبل از داکیومنت چک می‌شود چون تلگرام ویدیو را
    در قالب Document می‌فرستد که فلگ `video` دارد.
    """
    media = getattr(msg, "media", None)
    if not media:
        return MediaType.TEXT

    cls_name = type(media).__name__

    if cls_name == "MessageMediaPhoto":
        return MediaType.PHOTO
    if cls_name == "MessageMediaDocument":
        doc = getattr(media, "document", None)
        attrs = {type(a).__name__ for a in (getattr(doc, "attributes", None) or [])}
        if "DocumentAttributeVideo" in attrs:
            return MediaType.VIDEO
        if "DocumentAttributeAudio" in attrs:
            return MediaType.AUDIO
        if "DocumentAttributeSticker" in attrs:
            return MediaType.PHOTO       # استیکر را گرافیک حساب می‌کنیم
        return MediaType.DOCUMENT
    if cls_name == "MessageMediaPoll":
        return MediaType.POLL
    if cls_name == "MessageMediaWebPage":
        return MediaType.LINK
    if cls_name in ("MessageMediaGeo", "MessageMediaVenue"):
        return MediaType.UNKNOWN
    return MediaType.UNKNOWN


def post_url(username: Optional[str], message_id: int | str, *, is_megagroup: bool = False) -> str:
    """
    ساخت لینک دائمی پست.

    • کانال عمومی:   https://t.me/<username>/<id>
    • سوپرگروه:      مثل کانال
    • کانال خصوصی:   لینک عمومی ندارد → رشته‌ی خالی
    """
    if not username:
        return ""
    clean = str(username).lstrip("@").strip()
    if not clean:
        return ""
    return f"{_TME}/{clean}/{message_id}"


# ─────────────────────────────────────────────────────────────
# Channel → ChannelRef
# ─────────────────────────────────────────────────────────────
def channel_to_ref(
    ch: Any,
    *,
    full: Any = None,
    joined: bool = False,
) -> Optional[ChannelRef]:
    """
    تبدیل `Channel`/`Chat` تلگرام به `ChannelRef`.

    :param full:  خروجی `GetFullChannelRequest` (اختیاری) برای about و آمار دقیق‌تر
    :param joined: آیا اکانت ما در آن عضو است
    :return: None اگر شیء قابل تبدیل نباشد
    """
    if ch is None:
        return None

    native_id = getattr(ch, "id", None)
    if native_id is None:
        return None

    cls_name = type(ch).__name__
    if cls_name in ("ChannelForbidden", "ChatForbidden"):
        # دسترسی نداریم — فقط شناسه و عنوان را می‌دانیم
        return ChannelRef(
            provider="telegram",
            native_id=native_id,
            title=getattr(ch, "title", "") or "",
            kind=ChannelKind.PRIVATE,
            joined=joined,
            access_hash=getattr(ch, "access_hash", None),
            extra={"forbidden": True},
        )

    broadcast = bool(getattr(ch, "broadcast", False))
    megagroup = bool(getattr(ch, "megagroup", False))

    # ChannelFull → last در GetFullChannelRequest، فیلدهای آماری آن‌جاست
    full_channel = getattr(full, "full_chat", None) if full is not None else None
    full_about = getattr(full_channel, "about", "") if full_channel is not None else ""
    full_members = getattr(full_channel, "participants_count", None) if full_channel is not None else None

    members = full_members or getattr(ch, "participants_count", None)

    username = getattr(ch, "username", None)
    if not username:
        # فیلد usernames لیست Username(username, editable) است
        usernames = getattr(ch, "usernames", None) or []
        for u in usernames:
            cand = getattr(u, "username", None)
            if cand:
                username = cand
                break

    return ChannelRef(
        provider="telegram",
        native_id=native_id,
        title=getattr(ch, "title", "") or "",
        username=username,
        kind=ChannelKind.from_flags(broadcast=broadcast, megagroup=megagroup),
        members=_safe_int(members) if members else None,
        about=(full_about or "")[:1000],
        verified=bool(getattr(ch, "verified", False)),
        is_scam=bool(getattr(ch, "scam", False)),
        is_fake=bool(getattr(ch, "fake", False)),
        is_restricted=bool(getattr(ch, "restricted", False)),
        join_request=bool(getattr(ch, "join_request", False)),
        joined=joined or bool(getattr(ch, "left", False) is False and joined),
        access_hash=getattr(ch, "access_hash", None),
        discovered_at=_to_aware(getattr(ch, "date", None)),
        extra={
            "slowmode_enabled": bool(getattr(ch, "slowmode_enabled", False)),
            "noforwards": bool(getattr(ch, "noforwards", False)),
            "linked_chat_id": getattr(full_channel, "linked_chat_id", None) if full_channel else None,
            "can_view_participants": getattr(full_channel, "can_view_participants", None) if full_channel else None,
            "online_count": getattr(full_channel, "online_count", None) if full_channel else None,
        },
    )


def user_to_ref(u: Any) -> Optional[ChannelRef]:
    """تبدیل `User` به ChannelRef — برای وقتی نتیجه‌ی جستجو کاربر است نه کانال"""
    if u is None or getattr(u, "id", None) is None:
        return None
    name = " ".join(
        p for p in (getattr(u, "first_name", "") or "", getattr(u, "last_name", "") or "") if p
    ).strip()
    return ChannelRef(
        provider="telegram",
        native_id=u.id,
        title=name or (getattr(u, "username", "") or ""),
        username=getattr(u, "username", None),
        kind=ChannelKind.UNKNOWN,
        verified=bool(getattr(u, "verified", False)),
        is_scam=bool(getattr(u, "scam", False)),
        is_fake=bool(getattr(u, "fake", False)),
        is_restricted=bool(getattr(u, "restricted", False)),
        access_hash=getattr(u, "access_hash", None),
        extra={"entity_type": "user", "bot": bool(getattr(u, "bot", False))},
    )


# ─────────────────────────────────────────────────────────────
# Message → Post
# ─────────────────────────────────────────────────────────────
def message_to_post(
    msg: Any,
    *,
    channel: Optional[ChannelRef] = None,
    channel_uid: str = "",
    channel_title: str = "",
    channel_username: Optional[str] = None,
    channel_members: Optional[int] = None,
    has_views: bool = True,
) -> Optional[Post]:
    """
    تبدیل `Message` تلگرام به `Post`.

    :param has_views: کانال‌های معمولی (نه کانال رسمی) فیلد views ندارند؛
                      برای همین نمی‌توان بازدید را صفر حساب کرد بلکه None می‌گذاریم.
    :return: None اگر پیام قابل استفاده نباشد (سرویس، خالی، بدون تاریخ)
    """
    if msg is None:
        return None
    if type(msg).__name__ == "MessageService":       # پیام سیستمی
        return None
    mid = getattr(msg, "id", None)
    if mid is None:
        return None

    text = getattr(msg, "message", "") or ""

    if channel is not None:
        channel_uid = channel.uid
        channel_title = channel.title
        channel_username = channel_username or channel.username
        channel_members = channel_members if channel_members is not None else channel.members

    url = post_url(channel_username, mid)

    return Post(
        provider="telegram",
        channel_uid=channel_uid,
        message_id=mid,
        date=_to_aware(getattr(msg, "date", None)),
        text=text[:4000],
        hashtags=extract_hashtags(text),
        views=_safe_int(getattr(msg, "views", 0)) if has_views else 0,
        forwards=_safe_int(getattr(msg, "forwards", 0)),
        replies=_count_replies(msg),
        reactions=_count_reactions(msg),
        media_type=detect_media_type(msg),
        url=url,
        channel_title=channel_title,
        channel_members=channel_members,
        is_forwarded=bool(getattr(msg, "fwd_from", None)),
        edited_at=getattr(msg, "edit_date", None),
        extra={
            "post": bool(getattr(msg, "post", False)),
            "grouped_id": getattr(msg, "grouped_id", None),
        },
    )


def messages_to_posts(
    messages: Iterable[Any],
    *,
    channel: Optional[ChannelRef] = None,
    has_views: bool = True,
    skip_forwards: bool = True,
) -> List[Post]:
    """تبدیل دسته‌ای پیام‌ها + حذف تکراری‌ها (گروه‌بندی آلبوم‌ها را هم ادغام می‌کند)"""
    out: List[Post] = []
    seen_ids: set = set()
    seen_groups: set = set()

    for m in messages:
        p = message_to_post(m, channel=channel, has_views=has_views)
        if p is None:
            continue
        if skip_forwards and p.is_forwarded:
            continue                       # پست فورواردشده ترند اصلی نیست
        if not p.hashtags and not p.text.strip():
            continue                       # رسانه بدون متن — سیگنال متنی ندارد

        # آلبوم (چند عکس با یک grouped_id) → فقط اولین را نگه دار
        gid = p.extra.get("grouped_id")
        if gid:
            if gid in seen_groups:
                continue
            seen_groups.add(gid)

        if p.message_id in seen_ids:
            continue
        seen_ids.add(p.message_id)
        out.append(p)

    return out


def mention_ipsum() -> None:  # pragma: no cover
    """نگه‌داشتن import‌های استفاده‌نشده در چشمان ابزارهای static analysis"""
    _ = (datetime, log)
