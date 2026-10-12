import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

import ai_service
import config
import database
import search_service
from trendradar.config import TrendSettings
from trendradar.models import Post, utcnow
from trendradar.providers.telegram.provider import TelegramProvider


class KeywordNormalizationTests(unittest.TestCase):
    def test_normalizes_and_deduplicates_supported_scripts(self):
        self.assertEqual(ai_service.normalize_search_keyword("#Men's Casual Shirt"), "mens_casual_shirt")
        self.assertEqual(ai_service.normalize_search_keyword("لباس کژوال مردانه"), "لباس_کژوال_مردانه")
        self.assertEqual(ai_service.normalize_search_keyword("  #TREND--STYLE "), "trend_style")
        self.assertEqual(ai_service.normalize_search_keyword("#كتاب"), "کتاب")
        self.assertEqual(
            ai_service.normalize_search_keyword("لباس‌مردانه"),
            ai_service.normalize_search_keyword("لباس مردانه"),
        )

    def test_parses_fenced_json_and_filters_seed_history_and_duplicates(self):
        content = '''```json
{"keywords": ["#Oversized Shirt", "oversized_shirt", "پیراهن کژوال", "شلوار کژوال"]}
```'''
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_kwargs: response)
            )
        )
        with patch.object(config, "AVALAI_API_KEY", "test-key"), patch.object(
            ai_service, "get_client", return_value=client
        ):
            terms, error = ai_service.generate_search_keywords(
                ["#پیراهن"], excluded_terms=["شلوار_کژوال"], max_keywords=5
            )
        self.assertIsNone(error)
        self.assertEqual(terms, ["oversized_shirt", "پیراهن_کژوال"])


class ScheduledKeywordHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "schedules.db")
        database.init(self.db_path)
        self.schedule_id = database.create_schedule({
            "chat_id": 123,
            "hashtags": ["پیراهن"],
        })

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_new_ai_terms_are_durable_and_never_repeated_per_schedule(self):
        def fake_generator(_seeds, *, excluded_terms, **_kwargs):
            if not excluded_terms:
                return ["Relaxed Fit", "linen_shirt"], None
            return ["relaxed_fit", "linen shirt", "Oversized tee"], None

        with patch.object(search_service.ai_service, "generate_search_keywords", side_effect=fake_generator):
            first_terms, first_generated, first_error = search_service.generate_schedule_search_terms(
                self.schedule_id, ["#پیراهن"]
            )
            second_terms, second_generated, second_error = search_service.generate_schedule_search_terms(
                self.schedule_id, ["#پیراهن"]
            )

        self.assertIsNone(first_error)
        self.assertIsNone(second_error)
        self.assertEqual(first_generated, ["relaxed_fit", "linen_shirt"])
        self.assertEqual(second_generated, ["oversized_tee"])
        self.assertEqual(first_terms, ["پیراهن", "relaxed_fit", "linen_shirt"])
        self.assertEqual(second_terms, ["پیراهن", "oversized_tee"])
        self.assertEqual(
            database.get_schedule_keyword_history(self.schedule_id),
            ["relaxed_fit", "linen_shirt", "oversized_tee"],
        )

    def test_ai_failure_falls_back_to_user_terms(self):
        with patch.object(
            search_service.ai_service,
            "generate_search_keywords",
            return_value=([], "AvalAI offline"),
        ):
            terms, generated, error = search_service.generate_schedule_search_terms(
                self.schedule_id, ["#پیراهن"]
            )
        self.assertEqual(terms, ["پیراهن"])
        self.assertEqual(generated, [])
        self.assertEqual(error, "AvalAI offline")

    def test_run_log_records_shared_search_terms(self):
        terms = ["پیراهن", "relaxed_fit"]
        database.log_run(self.schedule_id, "OK", 7, search_terms=terms)
        with sqlite3.connect(self.db_path) as con:
            raw = con.execute("SELECT search_terms FROM job_runs").fetchone()[0]
        self.assertEqual(json.loads(raw), terms)

    def test_migrates_existing_job_runs_table(self):
        old_path = os.path.join(self.temp_dir.name, "old.db")
        with sqlite3.connect(old_path) as con:
            con.execute("""CREATE TABLE job_runs (
                id INTEGER PRIMARY KEY, schedule_id INTEGER, triggered_at TEXT,
                status TEXT, items_count INTEGER, winner_url TEXT, error TEXT
            )""")
        database.init(old_path)
        with sqlite3.connect(old_path) as con:
            columns = {row[1] for row in con.execute("PRAGMA table_info(job_runs)")}
        self.assertIn("search_terms", columns)


