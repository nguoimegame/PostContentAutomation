import contextlib
import io
import os
import time
import unittest
from unittest import mock

import post
from tests.test_post_publish import FakeFacebookTestCase, FEED, GROUP, LOGIN_COOKIE

TARGET = "https://www.facebook.com/groups/jaecoo"


def article(text, href=None):
    link = f'<a href="{href}">12 phút</a>' if href else ""
    return f'<div role="article"><span>{text}</span>{link}</div>'


class CleanPostUrlTest(unittest.TestCase):
    def test_tracking_parameters_are_dropped(self):
        self.assertEqual(
            post.clean_post_url("/groups/1/posts/22/?__cft__[0]=abc&__tn__=x"),
            "https://www.facebook.com/groups/1/posts/22/",
        )

    def test_old_style_links_keep_their_identifying_parameters(self):
        self.assertEqual(
            post.clean_post_url("https://www.facebook.com/permalink.php?story_fbid=5&id=6&__cft__=z"),
            "https://www.facebook.com/permalink.php?story_fbid=5&id=6",
        )

    def test_links_that_are_not_posts_are_rejected(self):
        for href in ("/hienglish", "/groups/1/", "https://example.com/posts/1", "", None, "/permalink.php?x=1"):
            self.assertIsNone(post.clean_post_url(href), href)

    def test_snippet_ignores_case_and_spacing(self):
        self.assertEqual(post.snippet("  Xin   CHÀO\ncả nhà "), "xin chào cả nhà")


class FindPostUrlTest(FakeFacebookTestCase):
    def find(self, html, text="Xin chào cả nhà, đây là bài mới", attempts=2):
        self.html = html
        self.log_in()
        with mock.patch.object(post, "SETTLE_SCALE", 0.01):
            return self.quietly(post.find_post_url, self.page, text, TARGET, attempts)

    def test_finds_the_link_of_the_article_with_the_same_text(self):
        html = article("Bài cũ không liên quan", "/groups/1/posts/1/") + article(
            "Xin chào cả nhà, đây là bài mới  ", "/groups/1/posts/2/?__cft__[0]=abc"
        )
        self.assertEqual(self.find(html), ("https://www.facebook.com/groups/1/posts/2/", True))

    def test_a_post_that_is_not_in_the_feed_is_reported_as_not_found(self):
        self.assertEqual(self.find(article("Bài cũ", "/groups/1/posts/1/")), (None, False))

    def test_a_live_post_without_a_usable_link_is_still_confirmed(self):
        self.assertEqual(self.find(article("Xin chào cả nhà, đây là bài mới", "/hienglish")), (None, True))

    def test_a_post_without_text_cannot_be_recognised(self):
        self.assertEqual(self.find(article("Bài cũ", "/groups/1/posts/1/"), text="  "), (None, None))

    def test_publish_records_the_link(self):
        self.html = (
            article("Xin chào cả nhà", "/groups/1/posts/9/") + GROUP % "box.remove();"
        ).replace("ENABLE_AFTER_MS", "300")
        self.log_in()
        with mock.patch.object(post, "SETTLE_SCALE", 0.01), mock.patch.object(post, "BUTTON_ENABLE_TIMEOUT_MS", 10000):
            post.reset_progress()
            self.assertTrue(self.quietly(post.publish, self.page, "Xin chào cả nhà", page_url=TARGET))
        self.assertEqual(post._progress["post_url"], "https://www.facebook.com/groups/1/posts/9/")
        self.assertIs(post._progress["verified"], True)
        self.assertTrue(post._progress["clicked"])

    def test_a_failure_in_the_lookup_does_not_fail_the_post(self):
        self.html = GROUP.replace("ENABLE_AFTER_MS", "300") % "box.remove();"
        self.log_in()
        with mock.patch.object(post, "SETTLE_SCALE", 0.01), mock.patch.object(post, "BUTTON_ENABLE_TIMEOUT_MS", 10000), \
                mock.patch.object(post, "find_post_url", side_effect=RuntimeError("hỏng")):
            post.reset_progress()
            self.assertTrue(self.quietly(post.publish, self.page, "Xin chào", page_url=TARGET))
        self.assertIsNone(post._progress["post_url"])


