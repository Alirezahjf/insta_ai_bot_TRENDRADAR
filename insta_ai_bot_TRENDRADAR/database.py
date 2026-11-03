"""دیتابیس SQLite برای اسکژول‌ها + سطل آشغال + لاگ اجراها"""
import json
import sqlite3
import threading
import hashlib
import unicodedata
from datetime import datetime, timedelta

_lock = threading.Lock()
_db_path = None


def topic_key(topic):
    normalized = " ".join(unicodedata.normalize("NFKC", topic).casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def init(path):
    global _db_path
    _db_path = path
    with _lock, sqlite3.connect(path) as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS schedules (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          chat_id INTEGER NOT NULL,
          times TEXT NOT NULL DEFAULT '[]',
          hashtags TEXT NOT NULL DEFAULT '[]',
          count_per_hashtag INTEGER NOT NULL DEFAULT 20,
          content_type TEXT NOT NULL DEFAULT 'both',
          want_comments INTEGER NOT NULL DEFAULT 0,
          top_n INTEGER NOT NULL DEFAULT 5,
          comments_per INTEGER NOT NULL DEFAULT 10,
          enabled INTEGER NOT NULL DEFAULT 1,
          trashed INTEGER NOT NULL DEFAULT 0,
          last_triggered TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL DEFAULT ''
        )""")
        schedule_columns = {row[1] for row in con.execute("PRAGMA table_info(schedules)")}
        if "topic" not in schedule_columns:
            con.execute("ALTER TABLE schedules ADD COLUMN topic TEXT NOT NULL DEFAULT ''")
        if "telegram_auto_join" not in schedule_columns:
            con.execute("ALTER TABLE schedules ADD COLUMN telegram_auto_join INTEGER NOT NULL DEFAULT 0")
        con.execute("""CREATE TABLE IF NOT EXISTS topic_keyword_history (
            chat_id INTEGER NOT NULL, topic_key TEXT NOT NULL, term TEXT NOT NULL,
            created_at TEXT NOT NULL, UNIQUE(chat_id, topic_key, term))""")
        keyword_columns = {row[1] for row in con.execute("PRAGMA table_info(topic_keyword_history)")}
        for column, ddl in (
            ("normalized_term", "TEXT NOT NULL DEFAULT ''"),
            ("status", "TEXT NOT NULL DEFAULT 'legacy'"),
            ("canonical", "INTEGER NOT NULL DEFAULT 1"),
            ("updated_at", "TEXT NOT NULL DEFAULT ''"),
        ):
            if column not in keyword_columns:
                con.execute(f"ALTER TABLE topic_keyword_history ADD COLUMN {column} {ddl}")
        from search_identity import normalize_search_keyword
        rows = con.execute("SELECT rowid, chat_id, topic_key, term FROM topic_keyword_history ORDER BY rowid").fetchall()
        seen_normalized = set()
        for rowid, chat_id, topic_key_value, term in rows:
            normalized = normalize_search_keyword(term)
            scope = (chat_id, topic_key_value, normalized)
            canonical = 1 if normalized and scope not in seen_normalized else 0
            if normalized:
                seen_normalized.add(scope)
            con.execute("UPDATE topic_keyword_history SET normalized_term=?, canonical=? WHERE rowid=?",
                        (normalized, canonical, rowid))
        con.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_topic_keyword_normalized
            ON topic_keyword_history(chat_id, topic_key, normalized_term)
            WHERE canonical=1 AND normalized_term<>''""")
        con.execute("""CREATE TABLE IF NOT EXISTS keyword_platform_attempts (
            chat_id INTEGER NOT NULL, topic_key TEXT NOT NULL, normalized_term TEXT NOT NULL,
            platform TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'reserved',
            attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '',
            run_id TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
            PRIMARY KEY(chat_id, topic_key, normalized_term, platform))""")
        con.execute("""CREATE TABLE IF NOT EXISTS topic_post_history (
            chat_id INTEGER NOT NULL, topic_key TEXT NOT NULL, alias_key TEXT NOT NULL,
            post_key TEXT NOT NULL, platform TEXT NOT NULL, url TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL, relevance INTEGER, analysis_json TEXT NOT NULL DEFAULT '{}',
            attempts INTEGER NOT NULL DEFAULT 0, run_id TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(chat_id, topic_key, alias_key))""")
        con.execute("""CREATE INDEX IF NOT EXISTS idx_topic_post_key
            ON topic_post_history(chat_id, topic_key, post_key)""")
        con.execute("""
        CREATE TABLE IF NOT EXISTS job_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          schedule_id INTEGER,
          triggered_at TEXT DEFAULT '',
          status TEXT DEFAULT '',
          items_count INTEGER DEFAULT 0,
          winner_url TEXT DEFAULT '',
          error TEXT DEFAULT '',
          search_terms TEXT NOT NULL DEFAULT '[]'
        )""")
        columns = {row[1] for row in con.execute("PRAGMA table_info(job_runs)")}
        if "search_terms" not in columns:
            con.execute("ALTER TABLE job_runs ADD COLUMN search_terms TEXT NOT NULL DEFAULT '[]'")
        con.execute("""
        CREATE TABLE IF NOT EXISTS schedule_keyword_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          schedule_id INTEGER NOT NULL,
          term TEXT NOT NULL,
          created_at TEXT NOT NULL,
          UNIQUE(schedule_id, term)
        )""")
        # Preserve no-repeat behavior when upgrading pre-topic schedules.
        from ai_service import normalize_search_keyword
        schedules = con.execute("SELECT id, chat_id, topic, hashtags FROM schedules").fetchall()
        for sid, chat, topic, raw_tags in schedules:
            try:
                tags = json.loads(raw_tags or "[]")
                topic = (topic or "").strip() or "، ".join(tags)
            except (ValueError, TypeError):
                continue
            if not topic:
                continue
            old_terms = [r[0] for r in con.execute("SELECT term FROM schedule_keyword_history WHERE schedule_id=?", (sid,))]
            for (raw,) in con.execute("SELECT search_terms FROM job_runs WHERE schedule_id=?", (sid,)):
                try:
                    terms = json.loads(raw or "[]")
                    if isinstance(terms, list):
                        old_terms.extend(terms)
                except (ValueError, TypeError):
                    pass
            now = datetime.now().isoformat(timespec="seconds")
            for old in old_terms:
                term = normalize_search_keyword(old)
                if term:
                    con.execute("INSERT OR IGNORE INTO topic_keyword_history (chat_id, topic_key, term, created_at) VALUES (?,?,?,?)", (chat, topic_key(topic), term, now))



