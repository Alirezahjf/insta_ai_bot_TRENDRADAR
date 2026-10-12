"""
تنظیمات TrendRadar — خواندن از همان `.env` پروژه.

همه‌ی کلیدها با پیشوند TREND_ یا TG_ هستند تا با تنظیمات ربات موجود
تداخل نکنند.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

# تلاش برای لود .env (هم‌راستا با config.py پروژه)
try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv()
except Exception:  # pragma: no cover
    pass


def _env(key: str, default: str = "") -> str:
    v = os.getenv(key)
    return v.strip() if isinstance(v, str) and v.strip() else default


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(_env(key, str(default)))
    except (TypeError, ValueError):
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    return _env(key, "1" if default else "0").lower() in ("1", "true", "yes", "on")


def _env_list(key: str, default: str = "") -> List[str]:
    raw = _env(key, default)
    return [p.strip() for p in raw.split(",") if p.strip()]


# ─────────────────────────────────────────────────────────────
# تلگرام (MTProto / Telethon)
# ─────────────────────────────────────────────────────────────
@dataclass
class TelegramSettings:
    """تنظیمات اتصال به تلگرام از طریق MTProto (اکانت کاربری)"""

    api_id: int = 0
    api_hash: str = ""
    phone: str = ""                                  # +98912...
    session_name: str = "trend_telegram"
    session_dir: str = "."                           # محل فایل .session
    password: str = ""                               # رمز دو مرحله‌ای (اگر فعال باشد)
    device_model: str = "TrendRadar"
    system_version: str = "Linux"
    app_version: str = "1.0"

    # ── شبکه و پایداری ──
    timeout: int = 15
    request_retries: int = 5
    connection_retries: int = 5
    retry_delay: int = 2
    flood_sleep_threshold: int = 60                  # زیر این مقدار، خودکار صبر می‌کند
    proxy: Optional[str] = None                      # socks5://host:port

    # ── ضد‌بن: گارد عضویت ──
    join_min_interval_sec: int = 90                  # حداقل فاصله بین دو عضویت
    join_max_interval_sec: int = 240                 # حداکثر (با jitter تصادفی)
    join_daily_cap: int = 20                         # سقف عضویت روزانه
    join_on_request: bool = False                     # چنل‌های join_request را رد کن

    # ── ضد‌بن: گارد جستجو ──
    search_min_interval_sec: float = 2.5
    search_daily_cap: int = 1500

    @classmethod
    def from_env(cls) -> "TelegramSettings":
        return cls(
            api_id=_env_int("TG_API_ID", 0),
            api_hash=_env("TG_API_HASH"),
            phone=_env("TG_PHONE"),
            session_name=_env("TG_SESSION_NAME", "trend_telegram"),
            session_dir=_env("TG_SESSION_DIR", "."),
            password=_env("TG_2FA_PASSWORD"),
            proxy=_env("TG_PROXY") or None,
            join_min_interval_sec=_env_int("TG_JOIN_MIN_INTERVAL", 90),
            join_max_interval_sec=_env_int("TG_JOIN_MAX_INTERVAL", 240),
            join_daily_cap=_env_int("TG_JOIN_DAILY_CAP", 20),
            search_min_interval_sec=_env_float("TG_SEARCH_MIN_INTERVAL", 2.5),
            search_daily_cap=_env_int("TG_SEARCH_DAILY_CAP", 1500),
            flood_sleep_threshold=_env_int("TG_FLOOD_SLEEP_THRESHOLD", 60),
        )

    @property
    def configured(self) -> bool:
        """آیا api_id و api_hash ست شده‌اند؟"""
        return bool(self.api_id and self.api_hash)

    @property
    def session_path(self) -> str:
        """مسیر کامل فایل session (بدون پسوند)"""
        return os.path.join(self.session_dir, self.session_name)


# ─────────────────────────────────────────────────────────────
# وزن‌های تحلیل ترند
# ─────────────────────────────────────────────────────────────
@dataclass
class ScoringWeights:
    """
    وزن متریک‌های ترند. جمع وزن‌ها باید ۱ باشد (خودکار نرمال می‌شود).

    هر متریک در `analyzer.py` پیاده شده و مقدار ۰..۱ برمی‌گرداند.
    """

    velocity: float = 0.30       # سرعت انتشار (پست در ساعت)
    reach: float = 0.22          # بازدید/اندازه‌ی مخاطب
    engagement: float = 0.20     # نرخ تعامل (ری‌اکشن + فوروارد + کامنت)
    spread: float = 0.15         # تنوع کانال‌ها (چند منبع مستقل)
    freshness: float = 0.13      # تازگی (هرچه جدیدتر، بهتر)

    def normalized(self) -> dict:
        total = sum(abs(v) for v in (self.velocity, self.reach, self.engagement,
                                     self.spread, self.freshness)) or 1.0
        return {
            "velocity": self.velocity / total,
            "reach": self.reach / total,
            "engagement": self.engagement / total,
            "spread": self.spread / total,
            "freshness": self.freshness / total,
        }

    @classmethod
    def from_env(cls) -> "ScoringWeights":
        return cls(
            velocity=_env_float("TREND_W_VELOCITY", 0.30),
            reach=_env_float("TREND_W_REACH", 0.22),
            engagement=_env_float("TREND_W_ENGAGEMENT", 0.20),
            spread=_env_float("TREND_W_SPREAD", 0.15),
            freshness=_env_float("TREND_W_FRESHNESS", 0.13),
        )


# ─────────────────────────────────────────────────────────────
# تنظیمات کل موتور
# ─────────────────────────────────────────────────────────────
@dataclass
class TrendSettings:
    """تنظیمات کلی موتور ترند‌یابی"""

    enabled_providers: List[str] = field(default_factory=lambda: ["telegram"])
    default_window_hours: int = 72
    default_limit_per_tag: int = 50
    max_channels_per_scan: int = 20
    min_channel_members: int = 500
    db_path: str = "trendradar.db"
    exclude_channels: List[str] = field(default_factory=list)   # uid یا username
    log_level: str = "INFO"

    telegram: TelegramSettings = field(default_factory=TelegramSettings)
    weights: ScoringWeights = field(default_factory=ScoringWeights)

    @classmethod
    def from_env(cls) -> "TrendSettings":
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return cls(
            enabled_providers=_env_list("TREND_PROVIDERS", "telegram"),
            default_window_hours=_env_int("TREND_WINDOW_HOURS", 72),
            default_limit_per_tag=_env_int("TREND_LIMIT_PER_TAG", 50),
            max_channels_per_scan=_env_int("TREND_MAX_CHANNELS", 20),
            min_channel_members=_env_int("TREND_MIN_MEMBERS", 500),
            db_path=_env("TREND_DB_PATH", os.path.join(base_dir, "trendradar.db")),
            exclude_channels=_env_list("TREND_EXCLUDE_CHANNELS"),
            log_level=_env("TREND_LOG_LEVEL", "INFO"),
            telegram=TelegramSettings.from_env(),
            weights=ScoringWeights.from_env(),
        )


def load_settings() -> TrendSettings:
    """نقطه‌ی ورود واحد برای خواندن تنظیمات"""
    return TrendSettings.from_env()
