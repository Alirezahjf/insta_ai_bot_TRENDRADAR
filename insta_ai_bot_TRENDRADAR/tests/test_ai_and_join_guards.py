import asyncio, json, unittest
from types import SimpleNamespace
from unittest.mock import patch
import config, scheduled_report, search_service
from trendradar.models import ChannelRef

def response(payload):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])

class StructuredAITests(unittest.TestCase):
    def test_structured_prompt_missing_metrics_and_no_visual_claim(self):
        seen = {}
        def create(**kwargs):
            seen.update(kwargs)
            data = json.loads(kwargs["messages"][-1]["content"])
            posts = data["posts"]
            return response({"analyses": [{"id": p["id"], "relevant": None if not p["text_available"] else True,
                                           "reason": "دلیل", "strengths": ["متن مرتبط"], "data_limits": ["بازدید نامشخص"],
                                           "actionable_tip": "کپشن راทดสอบ کن", "evidence": ["عنوان"], "hypotheses": []} for p in posts],
                             "best_id": None})
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        records = [{"id": "1", "platform": "Telegram", "title": "", "media_type": "photo", "text_available": False, "views": None},
                   {"id": "2", "platform": "Instagram", "title": "shirt", "media_type": "image", "text_available": True, "likes": 3, "views": None}]
        with patch.object(config, "AVALAI_API_KEY", "test"), patch("ai_service.get_client", return_value=client):
            result = scheduled_report.analyze_all(records, ["shirt"])
        prompt = seen["messages"][0]["content"]
        self.assertIn("تصویر یا ویدئو را نمی‌بینی", prompt)
        data = json.loads(seen["messages"][-1]["content"])
        self.assertIsNone(data["posts"][0]["metrics"]["views"])
        self.assertIn("views", data["posts"][0]["missing_metrics"])
        self.assertIsNone(result["relevance"]["1"])
        self.assertEqual(result["relevance"]["2"], True)
        self.assertIn("محدودیت داده", result["analyses"]["2"])

class JoinGuardTests(unittest.TestCase):
    def _radar(self, channels, join=None, remaining=10, wait=0):
        joined = []
        class Provider:
            tg = SimpleNamespace(join_guard=SimpleNamespace(seconds_until_next_join=wait, counter=SimpleNamespace(remaining=remaining)))
            async def connect(self): pass
            async def discover_channels(self, query, **kw): return channels
            async def join_channel(self, channel):
                joined.append(channel.uid)
                if join: return join(channel)
                channel.joined = True
                return channel
            def export_state(self): return {}
        storage = SimpleNamespace(upsert_channel=lambda c: None, save_provider_state=lambda *a: None, get_channels=lambda **kw: [])
        return SimpleNamespace(provider=lambda name: Provider(), storage=storage), joined
    def test_no_opt_in_does_not_join(self):
        ch = ChannelRef(provider="telegram", native_id=1)
        radar, joined = self._radar([ch])
        result = asyncio.run(search_service._discover_and_join(radar, ["shirt"], auto_join=False))
        self.assertEqual(joined, [])
        self.assertTrue(any("غیرفعال" in n for n in result.notes))
    def test_already_joined_and_duplicate_are_not_rejoined(self):
        a = ChannelRef(provider="telegram", native_id=1, joined=True)
        b = ChannelRef(provider="telegram", native_id=2)
        radar, joined = self._radar([a, b, b])
        asyncio.run(search_service._discover_and_join(radar, ["shirt"], auto_join=True))
        self.assertEqual(joined, ["telegram:2"])
    def test_daily_cap_and_flood_defer(self):
        from trendradar.errors import DailyCapReached, RateLimited
        ch = ChannelRef(provider="telegram", native_id=1)
        radar, joined = self._radar([ch], remaining=0)
        result = asyncio.run(search_service._discover_and_join(radar, ["shirt"], auto_join=True))
        self.assertEqual(joined, [])
        self.assertTrue(any("سقف روزانه" in n for n in result.notes))
        def flood(channel): raise RateLimited("flood", retry_after=120)
        radar, joined = self._radar([ch], join=flood)
        result = asyncio.run(search_service._discover_and_join(radar, ["shirt"], auto_join=True))
        self.assertTrue(any("ایمنی" in n for n in result.notes))
    def test_budget_and_revoked_consent_defer(self):
        ch = ChannelRef(provider="telegram", native_id=1)
        radar, joined = self._radar([ch], wait=60)
        with patch.dict("os.environ", {"TG_JOIN_BUDGET_SECONDS": "1"}):
            result = asyncio.run(search_service._discover_and_join(radar, ["shirt"], auto_join=True))
        self.assertEqual(joined, [])
        self.assertTrue(any("بودجه" in n for n in result.notes))
        radar, joined = self._radar([ch])
        result = asyncio.run(search_service._discover_and_join(radar, ["shirt"], auto_join=True, consent_check=lambda: False))
        self.assertEqual(joined, [])
        self.assertTrue(any("رضایت" in n for n in result.notes))
    def test_pending_approval_is_not_success(self):
        ch = ChannelRef(provider="telegram", native_id=1)
        def pending(channel):
            channel.extra["pending_approval"] = True
            return channel
        radar, joined = self._radar([ch], join=pending)
        result = asyncio.run(search_service._discover_and_join(radar, ["shirt"], auto_join=True))
        self.assertEqual(result.stats["joined"], 0)
        self.assertEqual(result.stats["pending_approval"], 1)
