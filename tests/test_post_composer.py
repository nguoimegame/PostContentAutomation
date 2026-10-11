import unittest

from playwright.sync_api import sync_playwright

import post

# Mirrors what a Facebook group page looked like with the composer open
# (observed 2026-10-11): a comment box and its file input come first in the
# DOM, and the real composer lives in a dialog further down.
GROUP_PAGE = """
<div role="textbox" id="comment-box" contenteditable="true" aria-label="Bình luận dưới tên Quang"></div>
<input type="file" id="comment-file" accept="video/*, video/mp4, image/*, image/heic">
<div role="button" id="group-composer-button"><span>Bạn viết gì đi...</span></div>
<div role="button">Bài viết ẩn danh</div>
<div role="dialog" aria-label="Thông báo" style="display:none"></div>
<div role="dialog">
  <div role="textbox" id="composer-box" contenteditable="true" aria-label="Tạo bài viết công khai..."></div>
  <input type="file" id="composer-file" accept="image/*,image/heif,video/*,video/mp4" multiple>
</div>
<div role="dialog" aria-label="Tạo bài viết"></div>
<input type="file" id="other-file" accept="image/*,video/*" multiple>
"""


class BrowserTestCase(unittest.TestCase):
    """Runs against local pages in a real headless browser, never Facebook."""

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

    def id_of(self, locator):
        self.assertIsNotNone(locator)
        return locator.get_attribute("id")


class FindCreatePostButtonTest(BrowserTestCase):
    def test_finds_the_vietnamese_group_composer(self):
        self.page.set_content(GROUP_PAGE)
        button = post.find_create_post_button(self.page)
        self.assertIsNotNone(button)
        self.assertIn("Bạn viết gì đi", button.inner_text())

    def test_finds_the_profile_composer(self):
        self.page.set_content('<div role="button" id="b"><span>Quang ơi, bạn đang nghĩ gì thế?</span></div>')
        self.assertEqual(self.id_of(post.find_create_post_button(self.page)), "b")

    def test_finds_the_english_composer(self):
        self.page.set_content('<div role="button" id="b">Write something...</div>')
        self.assertEqual(self.id_of(post.find_create_post_button(self.page)), "b")

    def test_ignores_a_hidden_button(self):
        self.page.set_content('<div role="button" style="display:none">Bạn viết gì đi...</div>')
        self.assertIsNone(post.find_create_post_button(self.page))

    def test_none_when_the_page_has_no_composer(self):
        self.page.set_content('<div role="button">Thích</div>')
        self.assertIsNone(post.find_create_post_button(self.page))


class FindComposerTextboxTest(BrowserTestCase):
    def test_picks_the_composer_not_the_comment_box(self):
        self.page.set_content(GROUP_PAGE)
        self.assertEqual(self.id_of(post.find_composer_textbox(self.page)), "composer-box")

    def test_falls_back_to_a_page_level_textbox(self):
        self.page.set_content('<div role="textbox" id="inline" contenteditable="true"></div>')
        self.assertEqual(self.id_of(post.find_composer_textbox(self.page)), "inline")

    def test_ignores_a_hidden_dialog(self):
        self.page.set_content(
            '<div role="dialog" style="display:none"><div role="textbox" id="hidden"></div></div>'
            '<div role="dialog"><div role="textbox" id="shown" contenteditable="true"></div></div>'
        )
        self.assertEqual(self.id_of(post.find_composer_textbox(self.page)), "shown")

    def test_none_when_there_is_nowhere_to_type(self):
        self.page.set_content("<p>Không có ô nhập</p>")
        self.assertIsNone(post.find_composer_textbox(self.page))


class FindUploadInputTest(BrowserTestCase):
    def test_picks_the_input_inside_the_composer(self):
        self.page.set_content(GROUP_PAGE)
        self.assertEqual(self.id_of(post.find_upload_input(self.page)), "composer-file")

    def test_falls_back_to_a_page_level_input(self):
        self.page.set_content('<input type="file" id="inline" accept="image/*,video/*">')
        self.assertEqual(self.id_of(post.find_upload_input(self.page)), "inline")

    def test_skips_inputs_for_other_file_types(self):
        self.page.set_content(
            '<div role="dialog"><div role="textbox"></div>'
            '<input type="file" id="docs" accept=".pdf,.docx">'
            '<input type="file" id="media" accept="image/*"></div>'
        )
        self.assertEqual(self.id_of(post.find_upload_input(self.page)), "media")

    def test_accepts_an_input_without_a_type_filter(self):
        self.page.set_content('<input type="file" id="any">')
        self.assertEqual(self.id_of(post.find_upload_input(self.page)), "any")

    def test_none_when_there_is_no_usable_input(self):
        self.page.set_content('<input type="file" accept=".pdf">')
        self.assertIsNone(post.find_upload_input(self.page))

    def test_can_actually_receive_a_file(self):
        self.page.set_content(GROUP_PAGE)
        post.find_upload_input(self.page).set_input_files(
            {"name": "clip.mp4", "mimeType": "video/mp4", "buffer": b"x"}
        )
        names = self.page.evaluate(
            "() => Object.fromEntries([...document.querySelectorAll('input[type=file]')]"
            ".map(i => [i.id, [...i.files].map(f => f.name)]))"
        )
        self.assertEqual(
            names, {"comment-file": [], "composer-file": ["clip.mp4"], "other-file": []}
        )


if __name__ == "__main__":
    unittest.main()
