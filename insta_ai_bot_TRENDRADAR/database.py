"""دیتابیس SQLite برای اسکژول‌ها + سطل آشغال + لاگ اجراها"""
import json
import sqlite3
import threading
from datetime import datetime

_lock = threading.Lock()
_db_path = None


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
        con.execute("""
        CREATE TABLE IF NOT EXISTS job_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          schedule_id INTEGER,
          triggered_at TEXT DEFAULT '',
          status TEXT DEFAULT '',
          items_count INTEGER DEFAULT 0,
          winner_url TEXT DEFAULT '',
          error TEXT DEFAULT ''
        )""")


def _row_to_dict(row):
    keys = ["id", "chat_id", "times", "hashtags", "count_per_hashtag", "content_type",
            "want_comments", "top_n", "comments_per", "enabled", "trashed",
            "last_triggered", "created_at", "updated_at"]
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
    with _lock, sqlite3.connect(_db_path) as con:
        cur = con.execute("""
        INSERT INTO schedules (chat_id, times, hashtags, count_per_hashtag, content_type,
          want_comments, top_n, comments_per, enabled, trashed, last_triggered, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,0,'',?,?)""", (
            data["chat_id"], json.dumps(data.get("times", []), ensure_ascii=False),
            json.dumps(data.get("hashtags", []), ensure_ascii=False),
            data.get("count_per_hashtag", 20), data.get("content_type", "both"),
            1 if data.get("want_comments") else 0,
            data.get("top_n", 5), data.get("comments_per", 10),
            1 if data.get("enabled", True) else 0, now, now))
        return cur.lastrowid


def list_schedules(chat_id=None, trashed=False):
    with _lock, sqlite3.connect(_db_path) as con:
        if chat_id is None:
            rows = con.execute("SELECT * FROM schedules WHERE trashed=? ORDER BY id DESC",
                               (1 if trashed else 0,)).fetchall()
        else:
            rows = con.execute("SELECT * FROM schedules WHERE chat_id=? AND trashed=? ORDER BY id DESC",
                               (chat_id, 1 if trashed else 0,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_schedule(sid: int):
    with _lock, sqlite3.connect(_db_path) as con:
        row = con.execute("SELECT * FROM schedules WHERE id=?", (sid,)).fetchone()
    return _row_to_dict(row) if row else None


def update_schedule(sid: int, data: dict):
    fields, vals = [], []
    for k in ("times", "hashtags", "count_per_hashtag", "content_type",
              "want_comments", "top_n", "comments_per", "enabled"):
        if k in data:
            v = data[k]
            if k in ("times", "hashtags"):
                v = json.dumps(v, ensure_ascii=False)
            if k in ("want_comments", "enabled"):
                v = 1 if v else 0
            fields.append(f"{k}=?")
            vals.append(v)
    if not fields:
        return
    fields.append("updated_at=?")
    vals.append(datetime.now().isoformat(timespec="seconds"))
    vals.append(sid)
    with _lock, sqlite3.connect(_db_path) as con:
        con.execute(f"UPDATE schedules SET {', '.join(fields)} WHERE id=?", vals)


def set_enabled(sid: int, enabled: bool):
    update_schedule(sid, {"enabled": enabled})


def trash_schedule(sid: int):
    with _lock, sqlite3.connect(_db_path) as con:
        con.execute("UPDATE schedules SET trashed=1 WHERE id=?", (sid,))


def restore_schedule(sid: int):
    with _lock, sqlite3.connect(_db_path) as con:
        con.execute("UPDATE schedules SET trashed=0 WHERE id=?", (sid,))


def purge_schedule(sid: int):
    with _lock, sqlite3.connect(_db_path) as con:
        con.execute("DELETE FROM schedules WHERE id=?", (sid,))


def purge_all_trash(chat_id):
    with _lock, sqlite3.connect(_db_path) as con:
        con.execute("DELETE FROM schedules WHERE chat_id=? AND trashed=1", (chat_id,))


def mark_triggered(sid: int, key: str):
    with _lock, sqlite3.connect(_db_path) as con:
        con.execute("UPDATE schedules SET last_triggered=? WHERE id=?", (key, sid))


def log_run(schedule_id, status, items_count=0, winner_url="", error=""):
    with _lock, sqlite3.connect(_db_path) as con:
        con.execute("""INSERT INTO job_runs
            (schedule_id, triggered_at, status, items_count, winner_url, error)
            VALUES (?,?,?,?,?,?)""", (
            schedule_id, datetime.now().isoformat(timespec="seconds"),
            status, items_count, winner_url, error[:500]))
