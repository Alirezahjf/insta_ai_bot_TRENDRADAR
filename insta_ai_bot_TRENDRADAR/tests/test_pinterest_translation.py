import sys, unittest
from types import SimpleNamespace
from unittest.mock import patch
import pinterest_service
import config

class PinterestTranslationTests(unittest.TestCase):
    def test_english_unchanged_and_persian_translated_with_provenance(self):
        def translate(queries, *, topic=""):
            return {q["id"]: "men casual shirt" for q in queries}, None
        with patch("ai_service.translate_pinterest_queries", side_effect=translate):
            queries, audit, error = pinterest_service.prepare_pinterest_queries(
                ["#Linen_Shirt", "پیراهن_کژوال_مردانه", "پیراهن کژوال مردانه"], topic="پوشاک")
        self.assertIsNone(error)
        self.assertEqual([q["query"] for q in queries], ["Linen Shirt", "men casual shirt"])
        self.assertEqual(audit[0]["status"], "unchanged")
        self.assertEqual(audit[1]["status"], "translated")
        self.assertEqual(queries[1]["sources"], ["پیراهن_کژوال_مردانه", "پیراهن کژوال مردانه"])

    def test_invalid_ai_translation_is_not_searched(self):
        with patch("ai_service.translate_pinterest_queries", return_value=({"q0": "#fashion"}, "invalid")):
            queries, audit, error = pinterest_service.prepare_pinterest_queries(["پیراهن"])
        self.assertEqual(queries, [])
        self.assertEqual(audit[0]["status"], "unavailable")
        self.assertTrue(error)

    def test_scan_dedups_english_queries_and_keeps_term_results(self):
        calls = []
        def extract(query, **kwargs):
            calls.append(query)
            return [{"pin_id": "1", "pin_url": "https://pinterest.com/pin/1/", "title": "shirt"}]
        fake = SimpleNamespace(extract=extract, score_pins=lambda pins: pins)
        with patch.dict(sys.modules, {"pinterest_extractor": fake}), \
             patch("ai_service.translate_pinterest_queries", return_value=({"q0": "casual shirt", "q1": "casual shirt"}, None)), \
             patch.dict("os.environ", {"PINTEREST_ENABLED": "1"}):
            pins, errors, audit = pinterest_service.scan_pinterest_terms(["پیراهن", "پیراهن_کژوال"], return_queries=True)
        self.assertEqual(calls, ["casual shirt"])
        self.assertEqual(pins[0]["search_terms"], ["پیراهن", "پیراهن_کژوال"])
        self.assertEqual(audit["term_results"], {"پیراهن": "succeeded", "پیراهن_کژوال": "succeeded"})

    def test_disabled_does_not_call_ai(self):
        with patch("ai_service.translate_pinterest_queries") as translate, patch.dict("os.environ", {"PINTEREST_ENABLED": "0"}):
            pins, errors, audit = pinterest_service.scan_pinterest_terms(["پیراهن"], return_queries=True)
        translate.assert_not_called()
        self.assertEqual(audit["term_results"]["پیراهن"], "skipped")

    def test_ai_timeout_is_reported_not_faked(self):
        import ai_service
        class Client:
            class chat:
                class completions:
                    @staticmethod
                    def create(**kw): raise TimeoutError("secret detail")
        with patch.object(config, "AVALAI_API_KEY", "test"), patch("ai_service.get_client", return_value=Client()):
            accepted, error = ai_service.translate_pinterest_queries([{"id": "q0", "source": "پیراهن"}])
        self.assertEqual(accepted, {})
        self.assertEqual(error, "TimeoutError")
