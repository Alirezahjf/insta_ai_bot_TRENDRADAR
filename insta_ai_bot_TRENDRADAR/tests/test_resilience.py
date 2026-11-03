import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import config
import scheduled_report
import search_service


def response(payload):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])


class BatchRecoveryTests(unittest.TestCase):
    def test_partial_batch_only_retries_missing_and_preserves_unknown(self):
        calls = []
        def request(**kwargs):
            data = json.loads(kwargs['messages'][-1]['content'])
            ids = [p['id'] for p in data['posts']]
            calls.append(ids)
            if len(calls) == 1:
                return response({'analyses': [{'id': ids[0], 'analysis': 'مرتبط', 'relevant': True},
                                              {'id': ids[1], 'analysis': 'اطلاعات ناکافی', 'relevant': None}],
                                 'best_id': ids[0]})
            return response({'analyses': [{'id': ids[0], 'analysis': 'نامرتبط', 'relevant': False}],
                             'best_id': None})
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=request)))
        records = [{'id': str(i), 'platform': 'Instagram'} for i in range(3)]
        with patch.object(config, 'AVALAI_API_KEY', 'test'), patch('ai_service.get_client', return_value=client):
            result = scheduled_report.analyze_all(records, ['topic'])
        self.assertEqual(calls, [['0', '1', '2'], ['2']])
        self.assertEqual(result['analyzed'], 3)
        self.assertEqual(result['relevance'], {'0': True, '1': None, '2': False})
        self.assertIsNone(result['best_id'])
        self.assertEqual(result['errors'], [])
        self.assertIn('unknown', scheduled_report.report_csv([records[1]], result['analyses'], result['relevance']).decode('utf-8-sig'))

    def test_bad_json_bounded_retry_no_false_success_and_no_sensitive_log(self):
        def request(**kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='secret response {'))])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=request)))
        records = [{'id': '1', 'platform': 'Instagram'}]
        with patch.object(config, 'AVALAI_API_KEY', 'test'), patch('ai_service.get_client', return_value=client), self.assertLogs('trendradar.analysis', level='WARNING') as logs:
            result = scheduled_report.analyze_all(records, ['topic'])
        self.assertEqual(result['analyzed'], 0)
        self.assertIsNone(result['best_id'])
        self.assertEqual(result['attempts'], 2)
        self.assertNotIn('secret response', ' '.join(logs.output))


class TelegramQueueTests(unittest.TestCase):
    def test_queue_timeout_does_not_start_scan(self):
        lock = threading.Lock()
        lock.acquire()
        radar = SimpleNamespace(scan=lambda **kwargs: self.fail('scan must not run'))
        settings = SimpleNamespace(telegram=SimpleNamespace(configured=True), enabled_providers=[])
        progress = []
        try:
            with patch.object(search_service, '_telegram_scan_lock', lock), patch.object(search_service, '_radar', radar), \
                 patch('trendradar.config.load_settings', return_value=settings), \
                 patch.dict('os.environ', {'TG_SCAN_QUEUE_TIMEOUT': '1'}):
                report, error = search_service.scan_telegram_terms(['test'], progress=progress.append)
        finally:
            lock.release()
        self.assertIsNone(report)
        self.assertIn('صف Telegram', error)
        self.assertTrue(progress)


if __name__ == '__main__':
    unittest.main()


class JoinBudgetTests(unittest.TestCase):
    def test_join_budget_stops_without_overriding_rate_guard(self):
        import asyncio
        calls = []
        class Provider:
            tg = SimpleNamespace(join_guard=SimpleNamespace(seconds_until_next_join=60, counter=SimpleNamespace(remaining=1)))
            async def connect(self): pass
            async def discover_channels(self, query, **kwargs):
                return [SimpleNamespace(uid="1", joined=False, title="test")]
            async def join_channel(self, channel): calls.append(channel)
            def export_state(self): return {}
        radar = SimpleNamespace(provider=lambda name: Provider(), storage=SimpleNamespace(save_provider_state=lambda *a: None, get_channels=lambda **kw: []))
        with patch.dict('os.environ', {'TG_JOIN_BUDGET_SECONDS': '1'}):
            result = asyncio.run(search_service._discover_and_join(radar, ['test'], auto_join=True))
        notes = result.notes
        self.assertFalse(calls)
        self.assertTrue(any('بودجه زمانی' in note for note in notes))

async def _async_result(value):
    return value


class TelegramQueueSuccessTests(unittest.TestCase):
    def test_queued_scan_proceeds_after_previous_run_releases_lock(self):
        lock = threading.Lock()
        lock.acquire()
        returned = SimpleNamespace(errors=[], provider_stats={}, term_results={})
        radar = SimpleNamespace(scan=lambda **kwargs: returned, _run_async=lambda value: value, provider=lambda name: SimpleNamespace())
        settings = SimpleNamespace(telegram=SimpleNamespace(configured=True), enabled_providers=[])
        timer = threading.Timer(0.1, lock.release)
        progress = []
        timer.start()
        try:
            with patch.object(search_service, '_telegram_scan_lock', lock), patch.object(search_service, '_radar', radar), \
                 patch('trendradar.config.load_settings', return_value=settings), \
                 patch.object(search_service, '_discover_and_join', new=lambda *a, **k: search_service.JoinDiscoveryResult()), \
                 patch.dict('os.environ', {'TG_SCAN_QUEUE_TIMEOUT': '2'}):
                report, error = search_service.scan_telegram_terms(['test'], progress=progress.append)
        finally:
            timer.join()
        self.assertIs(report, returned)
        self.assertEqual(error, '')
        self.assertTrue(any('صف' in stage for stage in progress))
        self.assertTrue(any('جست‌وجوی هشتگ' in stage for stage in progress))
        self.assertFalse(lock.locked())
