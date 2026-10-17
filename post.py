import os
import re
import sys
import time
import json
import shutil
import argparse
import contextlib
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit
from playwright.sync_api import Error as PlaywrightError, sync_playwright

import history
import spam
import store

# Session and settings live next to the scripts, whatever folder the caller runs from
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(BASE_DIR, "state.json")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
HISTORY_PATH = history.HISTORY_PATH
# Screenshots for finding out why a post failed; the newest of each kind is also kept as latest_<kind>.png
DEBUG_DIR = os.path.join(BASE_DIR, "debug")
KEEP_DEBUG_FILES = 60
# A line on stdout that the app and the daemon read to learn where the post ended up
POST_URL_MARK = "POST_URL="
POST_VERIFIED_MARK = "POST_VERIFIED="

VIDEO_EXTENSIONS = (".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".wmv")
VIDEO_UPLOAD_TIMEOUT_MS = 10 * 60 * 1000
# A text or image post still sitting in the composer after this long did not go through
POST_TIMEOUT_MS = 2 * 60 * 1000
# How long the post button may stay greyed out while an attached image is being processed
BUTTON_ENABLE_TIMEOUT_MS = 2 * 60 * 1000
BUTTON_APPEAR_TIMEOUT_MS = 10 * 1000

# Exit code telling the caller that Facebook wants a new login, as opposed to a failed post
EXIT_SESSION_EXPIRED = 2
# The post failed before the final post button was pressed, so nothing is on Facebook and trying again is safe
EXIT_RETRYABLE = 4
# Refused without opening a browser: a duplicate of a recent post, or too many posts lately (see spam.py)
EXIT_BLOCKED = 5

# How far the current make_post/publish call got, so a failure can say whether the post may be live
_progress = {"clicked": False, "permanent": False, "post_url": None, "verified": None}

def reset_progress():
    _progress.update(clicked=False, permanent=False, post_url=None, verified=None)

# Tests shrink the fixed pauses that give Facebook time to render
SETTLE_SCALE = 1.0

NEXT_WORDS = ["Tiếp", "Next"]
PUBLISH_WORDS = ["Đăng", "Post", "Chia sẻ", "Share", "Đăng bài"]
# The composer shows either the post button or, on some pages, a "next" step first
PRIMARY_WORDS = ["Đăng", "Post"] + NEXT_WORDS + ["Chia sẻ", "Share", "Đăng bài"]
SWITCH_WORDS = ["Chuyển ngay", "Switch Now", "Switch now", "Chuyển", "Switch"]
DISMISS_WORDS = ["Lúc khác", "Not now", "Bỏ qua", "Skip", "Hủy", "Cancel"]


class SessionExpired(Exception):
    """Facebook no longer accepts the saved session (logged out, or a checkpoint)."""


def use_utf8_output():
    # Redirected output uses the Windows code page, which cannot encode Vietnamese and would crash print()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

def settle(page, ms):
    page.wait_for_timeout(int(ms * SETTLE_SCALE))

def debug_shot(page, kind):
    """Save a screenshot as debug/<time>_<kind>.png (and debug/latest_<kind>.png). Returns its path."""
    os.makedirs(DEBUG_DIR, exist_ok=True)
    path = os.path.join(DEBUG_DIR, f"{datetime.now():%Y%m%d-%H%M%S}_{kind}.png")
    page.screenshot(path=path)
    try:
        shutil.copyfile(path, os.path.join(DEBUG_DIR, f"latest_{kind}.png"))
        prune_debug_files()
    except OSError:
        pass
    print(f"Da luu anh {path}")
    return path

def debug_files():
    """The time-stamped screenshots in the debug folder, oldest first."""
    try:
        names = [name for name in os.listdir(DEBUG_DIR) if re.match(r"^\d{8}-\d{6}_.+\.png$", name)]
    except OSError:
        return []
    return sorted(os.path.join(DEBUG_DIR, name) for name in names)

def prune_debug_files(keep=None):
    files = debug_files()
    for path in files[: max(0, len(files) - (keep or KEEP_DEBUG_FILES))]:
        try:
            os.remove(path)
        except OSError:
            pass

def new_debug_images(since):
    """The screenshots saved since `since` (time.time())."""
    return [path for path in debug_files() if os.path.getmtime(path) >= since - 1]

def is_video(path):
    return bool(path) and os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS

def wait_for_composer_to_close(page, timeout_ms, poll_ms=2000, button=None):
    # The composer is the dialog holding the post textbox; it stays open while a video is uploading.
    # `button` is the post button that was clicked (an element handle): on pages with a "next" step it
    # lives in a follow-up dialog without a textbox, so it has to be gone as well.
    composer = page.locator('div[role="dialog"]:has(div[role="textbox"])')
    waited = 0
    while True:
        still_open = any(composer.nth(i).is_visible() for i in range(composer.count()))
        if not still_open and button is not None:
            still_open = button.is_visible()
        if not still_open:
            return True
        if waited >= timeout_ms:
            return False
        page.wait_for_timeout(poll_ms)
        waited += poll_ms

# Selectors matching "What's on your mind", "Bạn đang nghĩ gì thế", or page/group post creation buttons
CREATE_POST_SELECTORS = [
    'div[role="button"]:has-text("What\'s on your mind")',
    'div[role="button"]:has-text("Bạn đang nghĩ gì thế")',
    'div[role="button"]:has-text("Write something...")',
    'div[role="button"]:has-text("Viết gì đó...")',
    'div[role="button"]:has-text("Bạn viết gì đi")',
    'div[role="button"]:has-text("Create post")',
    'div[role="button"]:has-text("Tạo bài viết")',
    'span:has-text("What\'s on your mind")',
    'span:has-text("Bạn đang nghĩ gì thế")',
    'span:has-text("Write something...")',
    'span:has-text("Viết gì đó...")',
    'span:has-text("Bạn viết gì đi")',
    'span:has-text("Create post")',
    'span:has-text("Tạo bài viết")',
    'span:has-text("đang nghĩ gì thế")',
    '//div[@role="button"][contains(., "What\'s on your mind")]',
    '//div[@role="button"][contains(., "Bạn đang nghĩ gì")]',
    '//div[@role="button"][contains(., "Write something")]',
    '//div[@role="button"][contains(., "Viết gì đó")]',
    '//div[@role="button"][contains(., "Bạn viết gì đi")]',
    '//div[@role="button"][contains(., "Create post")]',
    '//div[@role="button"][contains(., "Tạo bài viết")]'
]

TEXTBOX_SELECTORS = [
    'div[role="textbox"]',
    'div[contenteditable="true"]',
    'div[aria-label*="What\'s on your mind"]',
    'div[aria-label*="Bạn đang nghĩ gì thế"]',
    'div[aria-label*="Write something"]',
    'div[aria-label*="Viết gì đó"]'
]

def first_visible(page, selectors):
    for selector in selectors:
        try:
            matches = page.locator(selector)
            for i in range(matches.count()):
                if matches.nth(i).is_visible():
                    return matches.nth(i), selector
        except Exception:
            continue
    return None, None

def find_create_post_button(page):
    return first_visible(page, CREATE_POST_SELECTORS)[0]

def find_composer_textbox(page):
    # Feed and group pages also have comment boxes with role="textbox", and they come first in the DOM.
    # The post composer is the one inside a dialog, so look there before falling back to the whole page.
    in_dialog = ['div[role="dialog"] ' + selector for selector in TEXTBOX_SELECTORS]
    return first_visible(page, in_dialog + TEXTBOX_SELECTORS)[0]

def find_upload_input(page):
    # Same reason as the textbox: comment boxes have their own file inputs outside the composer dialog
    for selector in ('div[role="dialog"] input[type="file"]', 'input[type="file"]'):
        file_inputs = page.locator(selector)
        for i in range(file_inputs.count()):
            accept_attr = file_inputs.nth(i).get_attribute("accept") or ""
            # The correct file input typically accepts image/video or has empty/broad accept
            if "image" in accept_attr or "video" in accept_attr or not accept_attr:
                return file_inputs.nth(i)
    return None

def visible_dialogs(page):
    """The dialogs on screen, the post composer (the one with a textbox) first."""
    dialogs = page.locator('div[role="dialog"]')
    found = [dialogs.nth(i) for i in range(dialogs.count()) if dialogs.nth(i).is_visible()]
    return sorted(found, key=lambda dialog: dialog.locator('div[role="textbox"]').count() == 0)

def button_scopes(page):
    # The feed behind the composer has "Chia sẻ"/"Share" buttons of its own, so only dialogs are searched.
    # The whole page is used only for a composer that is not in a dialog.
    return visible_dialogs(page) or [page]

def find_button(scopes, words):
    """First visible button in `scopes` whose label is exactly one of `words`; earlier words win.

    Returns (locator, word), or (None, None). A disabled button is returned too.
    """
    for word in words:
        for scope in scopes:
            for matches in (
                scope.get_by_role("button", name=word, exact=True),
                scope.locator(f'[aria-label="{word}"]'),
            ):
                try:
                    for i in range(matches.count()):
                        if matches.nth(i).is_visible():
                            return matches.nth(i), word
                except Exception:
                    continue
    return None, None

def find_exact_text(scopes, words):
    """First visible element in `scopes` whose text is exactly one of `words`; earlier words win."""
    for word in words:
        for scope in scopes:
            matches = scope.get_by_text(word, exact=True)
            for i in range(matches.count()):
                if matches.nth(i).is_visible():
                    return matches.nth(i), word
    return None, None

def click_button(page, words, enable_timeout_ms=BUTTON_ENABLE_TIMEOUT_MS,
                 appear_timeout_ms=BUTTON_APPEAR_TIMEOUT_MS, poll_ms=500):
    """Click the first dialog button labelled one of `words`, once it is there and enabled.

    Returns (word, element) for the button that was clicked, or (None, None) if no such button
    showed up or it stayed disabled. Clicking a disabled button does nothing, so it is never forced.
    """
    waited = 0
    seen = None
    while True:
        button, word = find_button(button_scopes(page), words)
        if button is not None:
            seen = word
            try:
                if button.is_enabled(timeout=1000):
                    element = button.element_handle(timeout=1000)
                    print(f"Dang nhan nut '{word}'...")
                    try:
                        element.click(timeout=5000)
                    except Exception:
                        # Facebook sometimes leaves a transparent layer over a button that is enabled
                        element.click(force=True, timeout=3000)
                    return word, element
            except Exception as e:
                # The dialog was re-rendered between the lookup and the click; look again
                print(f"Chua nhan duoc nut '{word}': {e}")
        if waited >= (enable_timeout_ms if seen else appear_timeout_ms):
            if seen:
                print(f"Nut '{seen}' van bi vo hieu hoa sau {waited // 1000} giay (tep dinh kem chua tai xong?).")
            else:
                print(f"Khong tim thay nut nao trong so: {', '.join(words)}.")
            return None, None
        page.wait_for_timeout(poll_ms)
        waited += poll_ms

def find_switch_button(page):
    """The button Facebook shows when a Page has to be acted on under its own profile.

    Labels are matched exactly. A loose text match also hits posts in the feed that merely
    contain "chuyển" or "switch", and clicking those opens the post instead.
    """
    dialogs = visible_dialogs(page)
    button, _ = find_button(dialogs + [page], SWITCH_WORDS)
    if button is not None:
        return button
    # Inside a dialog the control is not always a real button; the text still has to match exactly
    return find_exact_text(dialogs, SWITCH_WORDS)[0]

def switch_profile_if_asked(page, attempts=5, poll_ms=2000):
    """Click the profile switch button if Facebook shows one. Returns True if it was clicked."""
    for attempt in range(attempts):
        button = find_switch_button(page)
        if button is not None:
            try:
                print("Phat hien nut chuyen doi profile. Dang nhan...")
                button.click(force=True, timeout=5000)
                return True
            except Exception as e:
                print(f"Loi khi nhan nut chuyen doi profile: {e}")
        elif find_create_post_button(page) is not None:
            print("Da o dung profile hoac nut tao bai viet da san sang. Khong can chuyen doi.")
            return False
        page.wait_for_timeout(poll_ms)
    return False

def dismiss_popups(page):
    """Close a prompt Facebook put in the way (e.g. "Trò chuyện trực tiếp với mọi người" -> "Lúc khác")."""
    dialogs = visible_dialogs(page)
    button, word = find_button(dialogs, DISMISS_WORDS)
    if button is None:
        button, word = find_exact_text(dialogs, DISMISS_WORDS)
    if button is None:
        return False
    try:
        print(f"Phat hien va nhan nut bo qua hop thoai phu: '{word}'")
        button.click(force=True, timeout=2000)
        settle(page, 1500)
        return True
    except Exception:
        return False

def has_login_cookie(context):
    # c_user holds the account id and only exists while logged in
    return any(cookie["name"] == "c_user" for cookie in context.cookies("https://www.facebook.com"))

def is_logged_out(page):
    path = urlsplit(page.url).path
    if path.startswith(("/login", "/checkpoint")):
        return True
    if not has_login_cookie(page.context):
        return True
    password = page.locator('input[name="pass"]')
    return any(password.nth(i).is_visible() for i in range(password.count()))

def save_session(context, state_path):
    # Facebook renews its cookies as the session is used; keeping the new ones keeps the login alive
    if not has_login_cookie(context):
        return
    temp_path = state_path + ".tmp"
    try:
        context.storage_state(path=temp_path)
        os.replace(temp_path, state_path)
    except Exception as e:
        print(f"Khong luu lai duoc phien dang nhap: {e}")

def require_login(page):
    if is_logged_out(page):
        try:
            debug_shot(page, "session")
        except Exception:
            pass
        raise SessionExpired("Facebook yeu cau dang nhap lai hoac xac minh (checkpoint).")

def media_files(image_path):
    """The attachment paths as a list; `image_path` may be one path, "a|b" or a list."""
    return store.split_media(image_path)

def check_media(image_path):
    """Raises ValueError naming the first attachment that is missing."""
    for path in media_files(image_path):
        if not os.path.isfile(path):
            raise ValueError(f"Khong tim thay tep anh/video: {path}")

@contextlib.contextmanager
def open_session(state_path, headless):
    """A logged-in browser page. Raises SessionExpired when there is no saved session."""
    if not os.path.exists(state_path):
        # No session is the same problem for the caller as an expired one: a login is needed
        raise SessionExpired(f"Khong tim thay tep {state_path}. Vui long chay python login.py truoc de dang nhap.")
    with sync_playwright() as p:
        print(f"Dang mo trinh duyet (Chế độ: {'Ẩn danh/Headless' if headless else 'Hiển thị/Headful'})...")
        browser = p.chromium.launch(headless=headless)
        try:
            # Create context restoring the saved state
            context = browser.new_context(storage_state=state_path)
            yield context, context.new_page()
        finally:
            browser.close()

def make_post(text, page_url=None, headless=False, image_path=None, state_path=None, dry_run=False, account=""):
    """Publish a post. Returns True once Facebook has taken it; raises SessionExpired if logged out.

    With `dry_run` the post is written but never published. `image_path` may name several files
    ("a.jpg|b.jpg" or a list). `account` picks which saved login to use.
    A failure leaves `_progress["clicked"]` telling whether the post button was pressed.
    """
    reset_progress()
    if state_path is None:
        state_path = STATE_PATH if account in ("", "default", None) else store.state_path_for(BASE_DIR, account)
    try:
        check_media(image_path)
        with open_session(state_path, headless) as (context, page):
            posted = publish(page, text, page_url=page_url, image_path=image_path, dry_run=dry_run)
            if posted and not dry_run:
                save_session(context, state_path)
            return posted
    except PlaywrightError as error:
        # The network dropped or the page took too long: report it like any other failed post
        print(f"Loi trinh duyet hoac mang: {error}")
        return False
    except ValueError as error:
        print(f"Loi: {error}")
        _progress["permanent"] = True
        return False

POST_LINK_SELECTOR = (
    'a[href*="/posts/"], a[href*="/permalink/"], a[href*="/permalink.php"], a[href*="story_fbid"], '
    'a[href*="/videos/"], a[href*="/reel/"], a[href*="/photo"]'
)
# Query parameters that identify a post on the old-style links; everything else is tracking
KEPT_QUERY = ("story_fbid", "id", "fbid", "set", "v")

def snippet(text, length=40):
    """The start of a text, as it is compared with what the page shows (whitespace and case ignored)."""
    return re.sub(r"\s+", " ", text or "").strip().lower()[:length]

def clean_post_url(href):
    """A post link without tracking parameters, or None if `href` does not point to a Facebook post."""
    if not href:
        return None
    parts = urlsplit(urljoin("https://www.facebook.com/", href))
    host = (parts.hostname or "").lower()
    if host != "facebook.com" and not host.endswith(".facebook.com"):
        return None
    path = parts.path
    old_style = path.endswith(("/permalink.php", "/photo.php", "/photo", "/story.php"))
    markers = ("/posts/", "/permalink/", "/videos/", "/reel/", "/photo", "/permalink.php", "/story.php")
    if not any(marker in path for marker in markers):
        return None
    query = ""
    if old_style:
        kept = [(key, value) for key, value in parse_qsl(parts.query) if key in KEPT_QUERY]
        if not kept:
            return None
        query = "?" + urlencode(kept)
    return f"https://{host}{path}{query}"

def find_post_url(page, text, target_url, attempts=3):
    """Look for the post just published at the top of `target_url`.

    Returns (url, verified): the link of the article that starts with the same text, and True; or
    (None, False) when no such article is there (a group that holds posts for approval, or Facebook
    being slow); (None, None) when the post has no text to recognise it by.
    """
    wanted = snippet(text)
    if not wanted:
        return None, None
    for attempt in range(attempts):
        # Facebook usually adds a new post to the open feed by itself; reload only when it did not
        if attempt:
            page.goto(target_url)
        settle(page, 3000 if attempt == 0 else 8000)
        articles = page.locator('div[role="article"]')
        for i in range(min(articles.count(), 6)):
            article = articles.nth(i)
            try:
                body = snippet(article.inner_text(), 4000)
            except Exception:
                continue
            if wanted not in body:
                continue
            links = article.locator(POST_LINK_SELECTOR)
            for j in range(links.count()):
                link = clean_post_url(links.nth(j).get_attribute("href"))
                if link:
                    return link, True
            # The post is there but its link is not found: it is live, only the address is unknown
            return None, True
    return None, False

def publish(page, text, page_url=None, image_path=None, dry_run=False):
    """Drive an open, logged-in page through writing and publishing one post."""
    target_url = page_url if page_url else "https://www.facebook.com/"
    print(f"Dang truy cap: {target_url}...")
    page.goto(target_url)

    print("Dang tai trang...")
    settle(page, 6000)
    require_login(page)

    # Switch profile logic if on a specific Page
    if page_url:
        print("Dang kiem tra yeu cau chuyen doi tu cach (profile)...")
        settle(page, 3000)

        if switch_profile_if_asked(page, poll_ms=int(2000 * SETTLE_SCALE)):
            print("Da yeu cau chuyen doi profile. Dang doi trang tai lai...")
            settle(page, 8000)
        else:
            print("Khong tim thay hoac khong can thiet phai nhan nut chuyen doi profile.")
            # Debug dialog structure if it is still visible
            dialog = page.locator('div[role="dialog"]').first
            if dialog.is_visible():
                try:
                    print("--- DEBUG DIALOG HTML (FIRST 1000 CHARACTERS) ---")
                    print(dialog.inner_html()[:1000])
                    print("-------------------------------------------------")
                except Exception as e:
                    pass

    clicked = False
    create_button, selector = first_visible(page, CREATE_POST_SELECTORS)
    if create_button:
        try:
            create_button.click()
            clicked = True
            print(f"Da nhan vao nut tao bai viet (Selector: {selector})")
        except Exception as e:
            print(f"Loi khi nhan nut tao bai viet: {e}")

    if not clicked:
        # A session that ended while the page was loading shows up here as a missing composer
        require_login(page)
        print("Khong tu dong tim thay nut tao bai viet. Dang chup anh man hinh debug...")
        debug_shot(page, "homepage")
        return False

    print("Dang doi hop thoai soan thao bai viet...")
    settle(page, 3000)

    typed = False
    locator = find_composer_textbox(page)
    if locator:
        try:
            print(f"Thu nhap noi dung vao o: {locator.get_attribute('aria-label')}")
            # Try to focus
            try:
                locator.focus(timeout=2000)
            except Exception:
                pass

            # Try to click with force and short timeout
            try:
                locator.click(force=True, timeout=2000)
            except Exception:
                pass

            # Fill the text
            locator.fill(text, timeout=3000)
            typed = True
            print("Da nhap noi dung bai dang thanh cong.")
        except Exception as e:
            print(f"Loi khi nhap noi dung: {e}")

    if not typed:
        print("Khong tim thay o nhap noi dung. Dang chup anh man hinh debug...")
        debug_shot(page, "modal")
        return False

    # Image upload logic if image_path is provided
    files = media_files(image_path)
    if files:
        print(f"Dang tai len {len(files)} tep: {', '.join(files)}...")
        for path in files:
            if not os.path.exists(path):
                print(f"Loi: Khong tim thay tep anh tai {path}")
                return False

        uploaded = False
        try:
            # Convert paths to absolute paths for Playwright
            abs_paths = [os.path.abspath(path) for path in files]

            # Let's locate the file input (usually hidden input[type='file'])
            inp = find_upload_input(page)
            if inp:
                print(f"Dang su dung o nhap tep (accept: '{inp.get_attribute('accept') or ''}')...")
                inp.set_input_files(abs_paths)
                uploaded = True
            else:
                print("Khong tim thay o nhap tep tin (input[type='file']) phu hop.")
        except Exception as e:
            print(f"Gap loi khi tai anh len bang input[type='file'] truc tiep: {e}")

        if not uploaded:
            # Fallback using file chooser event
            try:
                print("Thu click nut de trigger file chooser...")
                media_selectors = [
                    'div[role="button"]:has-text("Ảnh/video")',
                    'div[role="button"]:has-text("Photo/video")',
                    'span:has-text("Ảnh/video")',
                    'span:has-text("Photo/video")',
                    'div[aria-label="Ảnh/video"]',
                    'div[aria-label="Photo/video"]'
                ]

                for sel in media_selectors:
                    try:
                        btn = page.locator(sel).first
                        if btn.is_visible():
                            with page.expect_file_chooser() as fc_info:
                                btn.click(force=True)
                            file_chooser = fc_info.value
                            file_chooser.set_files(abs_paths)
                            uploaded = True
                            print(f"Da tai anh len qua file chooser (Selector: {sel})")
                            break
                    except Exception:
                        continue
            except Exception as e:
                print(f"Gap loi khi dung file chooser: {e}")

        if uploaded:
            print("Da tai anh len thanh cong. Dang cho xem truoc anh duoc hien thi...")
            settle(page, 6000) # Wait for image preview/upload to finish
        else:
            print("Khong the tai anh len. Dang chup anh man hinh debug...")
            debug_shot(page, "upload_failed")
            # Do not publish a post that is missing its image
            return False

    settle(page, 2000)

    if dry_run:
        # Leaving now discards the draft: everything up to the post button worked, nothing was published
        debug_shot(page, "dry_run")
        print("Che do thu (--dry-run): da soan xong bai viet nhung KHONG bam nut dang.")
        return True

    # Step 1: Click the main action button (could be "Tiếp", "Next", "Đăng", "Post", etc.)
    print("Dang tien hanh dang bai...")
    # The button stays disabled until the attachment has been processed
    has_video = any(is_video(path) for path in files)
    enable_timeout = VIDEO_UPLOAD_TIMEOUT_MS if has_video else BUTTON_ENABLE_TIMEOUT_MS
    clicked_text, post_button = click_button(page, PRIMARY_WORDS, enable_timeout)
    if post_button is None:
        print("Khong nhan duoc nut dang bai hoac tiep tuc. Dang chup anh man hinh debug...")
        debug_shot(page, "post_button")
        return False
    if clicked_text not in NEXT_WORDS:
        _progress["clicked"] = True

    # Step 2: If we clicked "Tiếp" or "Next", we must click the final "Đăng" or "Share" button
    if clicked_text in NEXT_WORDS:
        print("Da nhan nut 'Tiếp/Next'. Dang doi hop thoai chia se hien ra...")
        settle(page, 4000)

        # Dismiss any popup if present (e.g. "Thêm nút")
        dismiss_popups(page)

        clicked_text, post_button = click_button(page, PUBLISH_WORDS, enable_timeout)

        # Check again for secondary prompt popup that appears right after clicking Đăng
        settle(page, 2000)
        dismiss_popups(page)

        if post_button is None:
            print("Khong nhan duoc nut hoan tat dang bai (Đăng/Share). Dang chup anh man hinh debug...")
            debug_shot(page, "final_post_button")
            return False
        _progress["clicked"] = True

    # The composer closing is what shows Facebook took the post. A video keeps uploading after the
    # post button is clicked, and closing the browser early would cut it off.
    if has_video:
        print("Dang cho video tai len (toi da 10 phut)...")
        if not wait_for_composer_to_close(page, VIDEO_UPLOAD_TIMEOUT_MS, button=post_button):
            print("Video chua tai len xong sau 10 phut. Dang chup anh man hinh debug...")
            debug_shot(page, "video_timeout")
            return False
        print("Hop thoai soan bai da dong, video da duoc gui di.")
    elif not wait_for_composer_to_close(page, POST_TIMEOUT_MS, button=post_button):
        print("Hop thoai soan bai van mo sau khi nhan nut dang: bai chua duoc dang. Dang chup anh man hinh debug...")
        debug_shot(page, "not_posted")
        return False

    print("Dang cho bai viet duoc tai len he thong (15 giay)...")
    settle(page, 15000)

    # Save a screenshot to verify if it was posted successfully
    try:
        debug_shot(page, "result")
    except Exception as e:
        print(f"Loi khi chup anh ket qua: {e}")

    try:
        post_url, verified = find_post_url(page, text, target_url)
    except Exception as e:
        print(f"Khong tim duoc link bai viet: {e}")
        post_url, verified = None, None
    _progress.update(post_url=post_url, verified=verified)
    if verified is not None:
        print(f"{POST_VERIFIED_MARK}{'yes' if verified else 'no'}")
    if post_url:
        print(f"{POST_URL_MARK}{post_url}")
        print("Da thay bai viet tren trang, link o dong tren.")
    elif verified is False:
        print("Chua thay bai viet tren trang (nhom co kiem duyet, hoac Facebook chua hien bai). Hay kiem tra lai tren Facebook.")

    print("Hoan thanh qua trinh dang bai!")
    return True

def count_visible(page, selector):
    try:
        matches = page.locator(selector)
        return sum(1 for i in range(matches.count()) if matches.nth(i).is_visible())
    except Exception:
        return 0

# Runs in the page: a copy of the post composer (or, failing that, the top of the page) that keeps the structure
# a person or the AI needs to find buttons, and little else: no links, styles or pictures, short texts only. Text outside
# buttons is dropped when the composer is not open, so other people's posts in the feed never end up in the copy.
DOM_SNAPSHOT_JS = r"""(maxChars) => {
  const keepAttributes = ['role', 'aria-label', 'aria-disabled', 'aria-haspopup', 'type', 'accept', 'contenteditable',
                          'placeholder', 'data-testid', 'name', 'multiple'];
  const skip = new Set(['script', 'style', 'noscript', 'link', 'meta', 'head', 'iframe', 'canvas']);
  const empty = new Set(['svg', 'img', 'video', 'audio', 'picture']);
  const dialogs = [...document.querySelectorAll('div[role="dialog"]')]
    .filter(d => d.getClientRects().length > 0 && d.querySelector('[role="textbox"]'));
  const root = dialogs[0] || document.body;
  const inDialog = root !== document.body;
  const escape = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const parts = [];
  let size = 0;
  const push = s => { parts.push(s); size += s.length; };
  function walk(node, insideButton) {
    if (size > maxChars) return;
    if (node.nodeType === 3) {
      const t = node.textContent.replace(/\s+/g, ' ').trim();
      if (t && (inDialog || insideButton)) push(escape(t.slice(0, 30)));
      return;
    }
    if (node.nodeType !== 1) return;
    const tag = node.tagName.toLowerCase();
    if (skip.has(tag)) return;
    if (empty.has(tag)) { push('<' + tag + '/>'); return; }
    let attrs = '';
    for (const name of keepAttributes) {
      const value = node.getAttribute(name);
      if (value !== null) attrs += ' ' + name + '="' + escape(value.slice(0, 60)) + '"';
    }
    const button = insideButton || node.getAttribute('role') === 'button' || tag === 'button';
    push('<' + tag + attrs + '>');
    for (const child of node.childNodes) walk(child, button);
    push('</' + tag + '>');
  }
  walk(root, false);
  return { html: parts.join(''), from: inDialog ? 'composer' : 'page' };
}"""
DOM_SNAPSHOT_LIMIT = 60000

def snapshot_dom(page):
    """Save the sanitised structure of the open composer (or the page) to the debug folder.
    Returns (path, "composer"|"page")."""
    data = page.evaluate(DOM_SNAPSHOT_JS, DOM_SNAPSHOT_LIMIT)
    os.makedirs(DEBUG_DIR, exist_ok=True)
    path = os.path.join(DEBUG_DIR, f"{datetime.now():%Y%m%d-%H%M%S}_selftest.html")
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(data["html"])
    return path, data["from"]

def selftest(page, page_url=None):
    """Check which of the known selectors and button labels still match Facebook's page. Never posts.

    Opens the post composer, looks at it and closes it again. Returns a report dict;
    report["ok"] is True when a post could be written and published with the current selectors.
    """
    target = page_url if page_url else "https://www.facebook.com/"
    report = {"url": target, "logged_in": True, "create_post": {}, "composer_opened": False,
              "textbox": {}, "upload_input": False, "buttons": {}, "problems": [], "ok": False}
    page.goto(target)
    settle(page, 6000)
    if is_logged_out(page):
        report["logged_in"] = False
        report["problems"].append("Chua dang nhap hoac phien da het han (chay python login.py).")
        return report
    if page_url:
        settle(page, 3000)
        if switch_profile_if_asked(page, poll_ms=int(2000 * SETTLE_SCALE)):
            settle(page, 8000)

    for selector in CREATE_POST_SELECTORS:
        report["create_post"][selector] = count_visible(page, selector)
    button, _ = first_visible(page, CREATE_POST_SELECTORS)
    if button is None:
        report["problems"].append("Khong selector nao trong CREATE_POST_SELECTORS tim thay nut tao bai viet.")
    else:
        try:
            button.click()
            settle(page, 3000)
            report["composer_opened"] = True
        except Exception as e:
            report["problems"].append(f"Khong mo duoc hop soan bai: {e}")

    if report["composer_opened"]:
        for selector in TEXTBOX_SELECTORS:
            report["textbox"][selector] = count_visible(page, selector)
        if find_composer_textbox(page) is None:
            report["problems"].append("Khong selector nao trong TEXTBOX_SELECTORS tim thay o nhap noi dung.")
        report["upload_input"] = find_upload_input(page) is not None
        scopes = button_scopes(page)
        for word in PRIMARY_WORDS:
            report["buttons"][word] = find_button(scopes, [word])[0] is not None
        if not any(report["buttons"].values()):
            report["problems"].append("Khong thay nut Dang/Tiep nao (kiem tra PRIMARY_WORDS).")
        try:
            debug_shot(page, "selftest")
        except Exception:
            pass
        try:
            # Closing the composer leaves nothing behind: no text was typed
            page.keyboard.press("Escape")
        except Exception:
            pass
    if report["problems"]:
        # Something is broken: keep a copy of what the page looks like now, for a person or `ai_cli.py selectors`
        try:
            report["dom_file"], report["dom_from"] = snapshot_dom(page)
        except Exception:
            pass
    report["ok"] = not report["problems"]
    return report

def print_selftest(report):
    print(f"Trang kiem tra: {report['url']}")
    if not report["logged_in"]:
        print("  Chua dang nhap.")
    for group, title in (("create_post", "Nut tao bai viet"), ("textbox", "O nhap noi dung")):
        working = [selector for selector, count in report[group].items() if count]
        print(f"  {title}: {len(working)}/{len(report[group])} selector dang hoat dong")
        for selector in working[:3]:
            print(f"      + {selector}")
    if report["buttons"]:
        print("  Nut trong hop soan bai: " + ", ".join(f"{word}={'co' if found else 'khong'}" for word, found in report["buttons"].items()))
        print(f"  O tai tep len: {'co' if report['upload_input'] else 'khong'}")
    for problem in report["problems"]:
        print(f"  ! {problem}")
    if report.get("dom_file"):
        print(f"  Cau truc trang (da loc bot noi dung) luu o: {report['dom_file']}")
    print("KET QUA: " + ("on, co the dang bai." if report["ok"] else "co van de, xem phan ! o tren."))

def run_selftest(args):
    """Open Facebook with the saved session and run `selftest`. Returns (exit_code, report_or_None, message)."""
    account = args.account or ""
    state_path = STATE_PATH if account in ("", "default") else store.state_path_for(BASE_DIR, account)
    try:
        with open_session(state_path, args.headless) as (context, page):
            report = selftest(page, args.page)
    except SessionExpired as e:
        print(f"Loi: {e} Hay dang nhap lai (python login.py).")
        return EXIT_SESSION_EXPIRED, None, str(e)
    except PlaywrightError as e:
        print(f"Loi trinh duyet hoac mang: {e}")
        return 1, None, f"Loi trinh duyet hoac mang: {e}"
    print_selftest(report)
    if not report["logged_in"]:
        return EXIT_SESSION_EXPIRED, report, report["problems"][0]
    return (0 if report["ok"] else 1), report, ("Cac selector van hoat dong." if report["ok"] else "; ".join(report["problems"]))

def resolve_content(content, content_file):
    # A content file avoids command-line limits and quoting issues with long, multi-line posts
    if content_file:
        with open(content_file, encoding="utf-8", newline="") as f:
            return f.read()
    if content is None:
        raise ValueError("Thieu noi dung bai viet (truyen truc tiep hoac dung --content-file).")
    return content

def iso(moment):
    return moment.isoformat(timespec="seconds") if moment else None

def list_targets():
    """Where posts can go (the pages saved in config.json), the saved templates and the login state."""
    config = store.load_config(CONFIG_PATH)
    accounts = [{"name": "default", "session_saved_at": iso(store.session_saved_at(STATE_PATH))}]
    for name in config["accounts"]:
        accounts.append({"name": name, "session_saved_at": iso(store.session_saved_at(store.state_path_for(BASE_DIR, name)))})
    return {
        "pages": config["pages"],
        "templates": [template["name"] for template in config["templates"]],
        "session_saved_at": accounts[0]["session_saved_at"],
        "accounts": accounts,
    }

def print_targets(targets):
    for page in targets["pages"]:
        language = f" [{page['language']}]" if page.get("language") else ""
        print(f"{page['name']}{language}: {page['url']}")
    if not targets["pages"]:
        print("Chua co Fanpage/nhom nao trong config.json (them trong tab Fanpage & Mau cua app.py).")
    if targets["templates"]:
        print("Mau: " + ", ".join(targets["templates"]))
    for account in targets["accounts"]:
        print(f"Tai khoan {account['name']}: " + (account["session_saved_at"] or "chua dang nhap hoac phien da het han (chay python login.py)"))

def run_post(args):
    """Post according to `args`. Returns a result dict (see main)."""
    reset_progress()
    result = {"code": 1, "status": "failed", "message": "", "retryable": False,
              "post_url": None, "verified": None, "warnings": []}

    def finish(code, status, message, retryable=False):
        result.update(code=code, status=status, message=message, retryable=retryable)
        return result

    try:
        content = resolve_content(args.content, args.content_file)
        images = store.join_media(args.image)
        check_media(images)
        account = args.account or ""
        if account not in ("", "default"):
            store.state_path_for(BASE_DIR, account)
    except (OSError, ValueError) as e:
        print(f"Loi: {e}")
        return finish(1, "failed", str(e))

    config = store.load_config(CONFIG_PATH)
    records = history.load(HISTORY_PATH)
    problems = spam.check(content, args.page or "", records, datetime.now(), spam.settings_from(config),
                          allow_duplicate=args.allow_duplicate)
    if problems and args.dry_run:
        result["warnings"] = [problem.message for problem in problems]
        for warning in result["warnings"]:
            print(f"Canh bao: {warning}")
    elif problems and not args.force:
        message = " ".join(problem.message for problem in problems)
        print(f"Bi chan: {message} Dung --force de van dang.")
        record_history(args, content, images, "blocked", EXIT_BLOCKED, message, None, None)
        return finish(EXIT_BLOCKED, "blocked", f"{message} (dung --force de van dang)")

    try:
        ok = make_post(content, page_url=args.page, headless=args.headless, image_path=images,
                       dry_run=args.dry_run, account=account)
    except SessionExpired as e:
        print(f"Loi: {e} Hay dang nhap lai (python login.py).")
        message = f"{e} Hay dang nhap lai (python login.py)."
        record_history(args, content, images, "session_expired", EXIT_SESSION_EXPIRED, message, None, None)
        return finish(EXIT_SESSION_EXPIRED, "session_expired", message)
    result.update(post_url=_progress["post_url"], verified=_progress["verified"])
    if not ok:
        if _progress["clicked"]:
            message = ("Co the bai da duoc dang: nut dang da duoc bam. Hay kiem tra tren Facebook truoc khi dang lai "
                       "(xem nhat ky va anh trong debug_images).")
            retryable = False
        elif _progress["permanent"]:
            message, retryable = "Tep dinh kem khong ton tai.", False
        else:
            message = "Dang bai that bai, bai chua duoc dang. Xem nhat ky (stderr) va cac anh trong debug_images."
            retryable = True
        record_history(args, content, images, "failed", EXIT_RETRYABLE if retryable else 1, message, None, None)
        return finish(EXIT_RETRYABLE if retryable else 1, "failed", message, retryable)
    if args.dry_run:
        return finish(0, "dry_run", "Da soan xong bai nhung chua dang (--dry-run).")
    if result["post_url"]:
        message = "Facebook da nhan bai va bai da hien tren trang."
    elif result["verified"] is False:
        message = "Facebook da nhan bai nhung chua thay bai tren trang (nhom co kiem duyet?). Hay kiem tra tren Facebook."
    else:
        message = "Facebook da nhan bai."
    record_history(args, content, images, "posted", 0, message, result["post_url"], result["verified"])
    return finish(0, "posted", message)

def record_history(args, content, images, status, code, message, post_url, verified):
    history.append(HISTORY_PATH, {
        "status": status,
        "exit_code": code,
        "page_url": args.page or "",
        "account": args.account or "",
        "content": content,
        "image": images,
        "post_url": post_url,
        "verified": verified,
        "message": message,
    })

def main(argv=None):
    use_utf8_output()
    parser = argparse.ArgumentParser(description="Tu dong dang bai Facebook.")
    parser.add_argument("content", type=str, nargs="?", default=None, help="Noi dung can dang bai viet.")
    parser.add_argument("--content-file", type=str, default=None, help="Doc noi dung bai viet tu tep van ban UTF-8.")
    parser.add_argument("--page", type=str, default=None, help="Duong dan den trang Facebook can dang (Page URL).")
    parser.add_argument("--headless", action="store_true", help="Chay trinh duyet o che do an (Headless mode).")
    parser.add_argument("--image", type=str, action="append", default=[],
                        help="Anh hoac video can dang; lap lai (hoac dung a|b) de dang nhieu tep.")
    parser.add_argument("--account", type=str, default="", help="Ten tai khoan Facebook (mac dinh: tai khoan state.json).")
    parser.add_argument("--dry-run", action="store_true", help="Soan bai nhung khong bam nut dang (de thu).")
    parser.add_argument("--allow-duplicate", action="store_true", help="Bo qua viec kiem tra noi dung trung bai da dang.")
    parser.add_argument("--force", action="store_true", help="Bo qua moi kiem tra chong spam (trung noi dung, gioi han bai/ngay).")
    parser.add_argument("--json", action="store_true",
                        help="In ket qua duoi dang mot dong JSON tren stdout; nhat ky chuyen sang stderr.")
    parser.add_argument("--list-pages", action="store_true",
                        help="Liet ke Fanpage/nhom, mau noi dung va tinh trang dang nhap roi thoat.")
    parser.add_argument("--selftest", action="store_true",
                        help="Kiem tra cac selector con khop voi giao dien Facebook khong (khong dang bai).")

    args = parser.parse_args(argv)

    if args.list_pages:
        targets = list_targets()
        if args.json:
            print(json.dumps(targets, ensure_ascii=False))
        else:
            print_targets(targets)
        return 0

    started = time.time()
    # With --json stdout carries the result alone, so the progress log goes to stderr
    log = contextlib.redirect_stdout(sys.stderr) if args.json else contextlib.nullcontext()

    if args.selftest:
        with log:
            code, report, message = run_selftest(args)
        if args.json:
            print(json.dumps({"ok": code == 0, "exit_code": code, "message": message, "report": report,
                              "debug_images": new_debug_images(started)}, ensure_ascii=False))
        return code

    with log:
        result = run_post(args)
    if args.json:
        print(json.dumps({
            "ok": result["code"] == 0,
            "status": result["status"],
            "exit_code": result["code"],
            "message": result["message"],
            "retryable": result["retryable"],
            "post_url": result["post_url"],
            "verified": result["verified"],
            "warnings": result["warnings"],
            "debug_images": new_debug_images(started),
        }, ensure_ascii=False))
    return result["code"]

if __name__ == "__main__":
    sys.exit(main())
