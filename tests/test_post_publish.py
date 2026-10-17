import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import login
import post
from tests.test_post_composer import BrowserTestCase

# Records which named element each click landed on, so a test can tell a click on the
# composer's own button from a click that hit the feed behind it
CLICK_LOG = """
<script>
  window.clicks = [];
  document.addEventListener('click', event => {
    const named = event.target.closest('[data-name]');
    clicks.push(named ? named.dataset.name : 'other');
  }, true);
</script>
"""

# A group feed: a post that mentions "chuyển", and a share button under it
FEED = """
<div role="article">
  <a href="#opened-post" data-name="feed-post"><span>Hỏi cách chuyển chế độ lái trên J7, có nút Switch không?</span></a>
  <div role="button" data-name="feed-share"><span>Chia sẻ</span></div>
  <div role="button" data-name="feed-cancel"><span>Hủy</span></div>
</div>
"""


def dialog(inner, name="dialog"):
    return f'<div role="dialog" data-name="{name}" style="position:fixed;inset:20px;background:#fff">{inner}</div>'


def composer(buttons):
    return dialog('<div role="textbox" contenteditable="true"></div>' + buttons, "composer")


LOGIN_COOKIE = {"name": "c_user", "value": "1", "domain": ".facebook.com", "path": "/"}


