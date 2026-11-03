import unittest
from types import SimpleNamespace
from unittest.mock import patch
import instagram_media

CDN = "https://scontent.cdninstagram.com/x.jpg"
VID = "https://video.cdninstagram.com/x.mp4"

class InstagramMediaTests(unittest.TestCase):
    def test_image_video_carousel_and_missing(self):
        self.assertEqual(instagram_media.extract_media({"type": "Image", "displayUrl": CDN})[0]["type"], "photo")
        self.assertEqual(instagram_media.extract_media({"type": "Video", "videoUrl": VID})[0]["type"], "video")
        carousel = {"type": "Sidecar", "childPosts": [{"type": "Image", "displayUrl": CDN}, {"type": "Video", "videoUrl": VID}]}
        self.assertEqual([m["type"] for m in instagram_media.extract_media(carousel)], ["photo", "video"])
        self.assertEqual(instagram_media.extract_media({"type": "Image"}), [])
        self.assertEqual(instagram_media.extract_media({"type": "Image", "displayUrl": "http://evil.test/x.jpg"}), [])
    def test_video_cover_is_not_claimed_as_video(self):
        media = instagram_media.extract_media({"type": "Video", "displayUrl": CDN})
        self.assertEqual(media[0]["type"], "photo")
        self.assertTrue(media[0]["cover_only"])
    def test_send_failure_keeps_original_link(self):
        class Bot:
            def __init__(self): self.messages = []
            def send_photo(self, *a, **k): raise RuntimeError("failed to get HTTP URL content")
            def send_message(self, chat, text, **k): self.messages.append(text)
        bot = Bot()
        record = {"id": "1", "url": "https://www.instagram.com/p/abc/", "raw": {"type": "Image", "displayUrl": CDN}, "likes": 3}
        result = instagram_media.send_instagram_media(bot, 1, [record], {"relevance": {"1": True}, "analyses": {}}, pause=0)
        self.assertEqual(result[0]["status"], "send_failed")
        self.assertIn("https://www.instagram.com/p/abc/", bot.messages[0])
    def test_carousel_uses_media_group_and_caps(self):
        class Bot:
            def __init__(self): self.groups = []
            def send_media_group(self, chat, group): self.groups.append(group)
        bot = Bot()
        record = {"id": "1", "url": "https://www.instagram.com/p/abc/", "raw": {"type": "Sidecar", "childPosts": [{"type": "Image", "displayUrl": CDN + f"?{i}"} for i in range(5)]}}
        result = instagram_media.send_instagram_media(bot, 1, [record], {"relevance": {}, "analyses": {}}, max_media_per_post=3, pause=0)
        self.assertEqual(len(bot.groups[0]), 3)
        self.assertEqual(result[0]["media_count"], 3)
