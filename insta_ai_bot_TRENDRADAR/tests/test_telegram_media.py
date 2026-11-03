import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import config
import scheduled_report
import search_service
from trendradar.models import Post, MediaType, utcnow


class TelegramMediaTests(unittest.TestCase):
    def test_no_link_post_counts_and_has_media_metadata(self):
        post = Post(provider='telegram', channel_uid='telegram:42', message_id=8,
                    date=utcnow(), text='fashion', media_type=MediaType.PHOTO, views=25)
        records = scheduled_report.build_records([], [], SimpleNamespace(posts=[post]))
        self.assertEqual(records[0]['media_type'], 'photo')
        self.assertEqual(records[0]['channel_uid'], 'telegram:42')
        self.assertEqual(records[0]['message_id'], 8)
        self.assertEqual(records[0]['url'], '')
        self.assertEqual(len(scheduled_report.rank_records(records)), 1)

    def test_media_download_and_protected_channel(self):
        class Client:
            protected = False
            async def get_input_entity(self, _): return object()
            async def get_entity(self, _): return SimpleNamespace(noforwards=self.protected)
            async def get_messages(self, entity, ids):
                return SimpleNamespace(media=object(), noforwards=False, file=SimpleNamespace(size=3))
            def iter_download(self, media, **kwargs):
                async def chunks(): yield b'abc'
                return chunks()
        client = Client()
        class Provider:
            tg = SimpleNamespace(raw=client)
            async def connect(self): pass
        radar = SimpleNamespace(provider=lambda name: Provider())
        item = {'channel_uid': 'telegram:42', 'message_id': 8}
        result, error = asyncio.run(search_service._read_telegram_media(radar, item))
        self.assertEqual(result, b'abc')
        self.assertEqual(error, '')
        client.protected = True
        result, error = asyncio.run(search_service._read_telegram_media(radar, item))
        self.assertIsNone(result)
        self.assertIn('محدود', error)

    def test_size_limit_enforced_while_streaming(self):
        class Client:
            async def get_input_entity(self, _): return object()
            async def get_entity(self, _): return SimpleNamespace(noforwards=False)
            async def get_messages(self, entity, ids):
                return SimpleNamespace(media=object(), noforwards=False, file=SimpleNamespace(size=None))
            def iter_download(self, media, **kwargs):
                async def chunks(): yield b'abcd'
                return chunks()
        class Provider:
            tg = SimpleNamespace(raw=Client())
            async def connect(self): pass
        radar = SimpleNamespace(provider=lambda name: Provider())
        result, error = asyncio.run(search_service._read_telegram_media(radar, {'channel_uid': 'telegram:1', 'message_id': 1}, max_bytes=3))
        self.assertIsNone(result)
        self.assertIn('حجم', error)

    def test_job_displays_captionless_media_as_unknown_not_ranked(self):
        with patch.object(config, 'TELEGRAM_BOT_TOKEN', '123:TESTTOKEN'):
            import jobs
        post = Post(provider='telegram', channel_uid='telegram:42', message_id=9,
                    date=utcnow(), text='', media_type=MediaType.VIDEO, views=5)
        report = SimpleNamespace(posts=[post], posts_collected=1, errors=[])
        sched = {'id': 5, 'chat_id': 1, 'trashed': False, 'topic': 'fashion', 'hashtags': [],
                 'content_type': 'posts', 'count_per_hashtag': 2, 'want_comments': False}
        class Bot:
            def __init__(self): self.sent, self.docs = [], []
            def send_message(self, *a, **kw): return SimpleNamespace(message_id=1)
            def edit_message_text(self, *a, **kw): pass
            def send_video(self, chat, data, **kw): self.sent.append((data.read(), kw['caption']))
            def send_document(self, chat, data, **kw): self.docs.append(data.read())
        bot = Bot()
        def analyze(records, terms, **kwargs):
            return {'analyses': {records[0]['id']: 'ادعای بی‌پایه'}, 'relevance': {records[0]['id']: True},
                    'best_id': records[0]['id'], 'summary': '', 'analyzed': 1, 'errors': []}
        with patch.object(jobs, 'bot', bot), patch.object(jobs.database, 'get_schedule', return_value=sched), \
             patch.object(jobs.database, 'log_run'), \
             patch.object(jobs.search_service, 'generate_topic_search_terms', return_value=(['fashion'], [], None)), \
             patch.object(jobs.search_service, 'scan_telegram_terms', return_value=(report, '')), \
             patch.object(jobs.search_service, 'fetch_telegram_media', return_value=(b'video bytes', '')), \
             patch.object(jobs.apify_service, 'scrape_media', return_value=[]), \
             patch.object(jobs.pinterest_service, 'scan_pinterest_terms', return_value=([], [])), \
             patch.object(jobs.scheduled_report, 'analyze_all', side_effect=analyze):
            jobs.run_scheduled_job(5)
        self.assertEqual(len(bot.sent), 1)
        self.assertIn('نامشخص', bot.sent[0][1])
        payload = json.loads(bot.docs[0])
        self.assertEqual(len(payload['unknown_records']), 1)
        self.assertEqual(payload['unified_ranking'], [])
        self.assertIsNone(payload['ai']['best_id'])

    def test_job_shows_photo_without_url_and_without_claiming_visual_ai(self):
        with patch.object(config, 'TELEGRAM_BOT_TOKEN', '123:TESTTOKEN'):
            import jobs
        post = Post(provider='telegram', channel_uid='telegram:42', message_id=8,
                    date=utcnow(), text='fashion', media_type=MediaType.PHOTO, views=20)
        report = SimpleNamespace(posts=[post], posts_collected=1, errors=[])
        sched = {'id': 5, 'chat_id': 1, 'trashed': False, 'topic': 'fashion', 'hashtags': [],
                 'content_type': 'posts', 'count_per_hashtag': 2, 'want_comments': False}
        class Bot:
            def __init__(self): self.sent, self.edits, self.docs = [], [], []
            def send_message(self, *a, **kw): return SimpleNamespace(message_id=1)
            def edit_message_text(self, text, *a, **kw): self.edits.append(text)
            def send_photo(self, chat, data, **kw): self.sent.append((data.read(), kw['caption']))
            def send_document(self, chat, data, **kw): self.docs.append(data.read())
        bot = Bot()
        def analyze(records, terms, **kwargs):
            return {'analyses': {records[0]['id']: 'مرتبط'}, 'relevance': {records[0]['id']: True},
                    'best_id': records[0]['id'], 'summary': '', 'analyzed': 1, 'errors': []}
        with patch.object(jobs, 'bot', bot), patch.object(jobs.database, 'get_schedule', return_value=sched), \
             patch.object(jobs.database, 'log_run'), \
             patch.object(jobs.search_service, 'generate_topic_search_terms', return_value=(['fashion'], [], None)), \
             patch.object(jobs.search_service, 'scan_telegram_terms', return_value=(report, '')), \
             patch.object(jobs.search_service, 'fetch_telegram_media', return_value=(b'image bytes', '')), \
             patch.object(jobs.apify_service, 'scrape_media', return_value=[]), \
             patch.object(jobs.pinterest_service, 'scan_pinterest_terms', return_value=([], [])), \
             patch.object(jobs.scheduled_report, 'analyze_all', side_effect=analyze):
            jobs.run_scheduled_job(5)
        self.assertEqual(bot.sent[0][0], b'image bytes')
        self.assertIn('Telegram', bot.sent[0][1])
        self.assertIn('لینک مستقیم موجود نیست', bot.edits[-1])
        self.assertIn('نه محتوای تصویر/ویدئو', bot.edits[-1])
        payload = json.loads(bot.docs[0])
        self.assertEqual(len(payload['unified_ranking']), 1)
        self.assertEqual(payload['records'][0]['url'], '')


if __name__ == '__main__': unittest.main()
