import os, sqlite3, tempfile, threading, unittest
from concurrent.futures import ThreadPoolExecutor
import database
from search_identity import normalize_search_keyword, post_identity, dedupe_records

class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "db.sqlite")
        database.init(self.path)
        self.key = database.topic_key("python")
    def tearDown(self): self.tmp.cleanup()
    def test_keyword_normalization_and_atomic_reservation(self):
        self.assertEqual(normalize_search_keyword("#کتابِ مردانه"), normalize_search_keyword("كتاب_مردانه"))
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: database.reserve_topic_keywords(1, self.key, ["Python_News"], platforms=("instagram",)), range(4)))
        self.assertEqual(sum(len(r) for r in results), 1)
    def test_platform_failure_retries_only_failed_platform(self):
        database.reserve_topic_keywords(1, self.key, ["python_news"], platforms=("instagram", "telegram"))
        database.record_keyword_platform_result(1, self.key, ["python_news"], "instagram", "succeeded")
        database.record_keyword_platform_result(1, self.key, ["python_news"], "telegram", "failed", "timeout")
        self.assertEqual(database.retryable_topic_terms(1, self.key, ("instagram", "telegram")), ["python_news"])
        self.assertEqual(database.topic_terms_for_platform(1, self.key, "instagram", ["python_news"]), [])
        self.assertEqual(database.topic_terms_for_platform(1, self.key, "telegram", ["python_news"]), ["python_news"])
    def test_post_identity_equivalents_and_distinct(self):
        a = post_identity("Instagram", url="https://www.instagram.com/p/AbC123/?utm_source=x")
        b = post_identity("Instagram", short_code="AbC123")
        c = post_identity("Instagram", url="https://www.instagram.com/p/abc123/")
        self.assertEqual(a["key"], b["key"])
        self.assertNotEqual(a["key"], c["key"])
        tg = post_identity("Telegram", channel_uid="telegram:42", message_id=8)
        self.assertEqual(tg["url"], "")
        self.assertIn("42:8", tg["key"])
    def test_run_dedupe_and_history_retry(self):
        records = [
            {"id": "1", "platform": "Instagram", "url": "https://instagram.com/p/AbC/?igsh=x", "raw": {"shortCode": "AbC"}},
            {"id": "2", "platform": "Instagram", "url": "https://www.instagram.com/reel/AbC/", "raw": {}},
            {"id": "3", "platform": "Telegram", "url": "", "channel_uid": "telegram:42", "message_id": 8, "raw": {}},
        ]
        unique, duplicates = dedupe_records(records)
        self.assertEqual(duplicates, 1)
        self.assertEqual(len(unique), 2)
        claimed, skipped = database.reserve_post_records(1, self.key, unique, "run1")
        self.assertEqual(len(claimed), 2)
        database.complete_post_records(1, self.key, claimed, {"relevance": {"1": True, "3": None}, "analyses": {"1": "ok", "3": "unknown"}}, "run1")
        claimed2, skipped2 = database.reserve_post_records(1, self.key, unique, "run2")
        self.assertEqual(len(skipped2), 1)  # completed Instagram skipped
        self.assertEqual(len(claimed2), 1)  # unknown Telegram retried
        database.complete_post_records(1, self.key, claimed2, {"relevance": {}, "analyses": {}}, "run2")
        claimed3, _ = database.reserve_post_records(1, self.key, unique, "run3")
        self.assertEqual(len(claimed3), 1)  # failed AI retried, not silently completed
    def test_concurrent_post_claim_is_single(self):
        record = {"id": "1", "platform": "Instagram", "url": "https://instagram.com/p/AbC/", "raw": {}}
        unique, _ = dedupe_records([record])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda i: database.reserve_post_records(1, self.key, unique, f"run{i}")[0], range(2)))
        self.assertEqual(sum(len(r) for r in results), 1)
    def test_old_and_fresh_database_migration_preserves_data(self):
        old = os.path.join(self.tmp.name, "old.sqlite")
        with sqlite3.connect(old) as con:
            con.execute("CREATE TABLE schedules (id INTEGER PRIMARY KEY, chat_id INTEGER, times TEXT, hashtags TEXT, count_per_hashtag INTEGER, content_type TEXT, want_comments INTEGER, top_n INTEGER, comments_per INTEGER, enabled INTEGER, trashed INTEGER, last_triggered TEXT, created_at TEXT, updated_at TEXT)")
            con.execute("INSERT INTO schedules (id,chat_id,hashtags) VALUES (1,1,'[\"python\"]')")
            con.execute("CREATE TABLE topic_keyword_history (chat_id INTEGER, topic_key TEXT, term TEXT, created_at TEXT, UNIQUE(chat_id,topic_key,term))")
            con.execute("INSERT INTO topic_keyword_history VALUES (1,?, '#Python_News','old')", (self.key,))
        database.init(old)
        self.assertIn("python_news", database.topic_history(1, self.key))
        with sqlite3.connect(old) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM topic_keyword_history").fetchone()[0], 1)
            self.assertIn("topic_post_history", [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")])
        database.init(self.path)  # fresh re-init is idempotent

    def test_restart_recovers_stale_processing_claim(self):
        record = {"id": "1", "platform": "Instagram", "url": "https://instagram.com/p/AbC/", "raw": {}}
        unique, _ = dedupe_records([record])
        claimed, _ = database.reserve_post_records(1, self.key, unique, "run1")
        self.assertEqual(len(claimed), 1)
        database.init(self.path)  # simulate restart without deleting history
        reclaimed, skipped = database.reserve_post_records(1, self.key, unique, "run2", stale_seconds=0)
        self.assertEqual(len(reclaimed), 1)
        self.assertEqual(skipped, [])
