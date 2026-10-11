import os
import sys
import time
import argparse
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

VIDEO_EXTENSIONS = (".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".wmv")
VIDEO_UPLOAD_TIMEOUT_MS = 10 * 60 * 1000
# A text or image post still sitting in the composer after this long did not go through
POST_TIMEOUT_MS = 2 * 60 * 1000
# How long the post button may stay greyed out while an attached image is being processed
BUTTON_ENABLE_TIMEOUT_MS = 2 * 60 * 1000
BUTTON_APPEAR_TIMEOUT_MS = 10 * 1000

# Exit code telling the caller that Facebook wants a new login, as opposed to a failed post
EXIT_SESSION_EXPIRED = 2

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
            page.screenshot(path="debug_session.png")
        except Exception:
            pass
        raise SessionExpired("Facebook yeu cau dang nhap lai hoac xac minh (checkpoint).")

def make_post(text, page_url=None, headless=False, image_path=None):
    """Publish a post. Returns True once Facebook has taken it; raises SessionExpired if logged out."""
    state_path = "state.json"
    if not os.path.exists(state_path):
        print(f"Loi: Khong tim thay tep {state_path}. Vui long chay python login.py truoc de dang nhap.")
        return False

    with sync_playwright() as p:
        print(f"Dang mo trinh duyet (Chế độ: {'Ẩn danh/Headless' if headless else 'Hiển thị/Headful'})...")
        browser = p.chromium.launch(headless=headless)
        try:
            # Create context restoring the saved state
            context = browser.new_context(storage_state=state_path)
            page = context.new_page()
            posted = publish(page, text, page_url=page_url, image_path=image_path)
            if posted:
                save_session(context, state_path)
            return posted
        finally:
            browser.close()

def publish(page, text, page_url=None, image_path=None):
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
        page.screenshot(path="debug_homepage.png")
        print("Da luu anh debug_homepage.png. Vui long kiem tra lai giao dien Facebook.")
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
        page.screenshot(path="debug_modal.png")
        print("Da luu anh debug_modal.png.")
        return False

    # Image upload logic if image_path is provided
    if image_path:
        print(f"Dang tai anh len: {image_path}...")
        # Check if file exists
        if not os.path.exists(image_path):
            print(f"Loi: Khong tim thay tep anh tai {image_path}")
            return False

        uploaded = False
        try:
            # Convert path to absolute path for Playwright
            abs_image_path = os.path.abspath(image_path)

            # Let's locate the file input (usually hidden input[type='file'])
            inp = find_upload_input(page)
            if inp:
                print(f"Dang su dung o nhap tep (accept: '{inp.get_attribute('accept') or ''}')...")
                inp.set_input_files(abs_image_path)
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
                            file_chooser.set_files(abs_image_path)
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
            page.screenshot(path="debug_upload_failed.png")
            print("Da luu anh debug_upload_failed.png.")
            # Do not publish a post that is missing its image
            return False

    settle(page, 2000)

    # Step 1: Click the main action button (could be "Tiếp", "Next", "Đăng", "Post", etc.)
    print("Dang tien hanh dang bai...")
    # The button stays disabled until the attachment has been processed
    enable_timeout = VIDEO_UPLOAD_TIMEOUT_MS if is_video(image_path) else BUTTON_ENABLE_TIMEOUT_MS
    clicked_text, post_button = click_button(page, PRIMARY_WORDS, enable_timeout)
    if post_button is None:
        print("Khong nhan duoc nut dang bai hoac tiep tuc. Dang chup anh man hinh debug...")
        page.screenshot(path="debug_post_button.png")
        print("Da luu anh debug_post_button.png.")
        return False

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
            page.screenshot(path="debug_final_post_button.png")
            print("Da luu anh debug_final_post_button.png.")
            return False

    # The composer closing is what shows Facebook took the post. A video keeps uploading after the
    # post button is clicked, and closing the browser early would cut it off.
    if is_video(image_path):
        print("Dang cho video tai len (toi da 10 phut)...")
        if not wait_for_composer_to_close(page, VIDEO_UPLOAD_TIMEOUT_MS, button=post_button):
            print("Video chua tai len xong sau 10 phut. Dang chup anh man hinh debug...")
            page.screenshot(path="debug_video_timeout.png")
            print("Da luu anh debug_video_timeout.png.")
            return False
        print("Hop thoai soan bai da dong, video da duoc gui di.")
    elif not wait_for_composer_to_close(page, POST_TIMEOUT_MS, button=post_button):
        print("Hop thoai soan bai van mo sau khi nhan nut dang: bai chua duoc dang. Dang chup anh man hinh debug...")
        page.screenshot(path="debug_not_posted.png")
        print("Da luu anh debug_not_posted.png.")
        return False

    print("Dang cho bai viet duoc tai len he thong (15 giay)...")
    settle(page, 15000)

    # Save a screenshot to verify if it was posted successfully
    try:
        page.screenshot(path="debug_result.png")
        print("Da luu anh debug_result.png de kiem tra ket qua sau cung.")
    except Exception as e:
        print(f"Loi khi chup anh ket qua: {e}")

    print("Hoan thanh qua trinh dang bai!")
    return True

def resolve_content(content, content_file):
    # A content file avoids command-line limits and quoting issues with long, multi-line posts
    if content_file:
        with open(content_file, encoding="utf-8", newline="") as f:
            return f.read()
    if content is None:
        raise ValueError("Thieu noi dung bai viet (truyen truc tiep hoac dung --content-file).")
    return content

def main(argv=None):
    use_utf8_output()
    parser = argparse.ArgumentParser(description="Tu dong dang bai Facebook.")
    parser.add_argument("content", type=str, nargs="?", default=None, help="Noi dung can dang bai viet.")
    parser.add_argument("--content-file", type=str, default=None, help="Doc noi dung bai viet tu tep van ban UTF-8.")
    parser.add_argument("--page", type=str, default=None, help="Duong dan den trang Facebook can dang (Page URL).")
    parser.add_argument("--headless", action="store_true", help="Chay trinh duyet o che do an (Headless mode).")
    parser.add_argument("--image", type=str, default=None, help="Duong dan den tep anh hoac video can dang (tuy chon).")

    args = parser.parse_args(argv)
    try:
        content = resolve_content(args.content, args.content_file)
    except (OSError, ValueError) as e:
        print(f"Loi: {e}")
        return 1

    try:
        ok = make_post(content, page_url=args.page, headless=args.headless, image_path=args.image)
    except SessionExpired as e:
        print(f"Loi: {e} Hay dang nhap lai (python login.py).")
        return EXIT_SESSION_EXPIRED
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