class QuietBrowserTestCase(BrowserTestCase):
    def quietly(self, func, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return func(*args, **kwargs)

    def clicks(self):
        return self.page.evaluate("clicks")


class ClickButtonTest(QuietBrowserTestCase):
    def click(self, words=post.PRIMARY_WORDS, **timeouts):
        timeouts = {"enable_timeout_ms": 600, "appear_timeout_ms": 300, "poll_ms": 100, **timeouts}
        return self.quietly(post.click_button, self.page, words, **timeouts)

    def test_never_clicks_a_disabled_post_button(self):
        self.page.set_content(
            FEED + composer('<div role="button" aria-disabled="true" data-name="post"><span>Đăng</span></div>') + CLICK_LOG
        )
        self.assertEqual(self.click(), (None, None))
        self.assertEqual(self.clicks(), [])

    def test_waits_for_the_post_button_to_become_enabled(self):
        self.page.set_content(
            FEED
            + composer('<div role="button" aria-disabled="true" id="post" data-name="post"><span>Đăng</span></div>')
            + CLICK_LOG
            + "<script>setTimeout(() => document.getElementById('post').removeAttribute('aria-disabled'), 700)</script>"
        )
        word, element = self.click(enable_timeout_ms=10000)
        self.assertEqual(word, "Đăng")
        self.assertEqual(element.get_attribute("id"), "post")
        self.assertEqual(self.clicks(), ["post"])

    def test_ignores_buttons_in_the_feed_behind_the_composer(self):
        self.page.set_content(
            FEED
            + '<div role="button" data-name="feed-post-button">Đăng</div>'
            + composer('<div role="button" data-name="next">Tiếp</div>')
            + CLICK_LOG
        )
        self.assertEqual(self.click()[0], "Tiếp")
        self.assertEqual(self.clicks(), ["next"])

    def test_post_wins_over_next_and_share(self):
        self.page.set_content(
            composer(
                '<div role="button" data-name="share">Chia sẻ</div>'
                '<div role="button" data-name="next">Tiếp</div>'
                '<div role="button" data-name="post">Đăng</div>'
            )
            + CLICK_LOG
        )
        self.assertEqual(self.click()[0], "Đăng")
        self.assertEqual(self.clicks(), ["post"])

    def test_label_has_to_match_exactly(self):
        self.page.set_content(composer('<div role="button" data-name="story">Đăng lên tin</div>') + CLICK_LOG)
        self.assertEqual(self.click(), (None, None))
        self.assertEqual(self.clicks(), [])

    def test_finds_a_button_named_by_its_aria_label(self):
        self.page.set_content(
            composer('<div role="button" aria-label="Post" data-name="post"><i>➤</i></div>') + CLICK_LOG
        )
        self.assertEqual(self.click()[0], "Post")
        self.assertEqual(self.clicks(), ["post"])

    def test_final_step_does_not_click_next_again(self):
        self.page.set_content(
            dialog('<div role="button" data-name="next">Tiếp</div><div role="button" data-name="post">Đăng</div>')
            + CLICK_LOG
        )
        self.assertEqual(self.click(post.PUBLISH_WORDS)[0], "Đăng")
        self.assertEqual(self.clicks(), ["post"])

    def test_composer_outside_a_dialog_uses_the_whole_page(self):
        self.page.set_content(
            '<div role="textbox" contenteditable="true"></div><div role="button" data-name="post">Post</div>' + CLICK_LOG
        )
        self.assertEqual(self.click()[0], "Post")
        self.assertEqual(self.clicks(), ["post"])


class SwitchProfileTest(QuietBrowserTestCase):
    def switch(self):
        return self.quietly(post.switch_profile_if_asked, self.page, attempts=2, poll_ms=50)

    def test_feed_posts_mentioning_the_word_are_not_clicked(self):
        self.page.set_content(FEED + '<div role="button" data-name="create"><span>Bạn viết gì đi...</span></div>' + CLICK_LOG)
        self.assertFalse(self.switch())
        self.assertEqual(self.clicks(), [])
        self.assertEqual(self.page.evaluate("location.hash"), "")

    def test_nothing_is_clicked_when_the_composer_is_missing_too(self):
        self.page.set_content(FEED + CLICK_LOG)
        self.assertFalse(self.switch())
        self.assertEqual(self.clicks(), [])

    def test_clicks_the_switch_button_in_the_page_banner(self):
        self.page.set_content(
            FEED
            + '<div role="button" data-name="switch"><span>Chuyển ngay</span></div>'
            + '<div role="button" data-name="create"><span>Viết gì đó...</span></div>'
            + CLICK_LOG
        )
        self.assertTrue(self.switch())
        self.assertEqual(self.clicks(), ["switch"])

    def test_clicks_the_switch_button_in_a_dialog(self):
        self.page.set_content(
            FEED
            + dialog('<p>Chuyển sang Hi English để đăng bài</p><div role="button" data-name="switch">Chuyển</div>')
            + CLICK_LOG
        )
        self.assertTrue(self.switch())
        self.assertEqual(self.clicks(), ["switch"])

    def test_clicks_a_dialog_control_that_is_not_a_real_button(self):
        self.page.set_content(dialog('<p>Switch profiles to continue</p><span data-name="switch">Switch</span>') + CLICK_LOG)
        self.assertTrue(self.switch())
        self.assertEqual(self.clicks(), ["switch"])

    def test_dialog_that_only_mentions_switching_is_left_alone(self):
        self.page.set_content(dialog('<p data-name="text">Bạn có thể chuyển trang cá nhân bất cứ lúc nào</p>') + CLICK_LOG)
        self.assertFalse(self.switch())
        self.assertEqual(self.clicks(), [])


class DismissPopupsTest(QuietBrowserTestCase):
    def test_closes_a_prompt_dialog(self):
        self.page.set_content(
            dialog('<p>Trò chuyện trực tiếp với mọi người?</p><div role="button" data-name="later">Lúc khác</div>')
            + CLICK_LOG
        )
        with mock.patch.object(post, "SETTLE_SCALE", 0.01):
            self.assertTrue(self.quietly(post.dismiss_popups, self.page))
        self.assertEqual(self.clicks(), ["later"])

    def test_leaves_the_feed_alone(self):
        self.page.set_content(FEED + CLICK_LOG)
        self.assertFalse(self.quietly(post.dismiss_popups, self.page))
        self.assertEqual(self.clicks(), [])


class WaitForClickedButtonTest(QuietBrowserTestCase):
    """The "next" flow ends in a dialog without a textbox: the clicked button is the signal."""

    SETTINGS = dialog('<div role="button" id="post">Đăng</div>', "settings")

    def test_not_posted_while_the_clicked_button_is_still_there(self):
        self.page.set_content(self.SETTINGS)
        button = self.page.locator("#post").element_handle()
        self.assertFalse(post.wait_for_composer_to_close(self.page, 600, poll_ms=200, button=button))

    def test_posted_once_the_dialog_with_the_button_is_gone(self):
        self.page.set_content(
            self.SETTINGS
            + "<script>setTimeout(() => document.querySelector('[role=dialog]').remove(), 500)</script>"
        )
        button = self.page.locator("#post").element_handle()
        self.assertTrue(post.wait_for_composer_to_close(self.page, 10000, poll_ms=200, button=button))


class FakeFacebookTestCase(QuietBrowserTestCase):
    """Serves local HTML for facebook.com addresses; nothing reaches the network."""

    def setUp(self):
        super().setUp()
        self.html = "<p>Bảng tin</p>"
        self.page.route(
            "**/*", lambda route: route.fulfill(status=200, content_type="text/html; charset=utf-8", body=self.html)
        )
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.dir)
        patcher = mock.patch.object(post, "DEBUG_DIR", os.path.join(self.dir, "debug"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def shot(self, kind):
        """Whether the screenshot of that kind was saved (and its latest_ copy)."""
        return os.path.exists(os.path.join(post.DEBUG_DIR, f"latest_{kind}.png"))

    def log_in(self):
        self.page.context.add_cookies([LOGIN_COOKIE])


class SessionTest(FakeFacebookTestCase):
    def test_logged_in_on_the_feed(self):
        self.log_in()
        self.page.goto("https://www.facebook.com/")
        self.assertFalse(post.is_logged_out(self.page))

    def test_logged_out_without_the_login_cookie(self):
        self.page.goto("https://www.facebook.com/")
        self.assertTrue(post.is_logged_out(self.page))

    def test_logged_out_when_sent_to_the_login_or_checkpoint_page(self):
        self.log_in()
        for address in ("https://www.facebook.com/login/?next=x", "https://www.facebook.com/checkpoint/1501092823525282/"):
            with self.subTest(address=address):
                self.page.goto(address)
                self.assertTrue(post.is_logged_out(self.page))

    def test_logged_out_when_the_page_asks_for_a_password(self):
        self.log_in()
        self.html = '<form><input name="email"><input name="pass" type="password"></form>'
        self.page.goto("https://www.facebook.com/groups/abc")
        self.assertTrue(post.is_logged_out(self.page))

    def test_login_script_only_accepts_a_finished_login(self):
        self.assertFalse(login.is_logged_in(self.page.context))
        self.log_in()
        self.assertTrue(login.is_logged_in(self.page.context))

    def test_login_script_saves_the_session(self):
        self.log_in()
        login.save_state(self.page.context, "state.json")
        with open("state.json", encoding="utf-8") as handle:
            self.assertEqual([cookie["name"] for cookie in json.load(handle)["cookies"]], ["c_user"])
        self.assertFalse(os.path.exists("state.json.tmp"))

    def test_session_is_saved_again_after_a_post(self):
        self.log_in()
        self.page.context.add_cookies([dict(LOGIN_COOKIE, name="xs", value="renewed")])
        post.save_session(self.page.context, "state.json")
        with open("state.json", encoding="utf-8") as handle:
            saved = {cookie["name"]: cookie["value"] for cookie in json.load(handle)["cookies"]}
        self.assertEqual(saved, {"c_user": "1", "xs": "renewed"})

    def test_logged_out_browser_never_replaces_the_saved_session(self):
        with open("state.json", "w", encoding="utf-8") as handle:
            handle.write("phiên cũ")
        post.save_session(self.page.context, "state.json")
        with open("state.json", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "phiên cũ")


# A group page whose composer behaves like Facebook's: the post button is disabled
# for a while, and posting closes the dialog. %s is how the button reacts.
GROUP = FEED + """
<div role="button" id="create"><span>Bạn viết gì đi...</span></div>
<script>
  window.posted = [];
  document.getElementById('create').onclick = () => {
    const box = document.createElement('div');
    box.setAttribute('role', 'dialog');
    box.style = 'position:fixed;inset:20px;background:#fff';
    box.innerHTML = '<div role="textbox" contenteditable="true" style="min-height:40px"></div>'
      + '<div role="button" id="post" aria-disabled="true">Đăng</div>';
    document.body.append(box);
    const button = document.getElementById('post');
    button.onclick = () => {
      if (button.getAttribute('aria-disabled') === 'true') return;
      posted.push(box.querySelector('[role=textbox]').innerText.trim());
      %s
    };
    setTimeout(() => button.removeAttribute('aria-disabled'), ENABLE_AFTER_MS);
  };
</script>
"""


class PublishTest(FakeFacebookTestCase):
    URL = "https://www.facebook.com/groups/jaecoo"

    def publish(self, enable_after_ms=600, on_post="box.remove();", dry_run=False, **limits):
        self.html = (GROUP % on_post).replace("ENABLE_AFTER_MS", str(enable_after_ms))
        limits = {"SETTLE_SCALE": 0.01, "BUTTON_ENABLE_TIMEOUT_MS": 10000, "POST_TIMEOUT_MS": 10000, **limits}
        with contextlib.ExitStack() as stack:
            for name, value in limits.items():
                stack.enter_context(mock.patch.object(post, name, value))
            stack.enter_context(mock.patch.object(post, "find_post_url", return_value=(None, None)))
            return self.quietly(post.publish, self.page, "Xin chào cả nhà", page_url=self.URL, dry_run=dry_run)

    def test_post_is_published_once_the_button_is_enabled(self):
        self.log_in()
        self.assertTrue(self.publish())
        self.assertEqual(self.page.evaluate("posted"), ["Xin chào cả nhà"])
        # The feed post that mentions "chuyển" was not opened on the way
        self.assertEqual(self.page.url, self.URL)
        self.assertTrue(self.shot("result"))

    def test_dry_run_writes_the_post_but_does_not_publish_it(self):
        self.log_in()
        self.assertTrue(self.publish(dry_run=True))
        self.assertEqual(self.page.evaluate("posted"), [])
        self.assertTrue(self.shot("dry_run"))
        self.assertFalse(self.shot("result"))

    def test_fails_when_the_post_button_never_becomes_enabled(self):
        self.log_in()
        self.assertFalse(self.publish(enable_after_ms=3600000, BUTTON_ENABLE_TIMEOUT_MS=800))
        self.assertEqual(self.page.evaluate("posted"), [])
        self.assertTrue(self.shot("post_button"))
        self.assertFalse(self.shot("result"))

    def test_fails_when_the_composer_stays_open_after_the_click(self):
        self.log_in()
        self.assertFalse(self.publish(on_post="/* Facebook showed an error and kept the dialog */", POST_TIMEOUT_MS=500))
        self.assertTrue(self.shot("not_posted"))
        self.assertFalse(self.shot("result"))

    def test_expired_session_is_reported_as_such(self):
        with self.assertRaises(post.SessionExpired):
            self.publish()
        self.assertTrue(self.shot("session"))
        self.assertEqual(self.page.evaluate("typeof posted === 'undefined' ? [] : posted"), [])


if __name__ == "__main__":
    unittest.main()
