import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import database
import search_service
import scheduled_report


class TopicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        database.init(os.path.join(self.tmp.name, "db.sqlite"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_topic_history_shared_between_schedules_and_never_uses_old_terms(self):
        ids = [database.create_schedule({"chat_id": 12, "topic": "Python"}) for _ in range(2)]
        def generator(topic, **kwargs):
            return ["python_news", "python_tutorial"], None
        with patch("ai_service.generate_topic_keywords", side_effect=generator):
            a, _, _ = search_service.generate_topic_search_terms(database.get_schedule(ids[0]))
            b, _, error = search_service.generate_topic_search_terms(database.get_schedule(ids[1]))
        self.assertEqual(a, ["python_news", "python_tutorial"])
        self.assertEqual(b, [])
        self.assertTrue(error)

    def test_legacy_schedule_prior_terms_are_excluded(self):
        sid = database.create_schedule({"chat_id": 12, "hashtags": ["python"]})
        database.log_run(sid, "OK", search_terms=["python_news"])
        with patch("ai_service.generate_topic_keywords", return_value=(["python_news", "python_new"], None)):
            terms, _, _ = search_service.generate_topic_search_terms(database.get_schedule(sid))
        self.assertEqual(terms, ["python_new"])

    def test_join_is_opt_in_by_default(self):
        sid = database.create_schedule({"chat_id": 12, "topic": "test"})
        self.assertFalse(database.get_schedule(sid)["telegram_auto_join"])


class LinkValidationTests(unittest.TestCase):
    def test_only_actual_direct_post_urls(self):
        valid = scheduled_report.valid_post_url
        self.assertFalse(valid({"platform": "Instagram", "url": "https://www.instagram.com/explore/tags/test/"}))
        self.assertTrue(valid({"platform": "Instagram", "url": "https://www.instagram.com/p/AbC/"}))
        self.assertFalse(valid({"platform": "Pinterest", "url": "https://www.pinterest.com/pin/e1_x/"}))
        self.assertTrue(valid({"platform": "Telegram", "url": "https://t.me/example/123"}))
        self.assertFalse(valid({"platform": "Telegram", "url": "https://t.me.evil.com/example/123"}))


class JoinSequenceTests(unittest.TestCase):
    def test_discover_plain_terms_and_join_distinct_channels(self):
        calls = []
        channels = [SimpleNamespace(uid=str(i), joined=False, title=str(i)) for i in range(2)]
        class Provider:
            tg = SimpleNamespace(join_guard=SimpleNamespace(seconds_until_next_join=0))
            async def connect(self): calls.append("connect")
            async def discover_channels(self, query, **kwargs):
                calls.append(("discover", query, kwargs["limit"]))
                return channels
            async def join_channel(self, channel):
                calls.append(("join", channel.uid))
                channel.joined = True
                return channel
            def export_state(self): return {}
        storage = SimpleNamespace(upsert_channel=lambda c: None, save_provider_state=lambda *args: None)
        radar = SimpleNamespace(provider=lambda name: Provider(), storage=storage)
        notes = asyncio.run(search_service._discover_and_join(radar, ["casual_style", "men_style"]))
        self.assertEqual(calls, ["connect", ("discover", "casual style", 10), ("join", "0"), ("join", "1"), ("discover", "men style", 10)])
        self.assertIn("2", notes[-1])


if __name__ == "__main__":
    unittest.main()

class UIFlowTests(unittest.TestCase):
    def test_topic_then_time_then_join_consent(self):
        import config
        with patch.object(config, "TELEGRAM_BOT_TOKEN", "123:TESTTOKEN"):
            import sched_flow
        u = {}
        class Bot:
            def send_message(self, *args, **kwargs): pass
            def edit_message_text(self, *args, **kwargs): pass
        user_store = {99: u}
        with patch.object(sched_flow, "bot", Bot()), patch.object(sched_flow, "get_user", lambda cid: user_store[cid]):
            sched_flow.start_creation(99)
            call = SimpleNamespace(message=SimpleNamespace(chat=SimpleNamespace(id=99), message_id=1), data="topic_fashion")
            self.assertTrue(sched_flow.on_callback(call, u))
            self.assertEqual(u["state"], "S_TIMES")
            self.assertIn("پوشاک", u["topic"])
            message = SimpleNamespace(chat=SimpleNamespace(id=99), text="08:00")
            self.assertTrue(sched_flow.on_text(message, u))
            self.assertEqual(u["state"], "S_COUNT")
            sched_flow._ask_join_permission(99)
            call.data = "schjoin_yes"
            self.assertTrue(sched_flow.on_callback(call, u))
            self.assertTrue(u["telegram_auto_join"])
            self.assertEqual(u["state"], "S_CONFIRM")

class TelegramAPITests(unittest.TestCase):
    def test_real_telethon_public_hashtag_request_without_paid_stars(self):
        try:
            from telethon.tl.functions.channels import SearchPostsRequest
        except ImportError:
            self.skipTest("installed Telethon has no SearchPostsRequest")
        from trendradar.providers.telegram.search import TelegramSearch
        calls = []
        class FakeTG:
            async def request(self, request):
                calls.append(request)
                return SimpleNamespace(messages=[], chats=[])
        results = asyncio.run(TelegramSearch(FakeTG()).search_public_hashtag("fashion", limit=20))
        self.assertEqual(results, [])
        self.assertIsInstance(calls[0], SearchPostsRequest)
        self.assertEqual(calls[0].hashtag, "fashion")
        self.assertIsNone(calls[0].allow_paid_stars)

    def test_flood_limit_is_not_wrapped_or_ignored_by_join(self):
        from trendradar.errors import RateLimited
        from trendradar.models import ChannelRef
        from trendradar.providers.telegram.membership import TelegramMembership
        class Guard:
            def check(self, uid): pass
        class Raw:
            async def get_input_entity(self, native): return native
        class TG:
            raw = Raw()
            join_guard = Guard()
            async def request(self, *args, **kwargs):
                raise RateLimited("flood", retry_after=120, provider="telegram")
        with self.assertRaises(RateLimited):
            asyncio.run(TelegramMembership(TG()).join(ChannelRef(provider="telegram", native_id=12)))

class ScheduledOutputTests(unittest.TestCase):
    def test_only_relevant_direct_posts_in_ranked_output(self):
        import config
        with patch.object(config, "TELEGRAM_BOT_TOKEN", "123:TESTTOKEN"):
            import jobs
        import json
        schedule = {"id": 11, "chat_id": 1, "trashed": 0, "topic": "python", "hashtags": [],
                    "count_per_hashtag": 3, "content_type": "posts", "want_comments": False}
        class Bot:
            def __init__(self): self.edits, self.docs = [], []
            def send_message(self, *a, **kw): return SimpleNamespace(message_id=1)
            def edit_message_text(self, text, *a, **kw): self.edits.append(text)
            def send_document(self, chat_id, file, **kw): self.docs.append(file.read())
        bot = Bot()
        def classify(records, terms, **kwargs):
            return {"analyses": {r["id"]: "تحلیل" for r in records},
                    "relevance": {r["id"]: r["platform"] == "Instagram" for r in records},
                    "best_id": records[0]["id"], "summary": "مرتبط", "analyzed": len(records), "errors": []}
        with patch.object(jobs, "bot", bot), patch.object(jobs.database, "get_schedule", return_value=schedule), \
             patch.object(jobs.database, "log_run") as log, \
             patch.object(jobs.search_service, "generate_topic_search_terms", return_value=(["python_news"], [], None)), \
             patch.object(jobs.search_service, "scan_telegram_terms", return_value=(None, "")), \
             patch.object(jobs.apify_service, "scrape_media", return_value=[
                 {"url": "https://www.instagram.com/explore/tags/python/"},
                 {"url": "https://www.instagram.com/p/abc123/", "caption": "python", "likesCount": 3}]), \
             patch.object(jobs.pinterest_service, "scan_pinterest_terms", return_value=([
                 {"pin_url": "https://pinterest.com/pin/123/", "title": "نامرتبط", "score": 20}], [])), \
             patch.object(jobs.scheduled_report, "analyze_all", side_effect=classify):
            jobs.run_scheduled_job(11)
        self.assertEqual(len(bot.docs), 2)
        data = json.loads(bot.docs[0])
        self.assertEqual([r["platform"] for r in data["records"]], ["Instagram"])
        self.assertNotIn("نامرتبط", bot.docs[0].decode())
        self.assertIn('href="https://www.instagram.com/p/abc123/"', bot.edits[-1])
        self.assertNotIn('href="https://www.instagram.com/explore/', bot.edits[-1])
        self.assertNotEqual(log.call_args.args[1], "FAILED")
