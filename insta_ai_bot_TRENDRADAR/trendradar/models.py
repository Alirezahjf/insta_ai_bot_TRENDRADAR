"""
مدل‌های دامنه — قلب سیستم، کاملاً مستقل از هر پیام‌رسان.

هر پیام‌رسان باید داده‌های خام خودش را به این مدل‌ها تبدیل کند (`mapper`).
با این کار، منطق تحلیل ترند فقط یک‌بار نوشته می‌شود و برای همه‌ی
پیام‌رسان‌ها کار می‌کند.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


# ─────────────────────────────────────────────────────────────
# شمارشی‌ها
# ─────────────────────────────────────────────────────────────
class ChannelKind(str, Enum):
    """نوع کانال/گروه در پیام‌رسان‌های مختلف"""

    BROADCAST = "broadcast"   # کانال (فقط ادمین پست می‌ده) — معادل تلگرام
    GROUP = "group"           # گروه گفتگو (همه پست می‌دهند)
    SUPERGROUP = "supergroup"
    PRIVATE = "private"
    UNKNOWN = "unknown"

    @classmethod
    def from_flags(cls, *, broadcast: bool = False, megagroup: bool = False) -> "ChannelKind":
        """ساخت از فلگ‌های تلگرام (Channel.broadcast / Channel.megagroup)"""
        if broadcast:
            return cls.BROADCAST
        if megagroup:
            return cls.SUPERGROUP
        return cls.UNKNOWN


class MediaType(str, Enum):
    """نوع رسانه‌ی پست — برای وزن‌دهی متفاوت در تحلیل ترند"""

    TEXT = "text"
    PHOTO = "photo"
    VIDEO = "video"
    DOCUMENT = "document"
    LINK = "link"
    POLL = "poll"
    AUDIO = "audio"
    UNKNOWN = "unknown"


# ─────────────────────────────────────────────────────────────
# نرمال‌سازی هشتگ و متن
# ─────────────────────────────────────────────────────────────
# اعراب فارسی/عربی + کشیده + نیم‌فاصله‌ها
_DIACRITICS = re.compile(r"[\u064B-\u065F\u0670\u0640]")
_ZWNJ = re.compile(r"[\u200c\u200f\u200e]")
_NON_ALNUM = re.compile(r"[^\w\u0600-\u06FF\u0750-\u077F]+", re.UNICODE)

# حروف عربی → فارسی (تا «كتاب» و «کتاب» یک هشتگ حساب شوند)
_AR_TO_FA = str.maketrans({
    "ي": "ی", "ك": "ک", "ﻻ": "لا", "ة": "ه",
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ؤ": "و", "ئ": "ی",
})


def normalize_hashtag(raw: str) -> str:
    """
    نرمال‌سازی هشتگ — تا «#ترند» و «# ترند» و «#تُرند» یکی حساب شوند.

    >>> normalize_hashtag(" #تُرند_مدلینگ ")
    'ترند_مدلینگ'
    """
    if not raw:
        return ""
    tag = str(raw).strip().lstrip("#").strip()
    tag = tag.translate(_AR_TO_FA)
    tag = _DIACRITICS.sub("", tag)
    tag = _ZWNJ.sub(" ", tag)
    tag = " ".join(tag.split())          # چند فاصله → یک فاصله
    tag = tag.replace(" ", "_")          # تلگرام فاصله را نمی‌پذیرد
    tag = _NON_ALNUM.sub("", tag)
    return tag.strip("_").lower()


# الگوی هشتگ در متن — حروف فارسی + لاتین + عدد + زیرخط
HASHTAG_RE = re.compile(r"#([\w\u0600-\u06FF\u0750-\u077F]{2,64})", re.UNICODE)


def extract_hashtags(text: str) -> List[str]:
    """استخراج و نرمال‌سازی همه‌ی هشتگ‌های یک متن"""
    if not text:
        return []
    out: List[str] = []
    for m in HASHTAG_RE.finditer(str(text)):
        tag = normalize_hashtag(m.group(1))
        if tag and tag not in out:
            out.append(tag)
    return out


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────
# مدل‌های اصلی
# ─────────────────────────────────────────────────────────────
@dataclass(slots=True)
class ChannelRef:
    """
    ارجاع به یک کانال/گروه در یک پیام‌رسان خاص.

    `provider` + `native_id` کلید یکتای جهانی است.
    """

    provider: str                        # "telegram" | "instagram" | "rubika" ...
    native_id: int | str                 # شناسه در آن پیام‌رسان
    title: str = ""
    username: Optional[str] = None       # @username (اگر عمومی باشد)
    kind: ChannelKind = ChannelKind.UNKNOWN
    members: Optional[int] = None
    about: str = ""
    verified: bool = False
    is_scam: bool = False
    is_fake: bool = False
    is_restricted: bool = False
    join_request: bool = False           # عضویت نیاز به تایید ادمین دارد
    joined: bool = False                 # ما عضو هستیم؟
    access_hash: Optional[int] = None    # برای ساخت InputChannel (تلگرام)
    language: Optional[str] = None
    discovered_at: datetime = field(default_factory=utcnow)
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def uid(self) -> str:
        """کلید یکتای جهانی"""
        return f"{self.provider}:{self.native_id}"

    @property
    def url(self) -> str:
        """لینک عمومی — به‌صورت پیش‌فرض تلگرام؛ پرووایدرها override می‌کنند"""
        if self.username:
            return f"https://t.me/{self.username}"
        return ""

    @property
    def risk_flags(self) -> List[str]:
        """نشانه‌های خطر — چنل‌های اسکم/فیک/ریستریکت‌شده نباید انتخاب شوند"""
        flags: List[str] = []
        if self.is_scam:
            flags.append("scam")
        if self.is_fake:
            flags.append("fake")
        if self.is_restricted:
            flags.append("restricted")
        if self.join_request:
            flags.append("join_request")
        return flags

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["uid"] = self.uid
        d["url"] = self.url
        d["risk_flags"] = self.risk_flags
        return d


@dataclass(slots=True)
class Post:
    """یک پیام/پست جمع‌آوری‌شده از یک کانال"""

    provider: str
    channel_uid: str                     # ChannelRef.uid
    message_id: int | str
    date: datetime
    text: str = ""
    hashtags: List[str] = field(default_factory=list)
    views: int = 0
    forwards: int = 0
    replies: int = 0                     # تعداد کامنت/ریپلای
    reactions: int = 0
    media_type: MediaType = MediaType.TEXT
    url: str = ""
    channel_title: str = ""
    channel_members: Optional[int] = None
    is_forwarded: bool = False
    edited_at: Optional[datetime] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def uid(self) -> str:
        return f"{self.channel_uid}:{self.message_id}"

    @property
    def engagement(self) -> int:
        """تعامل کل — معیار خام محبوبیت"""
        return int(self.reactions) + int(self.forwards)

    @property
    def engagement_rate(self) -> float:
        """
        نرخ تعامل — نسبت تعامل به بازدید.
        اگر بازدید نداشته باشیم (نان‌کانال‌ها ویو ندارند)، از اعضا تخمین می‌زنیم.
        """
        base = self.views or self.channel_members or 0
        if not base:
            return 0.0
        return min(self.engagement / base, 5.0)   # سقف ۵۰۰٪ تا داده‌ی پرت اثر نگذارد

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["media_type"] = self.media_type.value
        d["date"] = self.date.isoformat()
        d["uid"] = self.uid
        d["engagement"] = self.engagement
        d["engagement_rate"] = round(self.engagement_rate, 4)
        return d


@dataclass(slots=True)
class HashtagStat:
    """
    آمار تجمیعی یک هشتگ در یک پنجره‌ی زمانی.

    این خروجی خام تحلیل است؛ امتیاز نهایی در `TrendSignal` می‌آید.
    """

    tag: str
    provider: str
    posts: int = 0
    channels: int = 0
    total_views: float = 0
    total_engagement: float = 0
    median_views: float = 0
    max_views: int = 0
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    window_hours: float = 0.0
    sample_post_uids: List[str] = field(default_factory=list)

    @property
    def posts_per_hour(self) -> float:
        """سرعت انتشار — مهم‌ترین شاخص ترند بودن"""
        if self.window_hours <= 0:
            return 0.0
        return self.posts / self.window_hours

    @property
    def channels_per_post(self) -> float:
        """تنوع منابع — اگر یک هشتگ در ۱۰ چنل مختلف باشد، ترند قوی‌تری است"""
        if not self.posts:
            return 0.0
        return self.channels / self.posts

    @property
    def avg_views(self) -> float:
        return (self.total_views / self.posts) if self.posts else 0.0

    @property
    def avg_engagement(self) -> float:
        return (self.total_engagement / self.posts) if self.posts else 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["posts_per_hour"] = round(self.posts_per_hour, 3)
        d["channels_per_post"] = round(self.channels_per_post, 3)
        d["avg_views"] = round(self.avg_views, 1)
        d["avg_engagement"] = round(self.avg_engagement, 1)
        for k in ("first_seen", "last_seen"):
            if d.get(k):
                d[k] = d[k].isoformat()
        return d


@dataclass(slots=True)
class TrendSignal:
    """
    خروجی نهایی تحلیل — یک هشتگ با امتیاز و توضیح.

    `breakdown` شفافیت می‌دهد: کدام متریک چند امتیاز داد و با چه وزنی.
    """

    tag: str
    provider: str
    provider_name: str = ""
    score: float = 0.0                   # ۰ تا ۱۰۰
    rank: int = 0
    stat: Optional[HashtagStat] = None
    breakdown: Dict[str, float] = field(default_factory=dict)   # متریک → مقدار نرمال‌شده ۰..۱
    weights: Dict[str, float] = field(default_factory=dict)     # متریک → وزن
    top_posts: List[Post] = field(default_factory=list)
    top_channels: List[str] = field(default_factory=list)
    reason: str = ""                     # توضیح فارسی برای نمایش به کاربر
    detected_at: datetime = field(default_factory=utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tag": self.tag,
            "provider": self.provider,
            "provider_name": self.provider_name,
            "score": round(self.score, 2),
            "rank": self.rank,
            "breakdown": {k: round(v, 3) for k, v in self.breakdown.items()},
            "weights": self.weights,
            "top_channels": self.top_channels,
            "reason": self.reason,
            "detected_at": self.detected_at.isoformat(),
            "posts": [p.to_dict() for p in self.top_posts],
            "stat": self.stat.to_dict() if self.stat else None,
        }


@dataclass(slots=True)
class SearchQuery:
    """درخواست جستجو — مستقل از پیام‌رسان"""

    hashtags: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    limit_per_tag: int = 50
    max_channels: int = 20               # حداکثر چنلی که برای یک جستجو بررسی می‌شود
    discover_channels: bool = True       # چنل جدید پیدا کنیم؟
    auto_join: bool = False              # خودکار عضو بشیم؟ (پرخطر — پیش‌فرض خاموش)
    joined_only: bool = False            # فقط در چنل‌های خودمان جستجو کنیم؟
    window_hours: int = 72               # پنجره‌ی زمانی ترند
    min_members: int = 500               # فیلتر حداقلی اعضا
    languages: List[str] = field(default_factory=list)
    providers: List[str] = field(default_factory=list)   # خالی = همه
    exclude_channel_uids: List[str] = field(default_factory=list)

    def normalized_hashtags(self) -> List[str]:
        seen: List[str] = []
        for t in self.hashtags:
            n = normalize_hashtag(t)
            if n and n not in seen:
                seen.append(n)
        return seen

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["hashtags"] = self.normalized_hashtags()
        return d


@dataclass(slots=True)
class ProviderHealth:
    """وضعیت سلامت یک پرووایدر — برای نمایش در پنل و تصمیم‌گیری پاییپ‌لاین"""

    provider: str
    name: str
    available: bool = False              # کتابخانه نصب است؟
    authorized: bool = False             # لاگین انجام شده؟
    account: Optional[str] = None        # نام اکانت userbot
    error: Optional[str] = None
    rate_limited_until: Optional[datetime] = None
    capabilities: List[str] = field(default_factory=list)

    @property
    def status_emoji(self) -> str:
        if not self.available:
            return "⚪"
        if self.error:
            return "🔴"
        if not self.authorized:
            return "🟡"
        if self.rate_limited_until and self.rate_limited_until > utcnow():
            return "🟠"
        return "🟢"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status_emoji"] = self.status_emoji
        if self.rate_limited_until:
            d["rate_limited_until"] = self.rate_limited_until.isoformat()
        return d


@dataclass
class RunReport:
    """گزارش کامل یک اجرای ترند‌یابی"""

    run_id: str
    query: SearchQuery
    started_at: datetime = field(default_factory=utcnow)
    finished_at: Optional[datetime] = None
    signals: List[TrendSignal] = field(default_factory=list)
    channels_scanned: int = 0
    channels_discovered: int = 0
    channels_joined: int = 0
    posts_collected: int = 0
    errors: List[str] = field(default_factory=list)
    provider_stats: Dict[str, Dict[str, int]] = field(default_factory=dict)

    @property
    def duration_sec(self) -> float:
        end = self.finished_at or utcnow()
        return (end - self.started_at).total_seconds()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "query": self.query.to_dict(),
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_sec": round(self.duration_sec, 1),
            "channels_scanned": self.channels_scanned,
            "channels_discovered": self.channels_discovered,
            "channels_joined": self.channels_joined,
            "posts_collected": self.posts_collected,
            "provider_stats": self.provider_stats,
            "errors": self.errors,
            "signals": [s.to_dict() for s in self.signals],
        }
