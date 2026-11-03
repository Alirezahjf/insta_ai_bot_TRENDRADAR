import asyncio, unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from trendradar.models import ChannelRef, Post, utcnow
from trendradar.providers.telegram.search import TelegramSearch
from trendradar.providers.telegram.provider import TelegramProvider
from trendradar.config import TrendSettings
from trendradar.errors import RateLimited

def post(mid, text="پیراهن کژوال مردانه", hashtags=None):
    return Post(provider="telegram", channel_uid="telegram:42", message_id=mid, date=utcnow(), text=text, hashtags=hashtags or [])

class TelegramFallbackTests(unittest.TestCase):
    def setUp(self):
        self.channel = ChannelRef(provider="telegram", native_id=42, title="shop")
        self.calls = []
        class Raw:
            async def get_input_entity(self, native): return native
            def iter_messages(self, entity, **kwargs):
                self.kwargs = kwargs
                async def gen():
                    for m in self.messages: yield m
                return gen()
        self.raw = Raw()
        self.tg = SimpleNamespace(raw=self.raw, search_counter=None, search_limiter=SimpleNamespace(wait_async=self._wait))
        self.search = TelegramSearch(self.tg)
    async def _wait(self): return 0
    def run_search(self, messages_by_term):
        async def fake_history(channel, term, **kwargs):
            self.calls.append(term)
            return messages_by_term.get(term, [])
        with patch.object(self.search, "search_in_chat_history", side_effect=fake_history):
            return asyncio.run(self.search.search_hashtag_with_fallback(self.channel, "پیراهن_کژوال_مردانه", limit=5))
    def test_hashtag_success_skips_plain(self):
        p = post(1, "#پیراهن_کژوال_مردانه", ["پیراهن_کژوال_مردانه"])
        result = self.run_search({"#پیراهن_کژوال_مردانه": [p]})
        self.assertEqual(self.calls, ["#پیراهن_کژوال_مردانه"])
        self.assertEqual(result[0].extra["match_mode"], "hashtag")
    def test_empty_hashtag_falls_back_to_plain_in_same_channel(self):
        p = post(2)
        result = self.run_search({"#پیراهن_کژوال_مردانه": [], "پیراهن کژوال مردانه": [p]})
        self.assertEqual(self.calls, ["#پیراهن_کژوال_مردانه", "پیراهن کژوال مردانه"])
        self.assertEqual(result[0].extra["match_mode"], "plain_text")
    def test_both_empty_is_not_error(self):
        self.assertEqual(self.run_search({}), [])
    def test_telegram_error_is_not_empty_result(self):
        async def fail(*a, **k): raise RateLimited("flood", retry_after=10)
        with patch.object(self.search, "search_in_chat_history", side_effect=fail):
            with self.assertRaises(RateLimited):
                asyncio.run(self.search.search_hashtag_with_fallback(self.channel, "test"))
    def test_provider_searches_channels_even_when_public_has_results(self):
        provider = TelegramProvider(TrendSettings())
        public = post(1)
        local = post(2)
        seen = []
        class Search:
            async def search_public_hashtag(self, tag, **kw): return [public]
            async def search_hashtag_with_fallback(self, channel, tag, **kw):
                seen.append(channel.uid); return [local, public]
            @staticmethod
            def _dedupe(posts):
                out, seen = [], set()
                for p in posts:
                    if p.uid not in seen: seen.add(p.uid); out.append(p)
                return out
        provider.search_ops = Search()
        result = asyncio.run(provider.search_hashtag("shirt", channels=[self.channel], limit=5))
        self.assertEqual(seen, ["telegram:42"])
        self.assertEqual([p.message_id for p in result], [1, 2])
