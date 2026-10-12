"""
💾 لایه‌ی ذخیره‌سازی (SQLite)
============================

چرا ذخیره‌سازی لازم است؟
------------------------
ترند یعنی **تغییر در زمان**. بدون تاریخچه نمی‌توان فهمید یک هشتگ در حال
اوج گرفتن است یا افت کردن. جداول:

    channels        → کانال‌های کشف‌شده + وضعیت عضویت
    posts           → پست‌های جمع‌آوری‌شده (کلید یکتا: چنل + شناسه‌ی پیام)
    hashtag_series  → سری زمانی شمارش هشتگ (برای محاسبه‌ی سرعت واقعی)
    runs            → تاریخچه‌ی اجراها
    provider_state  → وضعیت گاردها و session بین اجراها

طراحی: بدون ORM، تک‌فایل، thread-safe با قفل و اتصال کوتاه‌عمر.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .models import ChannelRef, HashtagStat, Post, RunReport

log = logging.getLogger("trendradar.storage")

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS channels (
    uid             TEXT PRIMARY KEY,
    provider        TEXT NOT NULL,
    native_id       TEXT NOT NULL,
    title           TEXT,
    username        TEXT,
    kind            TEXT,
    members         INTEGER,
    about           TEXT,
    verified        INTEGER DEFAULT 0,
    is_scam         INTEGER DEFAULT 0,
    is_fake         INTEGER DEFAULT 0,
    is_restricted   INTEGER DEFAULT 0,
    join_request    INTEGER DEFAULT 0,
    joined          INTEGER DEFAULT 0,
    access_hash     INTEGER,
    discovered_at   TEXT,
    last_seen_at    TEXT,
    last_scored     REAL,
    extra           TEXT
);
CREATE INDEX IF NOT EXISTS idx_channels_provider ON channels(provider);
CREATE INDEX IF NOT EXISTS idx_channels_joined   ON channels(joined);

CREATE TABLE IF NOT EXISTS posts (
    uid             TEXT PRIMARY KEY,
    provider        TEXT NOT NULL,
    channel_uid     TEXT NOT NULL,
    message_id      TEXT NOT NULL,
    date            TEXT NOT NULL,
    text            TEXT,
    hashtags        TEXT,
    views           INTEGER DEFAULT 0,
    forwards        INTEGER DEFAULT 0,
    replies          INTEGER DEFAULT 0,
    reactions       INTEGER DEFAULT 0,
    media_type      TEXT,
    url             TEXT,
    channel_title   TEXT,
    channel_members INTEGER,
    collected_at    TEXT,
    extra           TEXT
);
CREATE INDEX IF NOT EXISTS idx_posts_date    ON posts(date);
CREATE INDEX IF NOT EXISTS idx_posts_channel ON posts(channel_uid);
CREATE INDEX IF NOT EXISTS idx_posts_provider ON posts(provider);

CREATE TABLE IF NOT EXISTS hashtag_series (
    tag             TEXT NOT NULL,
    provider        TEXT NOT NULL,
    bucket_hour     TEXT NOT NULL,       -- ISO ساعت گرد‌شده
    posts           INTEGER DEFAULT 0,
    channels        INTEGER DEFAULT 0,
    views           INTEGER DEFAULT 0,
    engagement      INTEGER DEFAULT 0,
    PRIMARY KEY (tag, provider, bucket_hour)
);

CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    provider_set    TEXT,
    query           TEXT,
    started_at      TEXT,
    finished_at     TEXT,
    channels_scanned INTEGER DEFAULT 0,
    channels_discovered INTEGER DEFAULT 0,
    channels_joined INTEGER DEFAULT 0,
    posts_collected INTEGER DEFAULT 0,
    signals         TEXT,
    errors          TEXT
);

CREATE TABLE IF NOT EXISTS provider_state (
    provider        TEXT PRIMARY KEY,
    state           TEXT,
    updated_at      TEXT
);
"""