class ScheduledJobRoutingTests(unittest.TestCase):
    def test_apify_and_telegram_receive_the_same_generated_terms(self):
        try:
            import telebot  # noqa: F401
        except ImportError:
            self.skipTest("pyTelegramBotAPI dependency is not installed")

        with patch.object(config, "TELEGRAM_BOT_TOKEN", "123:TESTTOKEN"):
            if "jobs" in sys.modules:
                jobs = sys.modules["jobs"]
            else:
                import jobs

        schedule = {
            "id": 7, "chat_id": 123, "trashed": 0, "hashtags": ["seed_tag"],
            "count_per_hashtag": 10, "content_type": "both", "want_comments": 0,
            "top_n": 3, "comments_per": 5,
        }
        terms = ["seed_tag", "fresh_term"]

        class FakeBot:
            def send_message(self, *_args, **_kwargs):
                return SimpleNamespace(message_id=99)

            def edit_message_text(self, *_args, **_kwargs):
                return None

        with patch.object(jobs, "bot", FakeBot()), patch.object(
            jobs.database, "get_schedule", return_value=schedule
        ), patch.object(jobs.database, "log_run") as log_run, patch.object(
            jobs.search_service, "generate_schedule_search_terms",
            return_value=(terms, ["fresh_term"], None),
        ), patch.object(jobs.apify_service, "scrape_media", side_effect=[[], []]) as scrape, patch.object(
            jobs.search_service, "scan_telegram_terms", return_value=(None, "Telegram unavailable")
        ) as telegram_scan:
            jobs.run_scheduled_job(7)

        urls = [
            "https://www.instagram.com/explore/tags/seed_tag/",
            "https://www.instagram.com/explore/tags/fresh_term/",
        ]
        self.assertEqual(scrape.call_args_list, [call(urls, "posts", 10), call(urls, "reels", 10)])
        telegram_scan.assert_called_once_with(terms)
        self.assertEqual(log_run.call_args.kwargs["search_terms"], terms)


class TelegramSearchIntegrationTests(unittest.TestCase):
    def test_pipeline_runs_global_search_without_discovered_channels(self):
        from trendradar.analyzer import TrendAnalyzer
        from trendradar.models import SearchQuery
        from trendradar.pipeline import TrendPipeline
        from trendradar.providers.base import Capability
        from trendradar.storage import TrendStorage
        from trendradar.tools.test_pipeline import MockProvider

        with tempfile.TemporaryDirectory() as temp_dir:
            settings = TrendSettings(db_path=os.path.join(temp_dir, "trend.db"))
            settings.min_channel_members = 0
            provider = MockProvider(settings)
            # هیچ قابلیت کشف/لیست کانال فعالی نیست؛ فقط جست‌وجوی سطح پلتفرم.
            provider.capabilities = {Capability.SEARCH_HASHTAG}
            pipeline = TrendPipeline(
                provider, TrendStorage(settings.db_path), TrendAnalyzer(), settings=settings
            )
            report = asyncio.run(pipeline.run(SearchQuery(
                hashtags=["ترند_مدلینگ"], discover_channels=False,
                min_members=0, window_hours=200, limit_per_tag=20,
            )))

        self.assertEqual(report.channels_scanned, 0)
        self.assertEqual(report.posts_collected, 20)
        self.assertEqual(provider.search_calls, ["ترند_مدلینگ"])

    def test_telegram_search_is_global_first_and_reuses_keyword(self):
        provider = TelegramProvider(settings=TrendSettings())
        calls = []
        found = Post(
            provider="telegram", channel_uid="telegram:42", message_id=1,
            date=utcnow(), text="#linen_shirt", hashtags=["linen_shirt"],
        )

        class FakeSearch:
            async def search_global(self, term, **kwargs):
                calls.append((term, kwargs))
                return [found]

            async def search_in_channels_history(self, *_args, **_kwargs):
                raise AssertionError("global results already satisfy the requested limit")

        provider.search_ops = FakeSearch()
        result = asyncio.run(
            provider.search_hashtag("linen_shirt", channels=(), limit=1)
        )
        self.assertEqual(calls[0][0], "#linen_shirt")
        self.assertEqual(result, [found])

    def test_missing_telegram_credentials_are_reported_without_scanning(self):
        with patch("trendradar.config.load_settings", return_value=TrendSettings()):
            report, error = search_service.scan_telegram_terms(["linen_shirt"])
        self.assertIsNone(report)
        self.assertIn("TG_API_ID", error)


if __name__ == "__main__":
    unittest.main()