def _row_to_dict(row):
    keys = ["id", "chat_id", "times", "hashtags", "count_per_hashtag", "content_type",
            "want_comments", "top_n", "comments_per", "enabled", "trashed",
            "last_triggered", "created_at", "updated_at", "topic", "telegram_auto_join"]
    d = dict(zip(keys, row))
    try:
        d["times"] = json.loads(d["times"] or "[]")
    except Exception:
        d["times"] = []
    try:
        d["hashtags"] = json.loads(d["hashtags"] or "[]")
    except Exception:
        d["hashtags"] = []
    return d


def create_schedule(data: dict) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with _lock, sqlite3.connect(_db_path, timeout=30) as con:
        cur = con.execute("""
        INSERT INTO schedules (chat_id, times, hashtags, count_per_hashtag, content_type,
          want_comments, top_n, comments_per, enabled, trashed, last_triggered, created_at, updated_at, topic, telegram_auto_join)
        VALUES (?,?,?,?,?,?,?,?,?,0,'',?,?,?,?)""", (
            data["chat_id"], json.dumps(data.get("times", []), ensure_ascii=False),
            json.dumps(data.get("hashtags", []), ensure_ascii=False),
            data.get("count_per_hashtag", 20), data.get("content_type", "both"),
            1 if data.get("want_comments") else 0,
            data.get("top_n", 5), data.get("comments_per", 10),
            1 if data.get("enabled", True) else 0, now, now, data.get("topic", ""),
            1 if data.get("telegram_auto_join") else 0))
        return cur.lastrowid