class TrendStorage:
    """ذخیره‌گاه ترند‌یابی — thread-safe"""

    def __init__(self, path: str = "trendradar.db"):
        self.path = path
        self._lock = threading.RLock()
        self._init()

    # ── زیرساخت ──────────────────────────────────────────
    @contextmanager
    def _conn(self):
        """اتصال کوتاه‌عمر — چون sqlite3 بین تردها قابل اشتراک نیست"""
        con = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _init(self) -> None:
        with self._lock, self._conn() as con:
            con.executescript(SCHEMA)
        log.debug("دیتابیس آماده شد: %s", self.path)

    # ═════════════════════════════════════════════════════
    # کانال‌ها
    # ═════════════════════════════════════════════════════
    def upsert_channel(self, ch: ChannelRef) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._conn() as con:
            con.execute(
                """
                INSERT INTO channels (uid, provider, native_id, title, username, kind, members,
                    about, verified, is_scam, is_fake, is_restricted, join_request, joined,
                    access_hash, discovered_at, last_seen_at, extra)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(uid) DO UPDATE SET
                    title=excluded.title, username=excluded.username, kind=excluded.kind,
                    members=excluded.members, about=excluded.about, verified=excluded.verified,
                    is_scam=excluded.is_scam, is_fake=excluded.is_fake,
                    is_restricted=excluded.is_restricted, join_request=excluded.join_request,
                    joined=excluded.joined, access_hash=excluded.access_hash,
                    last_seen_at=excluded.last_seen_at, extra=excluded.extra
                """,
                (
                    ch.uid, ch.provider, str(ch.native_id), ch.title, ch.username, ch.kind.value,
                    ch.members, ch.about[:1000] if ch.about else "", int(ch.verified),
                    int(ch.is_scam), int(ch.is_fake), int(ch.is_restricted), int(ch.join_request),
                    int(ch.joined), ch.access_hash,
                    ch.discovered_at.isoformat() if ch.discovered_at else now,
                    now, json.dumps(ch.extra or {}, ensure_ascii=False),
                ),
            )

    def upsert_channels(self, channels: Iterable[ChannelRef]) -> int:
        n = 0
        for c in channels:
            try:
                self.upsert_channel(c)
                n += 1
            except Exception as e:
                log.debug("ذخیره‌ی کانال %s ناموفق: %s", c.uid, e)
        return n

    def get_joined_channels(self, provider: Optional[str] = None, limit: int = 500) -> List[ChannelRef]:
        sql = "SELECT * FROM channels WHERE joined = 1"
        params: List[Any] = []
        if provider:
            sql += " AND provider = ?"
            params.append(provider)
        sql += " ORDER BY members DESC NULLS LAST LIMIT ?"
        params.append(limit)
        with self._lock, self._conn() as con:
            rows = con.execute(sql, params).fetchall()
        return [self._row_to_channel(r) for r in rows]

    def get_channels(self, provider: Optional[str] = None, limit: int = 500) -> List[ChannelRef]:
        sql = "SELECT * FROM channels"
        params: List[Any] = []
        if provider:
            sql += " WHERE provider = ?"
            params.append(provider)
        sql += " ORDER BY members DESC NULLS LAST LIMIT ?"
        params.append(limit)
        with self._lock, self._conn() as con:
            rows = con.execute(sql, params).fetchall()
        return [self._row_to_channel(r) for r in rows]

    @staticmethod
    def _row_to_channel(r: sqlite3.Row) -> ChannelRef:
        from .models import ChannelKind

        try:
            kind = ChannelKind(r["kind"])
        except (ValueError, TypeError):
            kind = ChannelKind.UNKNOWN
        extra = {}
        try:
            extra = json.loads(r["extra"] or "{}")
        except (json.JSONDecodeError, TypeError):
            pass
        return ChannelRef(
            provider=r["provider"],
            native_id=int(r["native_id"]) if str(r["native_id"]).lstrip("-").isdigit() else r["native_id"],
            title=r["title"] or "",
            username=r["username"],
            kind=kind,
            members=r["members"],
            about=r["about"] or "",
            verified=bool(r["verified"]),
            is_scam=bool(r["is_scam"]),
            is_fake=bool(r["is_fake"]),
            is_restricted=bool(r["is_restricted"]),
            join_request=bool(r["join_request"]),
            joined=bool(r["joined"]),
            access_hash=r["access_hash"],
            extra=extra,
        )

    # ═════════════════════════════════════════════════════
    # پست‌ها
    # ═════════════════════════════════════════════════════
    def save_posts(self, posts: Sequence[Post]) -> int:
        """ذخیره‌ی پست‌ها + به‌روزرسانی سری زمانی هشتگ‌ها"""
        if not posts:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (
                p.uid, p.provider, p.channel_uid, str(p.message_id),
                p.date.isoformat(), p.text, json.dumps(p.hashtags, ensure_ascii=False),
                p.views, p.forwards, p.replies, p.reactions, p.media_type.value,
                p.url, p.channel_title, p.channel_members, now,
                json.dumps(p.extra or {}, ensure_ascii=False, default=str),
            )
            for p in posts
        ]
        with self._lock, self._conn() as con:
            con.executemany(
                """
                INSERT INTO posts (uid, provider, channel_uid, message_id, date, text, hashtags,
                    views, forwards, replies, reactions, media_type, url, channel_title,
                    channel_members, collected_at, extra)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(uid) DO UPDATE SET
                    views=excluded.views, forwards=excluded.forwards, replies=excluded.replies,
                    reactions=excluded.reactions, text=excluded.text, hashtags=excluded.hashtags,
                    collected_at=excluded.collected_at
                """,
                rows,
            )
        self._update_series(posts)
        return len(rows)

    def _update_series(self, posts: Sequence[Post]) -> None:
        """به‌روزرسانی سری زمانی ساعتی برای محاسبه‌ی سرعت واقعی انتشار"""
        buckets: Dict[tuple, Dict[str, Any]] = {}
        for p in posts:
            hour = p.date.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
            key_base = (p.provider, hour.isoformat())
            for tag in p.hashtags:
                k = (tag, *key_base)
                b = buckets.setdefault(k, {"posts": 0, "channels": set(), "views": 0, "engagement": 0})
                b["posts"] += 1
                b["channels"].add(p.channel_uid)
                b["views"] += p.views
                b["engagement"] += p.engagement

        if not buckets:
            return
        with self._lock, self._conn() as con:
            for (tag, provider, hour), b in buckets.items():
                con.execute(
                    """
                    INSERT INTO hashtag_series (tag, provider, bucket_hour, posts, channels, views, engagement)
                    VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(tag, provider, bucket_hour) DO UPDATE SET
                        posts=excluded.posts,
                        channels=MAX(hashtag_series.channels, excluded.channels),
                        views=MAX(hashtag_series.views, excluded.views),
                        engagement=MAX(hashtag_series.engagement, excluded.engagement)
                    """,
                    (tag, provider, hour, b["posts"], len(b["channels"]), b["views"], b["engagement"]),
                )

    def posts_since(
        self,
        since: datetime,
        *,
        provider: Optional[str] = None,
        tags: Optional[Sequence[str]] = None,
        limit: int = 5000,
    ) -> List[Post]:
        sql = "SELECT * FROM posts WHERE date >= ?"
        params: List[Any] = [since.isoformat()]
        if provider:
            sql += " AND provider = ?"
            params.append(provider)
        if tags:
            sql += " AND (" + " OR ".join("hashtags LIKE ?" for _ in tags) + ")"
            params.extend(f'%"{t}"%' for t in tags)
        sql += " ORDER BY date DESC LIMIT ?"
        params.append(limit)

        with self._lock, self._conn() as con:
            rows = con.execute(sql, params).fetchall()
        return [self._row_to_post(r) for r in rows]

    @staticmethod
    def _row_to_post(r: sqlite3.Row) -> Post:
        from .models import MediaType

        try:
            tags = json.loads(r["hashtags"] or "[]")
        except (json.JSONDecodeError, TypeError):
            tags = []
        try:
            mt = MediaType(r["media_type"])
        except (ValueError, TypeError):
            mt = MediaType.UNKNOWN
        try:
            extra = json.loads(r["extra"] or "{}")
        except (json.JSONDecodeError, TypeError):
            extra = {}
        try:
            date = datetime.fromisoformat(r["date"])
        except (ValueError, TypeError):
            date = datetime.now(timezone.utc)
        return Post(
            provider=r["provider"],
            channel_uid=r["channel_uid"],
            message_id=r["message_id"],
            date=date,
            text=r["text"] or "",
            hashtags=tags,
            views=r["views"] or 0,
            forwards=r["forwards"] or 0,
            replies=r["replies"] or 0,
            reactions=r["reactions"] or 0,
            media_type=mt,
            url=r["url"] or "",
            channel_title=r["channel_title"] or "",
            channel_members=r["channel_members"],
            extra=extra,
        )

    # ═════════════════════════════════════════════════════
    # سری زمانی (برای محاسبه‌ی سرعت واقعی ترند)
    # ═════════════════════════════════════════════════════
    def hashtag_history(self, tag: str, *, hours: int = 168, provider: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        تاریخچه‌ی ساعتی یک هشتگ — برای تشخیص «در حال اوج گرفتن» از «اوج گذشته».

        :param hours: چند ساعت به عقب
        """
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        sql = "SELECT * FROM hashtag_series WHERE tag = ? AND bucket_hour >= ?"
        params: List[Any] = [tag, since]
        if provider:
            sql += " AND provider = ?"
            params.append(provider)
        sql += " ORDER BY bucket_hour ASC"
        with self._lock, self._conn() as con:
            rows = con.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def hashtag_velocity(self, tag: str, *, recent_hours: int = 12, baseline_hours: int = 72) -> float:
        """
        نسبت سرعت فعلی به سرعت پایه — معیار کلاسیک تشخیص ترند در حال ظهور.

        > ۱  → در حال اوج گرفتن 🔥
        ≈ ۱  → ثابت
        < ۱  → در حال افت

        :return: نسبت (اگر داده‌ی کافی نباشد، ۱.۰)
        """
        now = datetime.now(timezone.utc)
        recent_since = (now - timedelta(hours=recent_hours)).isoformat()
        base_since = (now - timedelta(hours=baseline_hours)).isoformat()

        with self._lock, self._conn() as con:
            recent = con.execute(
                "SELECT COALESCE(SUM(posts),0) AS n FROM hashtag_series WHERE tag=? AND bucket_hour>=?",
                (tag, recent_since),
            ).fetchone()["n"]
            base = con.execute(
                "SELECT COALESCE(SUM(posts),0) AS n FROM hashtag_series WHERE tag=? AND bucket_hour>=? AND bucket_hour<?",
                (tag, base_since, recent_since),
            ).fetchone()["n"]

        base_rate = (base or 0) / max(1.0, (baseline_hours - recent_hours))
        recent_rate = (recent or 0) / max(1.0, recent_hours)
        if base_rate <= 0:
            return 3.0 if recent_rate > 0 else 1.0      # هشتگ کاملاً جدید → سیگنال قوی
        return round(recent_rate / base_rate, 3)

    # ═════════════════════════════════════════════════════
    # اجراها
    # ═════════════════════════════════════════════════════
    def save_run(self, report: RunReport) -> None:
        with self._lock, self._conn() as con:
            con.execute(
                """
                INSERT INTO runs (run_id, provider_set, query, started_at, finished_at,
                    channels_scanned, channels_discovered, channels_joined, posts_collected,
                    signals, errors)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET
                    finished_at=excluded.finished_at, signals=excluded.signals, errors=excluded.errors
                """,
                (
                    report.run_id,
                    ",".join(sorted(report.provider_stats)),
                    json.dumps(report.query.to_dict(), ensure_ascii=False),
                    report.started_at.isoformat(),
                    report.finished_at.isoformat() if report.finished_at else None,
                    report.channels_scanned, report.channels_discovered, report.channels_joined,
                    report.posts_collected,
                    json.dumps([s.to_dict() for s in report.signals], ensure_ascii=False),
                    json.dumps(report.errors, ensure_ascii=False),
                ),
            )

    def last_run(self) -> Optional[Dict[str, Any]]:
        with self._lock, self._conn() as con:
            row = con.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def recent_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock, self._conn() as con:
            rows = con.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ═════════════════════════════════════════════════════
    # وضعیت پرووایدر (گاردها، سشن)
    # ═════════════════════════════════════════════════════
    def save_provider_state(self, provider: str, state: Dict[str, Any]) -> None:
        with self._lock, self._conn() as con:
            con.execute(
                """
                INSERT INTO provider_state (provider, state, updated_at) VALUES (?,?,?)
                ON CONFLICT(provider) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at
                """,
                (provider, json.dumps(state, ensure_ascii=False), datetime.now(timezone.utc).isoformat()),
            )

    def load_provider_state(self, provider: str) -> Dict[str, Any]:
        with self._lock, self._conn() as con:
            row = con.execute(
                "SELECT state FROM provider_state WHERE provider = ?", (provider,)
            ).fetchone()
        if not row:
            return {}
        try:
            return json.loads(row["state"] or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}

    # ── آمار کلی ─────────────────────────────────────────
    def stats(self) -> Dict[str, int]:
        with self._lock, self._conn() as con:
            def one(sql: str, *p) -> int:
                return int(con.execute(sql, p).fetchone()[0] or 0)

            return {
                "channels": one("SELECT COUNT(*) FROM channels"),
                "channels_joined": one("SELECT COUNT(*) FROM channels WHERE joined=1"),
                "posts": one("SELECT COUNT(*) FROM posts"),
                "tags": one("SELECT COUNT(DISTINCT tag) FROM hashtag_series"),
                "runs": one("SELECT COUNT(*) FROM runs"),
            }

    def prune(self, *, keep_days: int = 60) -> int:
        """حذف داده‌های قدیمی — جلوگیری از رشد بی‌نهایت دیتابیس"""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()
        with self._lock, self._conn() as con:
            n = con.execute("DELETE FROM posts WHERE date < ?", (cutoff,)).rowcount
            con.execute("DELETE FROM hashtag_series WHERE bucket_hour < ?", (cutoff,))
            con.execute("DELETE FROM runs WHERE started_at < ?", (cutoff,))
        log.info("پاک‌سازی: %d پست قدیمی حذف شد", n)
        return n
