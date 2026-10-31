import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pinterest_service
from unified_ranking import build_unified_ranking


class PinterestScanTests(unittest.TestCase):
    def test_per_term_search_dedup_and_partial_failure(self):
        calls = []
        def extract(query, **kwargs):
            calls.append((query, kwargs))
            if query == "bad":
                raise RuntimeError("offline")
            return [{"pin_id": "1", "title": "shirt", "pin_url": "https://pinterest.com/pin/1/"}]
        fake = SimpleNamespace(extract=extract, score_pins=lambda pins: pins)
        with patch.dict(sys.modules, {"pinterest_extractor": fake}), patch.dict("os.environ", {"PINTEREST_ENABLED": "1"}):
            results, errors = pinterest_service.scan_pinterest_terms(["linen_shirt", "shirt", "bad"], 4)
        self.assertEqual([x[0] for x in calls], ["linen shirt", "shirt", "bad"])
        self.assertEqual(results[0]["search_terms"], ["linen_shirt", "shirt"])
        self.assertEqual(len(errors), 1)

    def test_disabled(self):
        with patch.dict("os.environ", {"PINTEREST_ENABLED": "0"}):
            self.assertEqual(pinterest_service.scan_pinterest_terms(["shirt"])[0], [])

    def test_unified_ranking_relative_and_ties(self):
        post = SimpleNamespace(uid="t:1", text="telegram", channel_title="", url="https://t.me/a/1")
        report = SimpleNamespace(signals=[SimpleNamespace(top_posts=[post], score=90)])
        ig = [{"url": "https://instagram.com/p/1", "caption": "a", "viral_score": 90},
              {"url": "https://instagram.com/p/2", "caption": "b", "viral_score": 20}]
        pins = [{"pin_id": "1", "title": "p1", "score": 40},
                {"pin_id": "2", "title": "p2", "score": 40}]
        ranked = build_unified_ranking(ig, report, pins)
        self.assertEqual([x["relative_score"] for x in ranked], [100, 50, 50, 50, 0])
        self.assertEqual({x["platform"] for x in ranked}, {"Instagram", "Telegram", "Pinterest"})

if __name__ == "__main__":
    unittest.main()
