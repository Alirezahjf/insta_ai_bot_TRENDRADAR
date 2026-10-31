import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import config
import scheduled_report
from trendradar.config import TrendSettings
from trendradar.models import ChannelRef, Post, utcnow
from trendradar.providers.telegram.provider import TelegramProvider
from trendradar.providers.telegram.search import TelegramSearch


class FakeAI:
    def __init__(self, fail_batch=False):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.seen = []
        self.fail_batch = fail_batch

    def create(self, **kwargs):
        data = json.loads(kwargs["messages"][-1]["content"])
        if "posts" in data:
            posts = data["posts"]
            self.seen.extend(p["id"] for p in posts)
            if self.fail_batch and len(self.seen) > 15:
                raise RuntimeError("failure")
            result = {"analyses": [{"id": p["id"], "analysis": "تحلیل"} for p in posts],
                      "best_id": posts[-1]["id"], "reason": "دلیل"}
        else:
            result = {"best_id": data["batch_winners"][-1]["id"], "summary": "نتیجه"}
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(result)))])


class AnalysisTests(unittest.TestCase):
    def test_every_post_is_analyzed_and_best_can_be_in_last_batch(self):
        records = [{"id": str(n), "platform": "Instagram", "title": "x"} for n in range(31)]
        ai = FakeAI()
        with patch.object(config, "AVALAI_API_KEY", "test"), patch("ai_service.get_client", return_value=ai):
            result = scheduled_report.analyze_all(records, ["python"])
        self.assertEqual(ai.seen, [str(n) for n in range(31)])
        self.assertEqual(result["analyzed"], 31)
        self.assertEqual(result["best_id"], "30")

    def test_incomplete_analysis_never_claims_overall_winner(self):
        records = [{"id": str(n), "platform": "Instagram"} for n in range(20)]
        ai = FakeAI(fail_batch=True)
        with patch.object(config, "AVALAI_API_KEY", "test"), patch("ai_service.get_client", return_value=ai):
            result = scheduled_report.analyze_all(records, ["python"])
        self.assertEqual(result["analyzed"], 15)
        self.assertIsNone(result["best_id"])
        self.assertTrue(result["errors"])

    def test_untrusted_csv_formulas_are_escaped(self):
        data = scheduled_report.report_csv([{"id": "x", "title": "=1+1", "url": "+evil"}], {})
        self.assertIn("'=1+1", data.decode("utf-8-sig"))


class TelegramFallbackTests(unittest.TestCase):
    def test_global_empty_falls_back_to_discovered_channel_without_joining(self):
        p = TelegramProvider(TrendSettings())
        ch = ChannelRef(provider="telegram", native_id=42, title="Python")
        post = Post(provider="telegram", channel_uid=ch.uid, message_id=1, date=utcnow(), text="Python tutorial")
        calls = []
        class Search:
            async def search_global(self, term, **kwargs):
                calls.append(("global", term))
                return []
            async def search_in_chat_history(self, channel, term, **kwargs):
                calls.append(("history", channel.uid, term))
                return [post]
            @staticmethod
            def _dedupe(items):
                return items
        class Discovery:
            async def search_channels(self, query, **kwargs):
                calls.append(("discover", query))
                return [ch]
        p.search_ops, p.discovery_ops = Search(), Discovery()
        result = asyncio.run(p.search_hashtag("python", limit=5))
        self.assertEqual(result, [post])
        self.assertEqual(calls, [("global", "#python"), ("discover", "python"), ("history", ch.uid, "python")])

    def test_history_fetch_does_not_apply_upper_date_as_lower_bound(self):
        class Raw:
            async def get_input_entity(self, _): return object()
            def iter_messages(self, entity, **kwargs):
                self.kwargs = kwargs
                async def generate():
                    if False: yield None
                return generate()
        class Limiter:
            async def wait_async(self): pass
        raw = Raw()
        tg = SimpleNamespace(raw=raw, search_counter=None, search_limiter=Limiter())
        channel = ChannelRef(provider="telegram", native_id=42)
        asyncio.run(TelegramSearch(tg).search_in_chat_history(channel, "python", since=datetime.now(timezone.utc)-timedelta(days=3)))
        self.assertNotIn("offset_date", raw.kwargs)


if __name__ == "__main__":
    unittest.main()