def list_schedules(chat_id=None, trashed=False):
    with _lock, sqlite3.connect(_db_path, timeout=30) as con:
        if chat_id is None:
            rows = con.execute("SELECT * FROM schedules WHERE trashed=? ORDER BY id DESC",
                               (1 if trashed else 0,)).fetchall()
        else:
            rows = con.execute("SELECT * FROM schedules WHERE chat_id=? AND trashed=? ORDER BY id DESC",
                               (chat_id, 1 if trashed else 0,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_schedule(sid: int):
    with _lock, sqlite3.connect(_db_path, timeout=30) as con:
        row = con.execute("SELECT * FROM schedules WHERE id=?", (sid,)).fetchone()
    return _row_to_dict(row) if row else None


def update_schedule(sid: int, data: dict):
    fields, vals = [], []
    for k in ("times", "hashtags", "count_per_hashtag", "content_type",
              "want_comments", "top_n", "comments_per", "enabled", "topic", "telegram_auto_join"):
        if k in data:
            v = data[k]
            if k in ("times", "hashtags"):
                v = json.dumps(v, ensure_ascii=False)
            if k in ("want_comments", "enabled", "telegram_auto_join"):
                v = 1 if v else 0
            fields.append(f"{k}=?")
            vals.append(v)
    if not fields:
        return
    fields.append("updated_at=?")
    vals.append(datetime.now().isoformat(timespec="seconds"))
    vals.append(sid)
    with _lock, sqlite3.connect(_db_path, timeout=30) as con:
        con.execute(f"UPDATE schedules SET {', '.join(fields)} WHERE id=?", vals)


def set_enabled(sid: int, enabled: bool):
    update_schedule(sid, {"enabled": enabled})


def trash_schedule(sid: int):
    with _lock, sqlite3.connect(_db_path, timeout=30) as con:
        con.execute("UPDATE schedules SET trashed=1 WHERE id=?", (sid,))


def restore_schedule(sid: int):
    with _lock, sqlite3.connect(_db_path, timeout=30) as con:
        con.execute("UPDATE schedules SET trashed=0 WHERE id=?", (sid,))


def purge_schedule(sid: int):
    with _lock, sqlite3.connect(_db_path, timeout=30) as con:
        con.execute("DELETE FROM schedule_keyword_history WHERE schedule_id=?", (sid,))
        con.execute("DELETE FROM schedules WHERE id=?", (sid,))


def purge_all_trash(chat_id):
    with _lock, sqlite3.connect(_db_path, timeout=30) as con:
        con.execute("""DELETE FROM schedule_keyword_history
            WHERE schedule_id IN (
              SELECT id FROM schedules WHERE chat_id=? AND trashed=1
            )""", (chat_id,))
        con.execute("DELETE FROM schedules WHERE chat_id=? AND trashed=1", (chat_id,))


def get_schedule_keyword_history(sid: int):
    """تمام عبارت‌های تولیدشده‌ی قبلی یک اسکژول را برای جلوگیری از تکرار برمی‌گرداند."""
    with _lock, sqlite3.connect(_db_path, timeout=30) as con:
        rows = con.execute(
            "SELECT term FROM schedule_keyword_history WHERE schedule_id=? ORDER BY id",
            (sid,),
        ).fetchall()
    return [row[0] for row in rows]


def record_schedule_keywords(sid: int, terms):
    """کلیدواژه‌های جدید را اتمیک ثبت می‌کند و فقط مواردی را برمی‌گرداند که تازه بودند."""
    now = datetime.now().isoformat(timespec="seconds")
    inserted = []
    with _lock, sqlite3.connect(_db_path, timeout=30) as con:
        for term in terms or []:
            if not isinstance(term, str) or not term.strip():
                continue
            cur = con.execute(
                "INSERT OR IGNORE INTO schedule_keyword_history (schedule_id, term, created_at) VALUES (?,?,?)",
                (sid, term.strip(), now),
            )
            if cur.rowcount:
                inserted.append(term.strip())
    return inserted


def mark_triggered(sid: int, key: str):
    with _lock, sqlite3.connect(_db_path, timeout=30) as con:
        con.execute("UPDATE schedules SET last_triggered=? WHERE id=?", (key, sid))


def log_run(schedule_id, status, items_count=0, winner_url="", error="", search_terms=None):
    with _lock, sqlite3.connect(_db_path, timeout=30) as con:
        con.execute("""INSERT INTO job_runs
            (schedule_id, triggered_at, status, items_count, winner_url, error, search_terms)
            VALUES (?,?,?,?,?,?,?)""", (
            schedule_id, datetime.now().isoformat(timespec="seconds"),
            status, items_count, winner_url, error[:500],
            json.dumps(search_terms or [], ensure_ascii=False)))


def topic_history(chat_id, topic_key):
    with _lock, sqlite3.connect(_db_path, timeout=30) as con:
        return [r[0] for r in con.execute(
            "SELECT COALESCE(NULLIF(normalized_term,''),term) FROM topic_keyword_history WHERE chat_id=? AND topic_key=? ORDER BY rowid",
            (chat_id, topic_key))]


def reserve_topic_keywords(chat_id, topic_key, terms, platforms=()):
    """Atomically reserve normalized terms; optional per-platform attempt rows."""
    from search_identity import normalize_search_keyword
    now = datetime.now().isoformat(timespec="seconds")
    accepted, seen = [], set()
    with _lock, sqlite3.connect(_db_path, timeout=30) as con:
        con.execute("BEGIN IMMEDIATE")
        for raw in terms or []:
            term = normalize_search_keyword(raw)
            if not term or term in seen:
                continue
            seen.add(term)
            initial_status = "reserved" if platforms else "legacy"
            cursor = con.execute(
                """INSERT OR IGNORE INTO topic_keyword_history
                   (chat_id,topic_key,term,created_at,normalized_term,status,canonical,updated_at)
                   VALUES (?,?,?,?,?,?,1,?)""",
                (chat_id, topic_key, term, now, term, initial_status, now))
            if cursor.rowcount:
                accepted.append(term)
                for platform in platforms:
                    con.execute(
                        """INSERT OR IGNORE INTO keyword_platform_attempts
                           (chat_id,topic_key,normalized_term,platform,status,attempts,updated_at)
                           VALUES (?,?,?,?,'reserved',0,?)""",
                        (chat_id, topic_key, term, platform, now))
    return accepted


def retryable_topic_terms(chat_id, topic_key, platforms, max_attempts=3):
    placeholders = ",".join("?" for _ in platforms)
    stale_cutoff = (datetime.now() - timedelta(hours=6)).isoformat(timespec="seconds")
    with _lock, sqlite3.connect(_db_path, timeout=30) as con:
        rows = con.execute(
            f"""SELECT DISTINCT normalized_term FROM keyword_platform_attempts
                WHERE chat_id=? AND topic_key=? AND platform IN ({placeholders})
                  AND attempts<? AND (status='failed' OR (status='reserved' AND updated_at<?))
                ORDER BY normalized_term""",
            (chat_id, topic_key, *platforms, max_attempts, stale_cutoff)).fetchall()
    return [r[0] for r in rows]


def topic_terms_for_platform(chat_id, topic_key, platform, terms, max_attempts=3):
    """Only search terms not already successful on this platform."""
    from search_identity import normalize_search_keyword
    out = []
    with _lock, sqlite3.connect(_db_path, timeout=30) as con:
        for raw in terms or []:
            term = normalize_search_keyword(raw)
            if not term:
                continue
            row = con.execute(
                """SELECT status, attempts FROM keyword_platform_attempts
                   WHERE chat_id=? AND topic_key=? AND normalized_term=? AND platform=?""",
                (chat_id, topic_key, term, platform)).fetchone()
            if row is None or (row[0] != "succeeded" and row[1] < max_attempts):
                out.append(term)
    return list(dict.fromkeys(out))


def record_keyword_platform_result(chat_id, topic_key, terms, platform, status, error="", run_id=""):
    from search_identity import normalize_search_keyword
    if status not in ("succeeded", "failed"):
        return
    now = datetime.now().isoformat(timespec="seconds")
    with _lock, sqlite3.connect(_db_path, timeout=30) as con:
        con.execute("BEGIN IMMEDIATE")
        for raw in terms or []:
            term = normalize_search_keyword(raw)
            if not term:
                continue
            con.execute(
                """INSERT INTO keyword_platform_attempts
                   (chat_id,topic_key,normalized_term,platform,status,attempts,last_error,run_id,updated_at)
                   VALUES (?,?,?,?,?,1,?,?,?)
                   ON CONFLICT(chat_id,topic_key,normalized_term,platform) DO UPDATE SET
                     status=excluded.status, attempts=keyword_platform_attempts.attempts+1,
                     last_error=excluded.last_error, run_id=excluded.run_id, updated_at=excluded.updated_at""",
                (chat_id, topic_key, term, platform, status, str(error)[:200], run_id, now))


def get_schedule_used_terms(sid):
    with _lock, sqlite3.connect(_db_path, timeout=30) as con:
        rows = con.execute("SELECT search_terms FROM job_runs WHERE schedule_id=?", (sid,)).fetchall()
    terms = []
    for (raw,) in rows:
        try:
            terms.extend(json.loads(raw or "[]"))
        except (ValueError, TypeError):
            pass
    return terms


def reserve_post_records(chat_id, topic_key_value, records, run_id, *,
                         stale_seconds=7200, max_failed=3, max_unknown=2):
    """Claim only posts whose processing state permits a new AI attempt.

    Completed true/false classifications are never re-analyzed. Failed and
    unknown items have bounded retries; stale processing claims are recoverable
    after a restart. Alias rows let a permalink and a source/message identity
    resolve to the same canonical post.
    """
    from search_identity import record_identity
    import json
    import time
    now = datetime.now().isoformat(timespec="seconds")
    accepted, skipped = [], []
    with _lock, sqlite3.connect(_db_path, timeout=30) as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN IMMEDIATE")
        for record in records:
            identity = record_identity(record)
            aliases = identity["aliases"] if identity else ([record["post_key"]] if record.get("post_key") else [])
            if not aliases:
                accepted.append({**record, "history_claimed": False})
                continue
            placeholders = ",".join("?" for _ in aliases)
            existing = con.execute(
                f"SELECT * FROM topic_post_history WHERE chat_id=? AND topic_key=? AND alias_key IN ({placeholders})",
                (chat_id, topic_key_value, *aliases)).fetchall()
            post_key = existing[0]["post_key"] if existing else identity["key"]
            rows = con.execute(
                "SELECT * FROM topic_post_history WHERE chat_id=? AND topic_key=? AND post_key=?",
                (chat_id, topic_key_value, post_key)).fetchall() if existing else []
            status = rows[0]["status"] if rows else ""
            attempts = max((r["attempts"] for r in rows), default=0)
            previous = json.loads(rows[0]["analysis_json"] or "{}") if rows else {}
            updated = rows[0]["updated_at"] if rows else ""
            stale = False
            if status == "processing" and updated:
                try:
                    age = (datetime.now() - datetime.fromisoformat(updated)).total_seconds()
                    stale = age > stale_seconds
                except ValueError:
                    stale = True
            blocked = (status in ("completed", "exhausted_failed", "exhausted_unknown")
                       or (status == "processing" and not stale)
                       or (status == "failed" and attempts >= max_failed)
                       or (status == "unknown" and attempts >= max_unknown))
            if blocked:
                final_status = status
                if status == "failed" and attempts >= max_failed:
                    final_status = "exhausted_failed"
                elif status == "unknown" and attempts >= max_unknown:
                    final_status = "exhausted_unknown"
                if final_status != status:
                    con.execute("UPDATE topic_post_history SET status=?, updated_at=? WHERE chat_id=? AND topic_key=? AND post_key=?",
                                (final_status, now, chat_id, topic_key_value, post_key))
                skipped.append({"id": record.get("id"), "platform": record.get("platform"),
                                "url": record.get("url", ""), "post_key": post_key,
                                "status": final_status, "previous_relevance": rows[0]["relevance"] if rows else None,
                                "previous_analysis": previous})
                continue
            next_attempt = attempts + 1
            url = identity["url"] if identity else record.get("url", "")
            platform = record.get("platform", "")
            for alias in aliases:
                con.execute(
                    """INSERT INTO topic_post_history
                       (chat_id,topic_key,alias_key,post_key,platform,url,status,attempts,run_id,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,'processing',?,?,?,?)
                       ON CONFLICT(chat_id,topic_key,alias_key) DO UPDATE SET
                         status='processing', attempts=excluded.attempts, run_id=excluded.run_id,
                         url=excluded.url, updated_at=excluded.updated_at""",
                    (chat_id, topic_key_value, alias, post_key, platform, url, next_attempt, run_id, now, now))
            accepted.append({**record, "history_claimed": True, "history_post_key": post_key,
                             "history_attempts": next_attempt})
    return accepted, skipped


def complete_post_records(chat_id, topic_key_value, records, analysis, run_id):
    """Persist the real AI outcome; missing entries remain retryable failures."""
    import json
    now = datetime.now().isoformat(timespec="seconds")
    with _lock, sqlite3.connect(_db_path, timeout=30) as con:
        con.execute("BEGIN IMMEDIATE")
        for record in records:
            if not record.get("history_claimed"):
                continue
            post_key = record.get("history_post_key") or record.get("post_key")
            if not post_key:
                continue
            uid = record["id"]
            if uid not in analysis.get("relevance", {}):
                status, flag = "failed", None
            else:
                flag = analysis["relevance"][uid]
                status = "unknown" if flag is None else "completed"
            payload = {"analysis": analysis.get("analyses", {}).get(uid, ""),
                       "details": analysis.get("analysis_details", {}).get(uid, {})}
            relevance = None if flag is None else (1 if flag else 0)
            con.execute(
                """UPDATE topic_post_history SET status=?, relevance=?, analysis_json=?,
                   run_id=?, updated_at=? WHERE chat_id=? AND topic_key=? AND post_key=?""",
                (status, relevance, json.dumps(payload, ensure_ascii=False), run_id, now,
                 chat_id, topic_key_value, post_key))