class ProgressTest(FakeFacebookTestCase):
    """What a failure says about whether the post may be live."""

    def publish(self, html, **limits):
        self.html = html.replace("ENABLE_AFTER_MS", "300")
        self.log_in()
        limits = {"SETTLE_SCALE": 0.01, "BUTTON_ENABLE_TIMEOUT_MS": 800, "POST_TIMEOUT_MS": 500, **limits}
        post.reset_progress()
        with mock.patch.multiple(post, find_post_url=mock.Mock(return_value=(None, None)), **limits):
            return self.quietly(post.publish, self.page, "Xin chào", page_url=TARGET)

    def test_nothing_was_pressed_when_the_composer_cannot_be_found(self):
        self.assertFalse(self.publish(FEED))
        self.assertFalse(post._progress["clicked"])

    def test_nothing_was_pressed_when_the_post_button_never_works(self):
        self.assertFalse(self.publish(GROUP.replace("ENABLE_AFTER_MS", "3600000") % "box.remove();"))
        self.assertFalse(post._progress["clicked"])

    def test_the_button_was_pressed_when_the_dialog_stays_open(self):
        self.assertFalse(self.publish(GROUP % "/* an error, the dialog stays */"))
        self.assertTrue(post._progress["clicked"])


# A composer with a file input that takes several files, like Facebook's
UPLOAD = FEED + """
<div role="button" id="create"><span>Bạn viết gì đi...</span></div>
<script>
  window.posted = [];
  document.getElementById('create').onclick = () => {
    const box = document.createElement('div');
    box.setAttribute('role', 'dialog');
    box.style = 'position:fixed;inset:20px;background:#fff';
    box.innerHTML = '<div role="textbox" contenteditable="true" style="min-height:40px"></div>'
      + '<input type="file" id="files" accept="image/*,video/*" multiple>'
      + '<div role="button" id="post">Đăng</div>';
    document.body.append(box);
    document.getElementById('post').onclick = () => {
      posted.push(Array.from(document.getElementById('files').files).map(f => f.name));
      box.remove();
    };
  };
</script>
"""


class SeveralFilesTest(FakeFacebookTestCase):
    def make_files(self, *names):
        paths = []
        for name in names:
            path = os.path.join(self.dir, name)
            with open(path, "wb") as handle:
                handle.write(b"x")
            paths.append(path)
        return paths

    def publish(self, image_path):
        self.html = UPLOAD
        self.log_in()
        with mock.patch.multiple(post, SETTLE_SCALE=0.01, find_post_url=mock.Mock(return_value=(None, None))):
            return self.quietly(post.publish, self.page, "Xin chào", page_url=TARGET, image_path=image_path)

    def test_every_file_is_attached_to_the_one_post(self):
        first, second = self.make_files("a.png", "b.png")
        self.assertTrue(self.publish([first, second]))
        self.assertEqual(self.page.evaluate("posted"), [["a.png", "b.png"]])

    def test_a_pipe_separated_text_works_too(self):
        first, second = self.make_files("a.png", "b.png")
        self.assertTrue(self.publish(f"{first}|{second}"))
        self.assertEqual(self.page.evaluate("posted"), [["a.png", "b.png"]])

    def test_a_single_path_still_works(self):
        (first,) = self.make_files("a.png")
        self.assertTrue(self.publish(first))
        self.assertEqual(self.page.evaluate("posted"), [["a.png"]])

    def test_a_missing_file_stops_the_post(self):
        (first,) = self.make_files("a.png")
        self.assertFalse(self.publish([first, os.path.join(self.dir, "khong-co.png")]))
        self.assertEqual(self.page.evaluate("posted"), [])

    def test_make_post_names_the_missing_file_before_opening_a_browser(self):
        post.reset_progress()
        with mock.patch.object(post, "open_session", side_effect=AssertionError("không được mở trình duyệt")):
            self.assertFalse(self.quietly(post.make_post, "x", image_path="khong-co.png", state_path="state.json"))
        self.assertTrue(post._progress["permanent"])


class SelfTestTest(FakeFacebookTestCase):
    def run_selftest(self, html, logged_in=True):
        self.html = html
        if logged_in:
            self.log_in()
        with mock.patch.object(post, "SETTLE_SCALE", 0.01):
            return self.quietly(post.selftest, self.page, TARGET)

    def test_a_working_page_passes_without_posting(self):
        report = self.run_selftest(GROUP.replace("ENABLE_AFTER_MS", "300") % "box.remove();")
        self.assertTrue(report["ok"], report["problems"])
        self.assertTrue(report["composer_opened"])
        self.assertGreater(sum(report["create_post"].values()), 0)
        self.assertTrue(report["buttons"]["Đăng"])
        self.assertEqual(self.page.evaluate("posted"), [])
        self.assertTrue(self.shot("selftest"))

    def test_a_page_without_the_composer_button_reports_the_problem(self):
        report = self.run_selftest(FEED)
        self.assertFalse(report["ok"])
        self.assertFalse(report["composer_opened"])
        self.assertTrue(any("CREATE_POST_SELECTORS" in problem for problem in report["problems"]))

    def test_a_logged_out_browser_is_reported(self):
        report = self.run_selftest(FEED, logged_in=False)
        self.assertFalse(report["logged_in"])
        self.assertFalse(report["ok"])


