import unittest

from playwright.sync_api import sync_playwright

import post

COMPOSER = '<div role="dialog" id="composer"><div role="textbox">bài viết</div></div>'


class IsVideoTest(unittest.TestCase):
    def test_recognizes_common_video_files(self):
        for name in ("clip.mp4", "CLIP.MOV", "D:/video/a.b.webm", "x.mkv", "x.avi", "x.m4v"):
            with self.subTest(name=name):
                self.assertTrue(post.is_video(name))

    def test_images_are_not_videos(self):
        for name in ("anh.png", "anh.JPG", "anh.gif", "mp4", "clip.mp4.png"):
            with self.subTest(name=name):
                self.assertFalse(post.is_video(name))

    def test_no_file_is_not_a_video(self):
        self.assertFalse(post.is_video(None))
        self.assertFalse(post.is_video(""))


class WaitForComposerToCloseTest(unittest.TestCase):
    """Runs against a local page in a real headless browser, never Facebook."""

    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Exception as error:
            cls.playwright.stop()
            raise unittest.SkipTest(f"Không mở được Chromium: {error}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)

    def test_returns_once_the_composer_disappears(self):
        self.page.set_content(
            COMPOSER
            + "<script>setTimeout(() => document.getElementById('composer').remove(), 1500)</script>"
        )
        self.assertTrue(post.wait_for_composer_to_close(self.page, 15000, poll_ms=200))
        self.assertEqual(self.page.locator("#composer").count(), 0)

    def test_gives_up_when_the_composer_stays_open(self):
        self.page.set_content(COMPOSER)
        self.assertFalse(post.wait_for_composer_to_close(self.page, 1000, poll_ms=200))

    def test_hidden_composer_counts_as_closed(self):
        self.page.set_content(COMPOSER.replace('id="composer"', 'id="composer" style="display:none"'))
        self.assertTrue(post.wait_for_composer_to_close(self.page, 1000, poll_ms=200))

    def test_other_dialogs_are_ignored(self):
        self.page.set_content('<div role="dialog">Trò chuyện với mọi người?</div>')
        self.assertTrue(post.wait_for_composer_to_close(self.page, 1000, poll_ms=200))


if __name__ == "__main__":
    unittest.main()