ODD_COMPOSER = FEED + """
<div role="button" id="create"><span>Bạn viết gì đi...</span></div>
<script>
  document.getElementById('create').onclick = () => {
    const box = document.createElement('div');
    box.setAttribute('role', 'dialog');
    box.style = 'position:fixed;inset:20px;background:#fff';
    box.innerHTML = '<div role="textbox" contenteditable="true" aria-label="Tạo bài viết công khai" style="min-height:40px">'
      + 'Đoạn văn rất dài của người dùng không được gửi đi đâu cả vì nó quá dài</div>'
      + '<a href="https://www.facebook.com/profile.php?id=123">Hồ sơ</a><img src="https://cdn.example/avatar.jpg">'
      + '<div role="button" aria-label="Gửi đi" data-secret="x">Gửi đi</div>';
    document.body.append(box);
  };
</script>
"""


class DomSnapshotTest(FakeFacebookTestCase):
    def run_selftest(self, html):
        self.html = html
        self.log_in()
        with mock.patch.object(post, "SETTLE_SCALE", 0.01):
            return self.quietly(post.selftest, self.page, TARGET)

    def saved(self, report):
        with open(report["dom_file"], encoding="utf-8") as handle:
            return handle.read()

    def test_a_working_page_saves_no_structure(self):
        report = self.run_selftest(GROUP.replace("ENABLE_AFTER_MS", "300") % "box.remove();")
        self.assertTrue(report["ok"])
        self.assertNotIn("dom_file", report)

    def test_a_broken_composer_is_saved_without_links_styles_and_long_texts(self):
        report = self.run_selftest(ODD_COMPOSER)
        self.assertFalse(report["ok"])
        self.assertEqual(report["dom_from"], "composer")
        saved = self.saved(report)
        self.assertIn('<div role="textbox" aria-label="Tạo bài viết công khai" contenteditable="true">', saved)
        self.assertIn('aria-label="Gửi đi"', saved)
        self.assertIn("<img/>", saved)
        for secret in ("href", "profile.php", "style=", "avatar.jpg", "data-secret", "không được gửi đi đâu cả vì nó quá dài"):
            self.assertNotIn(secret, saved)
        self.assertIn("Đoạn văn rất dài của người dù", saved)  # cut to 30 characters

    def test_without_a_composer_the_feed_text_is_not_kept(self):
        report = self.run_selftest(FEED)
        self.assertEqual(report["dom_from"], "page")
        saved = self.saved(report)
        self.assertNotIn("Hỏi cách chuyển chế độ lái", saved)
        self.assertIn("Chia sẻ", saved)  # the text of a button is what finding a button needs
        self.assertNotIn("href", saved)

    def test_the_saved_markup_is_escaped(self):
        report = self.run_selftest(FEED + '<div role="button" aria-label="a&quot;b<c">x &lt;y&gt;</div>')
        saved = self.saved(report)
        self.assertIn("a&quot;b&lt;c", saved)
        self.assertIn("x &lt;y&gt;", saved)

    def test_the_path_is_printed_for_the_person_reading_the_report(self):
        report = self.run_selftest(FEED)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            post.print_selftest(report)
        self.assertIn(report["dom_file"], out.getvalue())


class DebugFolderTest(FakeFacebookTestCase):
    def test_each_screenshot_is_kept_and_the_latest_is_copied(self):
        self.page.set_content("<p>x</p>")
        first = self.quietly(post.debug_shot, self.page, "result")
        self.assertTrue(os.path.dirname(first).endswith("debug"))
        self.assertTrue(os.path.exists(first))
        self.assertTrue(self.shot("result"))

    def test_old_screenshots_are_removed_but_the_latest_copies_stay(self):
        os.makedirs(post.DEBUG_DIR)
        for i in range(8):
            with open(os.path.join(post.DEBUG_DIR, f"20260101-00000{i}_result.png"), "wb") as handle:
                handle.write(b"x")
        with open(os.path.join(post.DEBUG_DIR, "latest_result.png"), "wb") as handle:
            handle.write(b"x")
        post.prune_debug_files(keep=3)
        names = sorted(os.listdir(post.DEBUG_DIR))
        self.assertEqual(names, ["20260101-000005_result.png", "20260101-000006_result.png",
                                 "20260101-000007_result.png", "latest_result.png"])

    def test_only_screenshots_from_this_run_are_listed(self):
        os.makedirs(post.DEBUG_DIR)
        old = os.path.join(post.DEBUG_DIR, "20260101-000000_result.png")
        with open(old, "wb") as handle:
            handle.write(b"x")
        os.utime(old, (1, 1))
        started = time.time()
        self.page.set_content("<p>x</p>")
        new = self.quietly(post.debug_shot, self.page, "modal")
        self.assertEqual(post.new_debug_images(started), [new])


if __name__ == "__main__":
    unittest.main()
