import os
import sys
import time
import argparse
from playwright.sync_api import sync_playwright

VIDEO_EXTENSIONS = (".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".wmv")
VIDEO_UPLOAD_TIMEOUT_MS = 10 * 60 * 1000

def is_video(path):
    return bool(path) and os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS

def wait_for_composer_to_close(page, timeout_ms, poll_ms=2000):
    # The composer is the dialog holding the post textbox; it stays open while a video is uploading
    composer = page.locator('div[role="dialog"]:has(div[role="textbox"])')
    waited = 0
    while True:
        still_open = any(composer.nth(i).is_visible() for i in range(composer.count()))
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

def make_post(text, page_url=None, headless=False, image_path=None):
    state_path = "state.json"
    if not os.path.exists(state_path):
        print(f"Loi: Khong tim thay tep {state_path}. Vui long chay python login.py truoc de dang nhap.")
        return False

    with sync_playwright() as p:
        print(f"Dang mo trinh duyet (Chế độ: {'Ẩn danh/Headless' if headless else 'Hiển thị/Headful'})...")
        browser = p.chromium.launch(headless=headless)
        
        # Create context restoring the saved state
        context = browser.new_context(storage_state=state_path)
        page = context.new_page()
        
        target_url = page_url if page_url else "https://www.facebook.com/"
        print(f"Dang truy cap: {target_url}...")
        page.goto(target_url)
        
        print("Dang tai trang...")
        page.wait_for_timeout(6000)
        
        create_post_selectors = CREATE_POST_SELECTORS

        # Switch profile logic if on a specific Page
        if page_url:
            print("Dang kiem tra yeu cau chuyen doi tu cach (profile)...")
            page.wait_for_timeout(3000)
            
            # We will wait up to 10 seconds for either the switch prompt/dialog OR the post button to appear.
            switched = False
            for attempt in range(5):
                # 1. Try to find the button inside dialog by get_by_role or text
                dialog = page.locator('div[role="dialog"]').first
                if dialog.is_visible():
                    print("Phat hien hop thoai yeu cau chuyen doi profile (dialog)...")
                    
                    # Try different locators for the switch button inside the dialog
                    dialog_locators = [
                        dialog.get_by_role("button", name="Chuyển", exact=True),
                        dialog.get_by_role("button", name="Switch", exact=True),
                        dialog.locator('div[role="button"]:has-text("Chuyển")'),
                        dialog.locator('div[role="button"]:has-text("Switch")'),
                        dialog.locator('span:has-text("Chuyển")'),
                        dialog.locator('span:has-text("Switch")'),
                        dialog.locator('button:has-text("Chuyển")'),
                        dialog.locator('button:has-text("Switch")'),
                        # Filter by text content
                        dialog.locator('div, span, button, a').filter(has_text="Chuyển").first,
                        dialog.locator('div, span, button, a').filter(has_text="Switch").first
                    ]
                    
                    for loc in dialog_locators:
                        try:
                            if loc.is_visible():
                                print(f"Dang nhan nut chuyen doi trong dialog bang: {loc}")
                                loc.click(force=True, timeout=5000)
                                switched = True
                                break
                        except Exception as e:
                            continue
                            
                    if switched:
                        break
                
                # 2. Check for buttons on the page (like "Chuyển ngay" in the banner)
                switch_selectors = [
                    'div[role="button"]:has-text("Chuyển ngay")',
                    'div[role="button"]:has-text("Switch Now")',
                    'div[role="button"]:has-text("Chuyển")',
                    'div[role="button"]:has-text("Switch")',
                    'span:has-text("Chuyển ngay")',
                    'span:has-text("Switch Now")',
                    'span:has-text("Chuyển")',
                    'span:has-text("Switch")',
                    'button:has-text("Chuyển ngay")',
                    'button:has-text("Switch Now")',
                    'button:has-text("Chuyển")',
                    'button:has-text("Switch")'
                ]
                
                for selector in switch_selectors:
                    try:
                        locator = page.locator(selector).first
                        if locator.is_visible():
                            print(f"Phat hien nut chuyen doi tren trang. Dang nhan: {selector}")
                            locator.click(force=True, timeout=5000)
                            switched = True
                            break
                    except Exception:
                        continue
                
                if switched:
                    break
                
                # 3. If post box is already visible, we don't need to switch
                post_box_visible = False
                for sel in create_post_selectors:
                    try:
                        if page.locator(sel).first.is_visible():
                            post_box_visible = True
                            break
                    except Exception:
                        continue
                if post_box_visible:
                    print("Da o dung profile hoac nut tao bai viet da san sang. Khong can chuyen doi.")
                    break
                    
                page.wait_for_timeout(2000)
            
            if switched:
                print("Da yeu cau chuyen doi profile. Dang doi trang tai lai...")
                page.wait_for_timeout(8000)
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
        create_button, selector = first_visible(page, create_post_selectors)
        if create_button:
            try:
                create_button.click()
                clicked = True
                print(f"Da nhan vao nut tao bai viet (Selector: {selector})")
            except Exception as e:
                print(f"Loi khi nhan nut tao bai viet: {e}")

        if not clicked:
            print("Khong tu dong tim thay nut tao bai viet. Dang chup anh man hinh debug...")
            page.screenshot(path="debug_homepage.png")
            print("Da luu anh debug_homepage.png. Vui long kiem tra lai giao dien Facebook.")
            browser.close()
            return False
            
        print("Dang doi hop thoai soan thao bai viet...")
        page.wait_for_timeout(3000)
        
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
            browser.close()
            return False
            
        # Image upload logic if image_path is provided
        if image_path:
            print(f"Dang tai anh len: {image_path}...")
            # Check if file exists
            if not os.path.exists(image_path):
                print(f"Loi: Khong tim thay tep anh tai {image_path}")
                browser.close()
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
                page.wait_for_timeout(6000) # Wait for image preview/upload to finish
            else:
                print("Khong the tai anh len. Dang chup anh man hinh debug...")
                page.screenshot(path="debug_upload_failed.png")
                print("Da luu anh debug_upload_failed.png.")
                # Do not publish a post that is missing its image
                browser.close()
                return False
                
        page.wait_for_timeout(2000)
        
        # Step 1: Click the main action button (could be "Tiếp", "Next", "Đăng", "Post", etc.)
        print("Dang tien hanh dang bai...")
        
        # Candidate words for exact match
        candidate_words = ["Đăng", "Post", "Tiếp", "Next", "Chia sẻ", "Share"]
        clicked_primary = False
        clicked_text = ""
        
        # 1. Try exact matches with get_by_role/get_by_text first
        for word in candidate_words:
            try:
                btn = page.get_by_role("button", name=word, exact=True).first
                if btn.is_visible() and btn.is_enabled():
                    clicked_text = word
                    print(f"Thu nhan nut bang get_by_role (exact): '{word}'")
                    btn.click(force=True, timeout=3000)
                    clicked_primary = True
                    break
            except Exception:
                pass
                
            try:
                btn = page.get_by_text(word, exact=True).first
                if btn.is_visible():
                    clicked_text = word
                    print(f"Thu nhan nut bang get_by_text (exact): '{word}'")
                    btn.click(force=True, timeout=3000)
                    clicked_primary = True
                    break
            except Exception:
                pass
                
        # 2. Try selector-based exact matches
        if not clicked_primary:
            primary_selectors = [
                'div[role="button"] >> text="Đăng"',
                'div[role="button"] >> text="Post"',
                'div[role="button"] >> text="Tiếp"',
                'div[role="button"] >> text="Next"',
                'div[role="button"] >> text="Chia sẻ"',
                'div[role="button"] >> text="Share"',
                'button >> text="Đăng"',
                'button >> text="Post"',
                'button >> text="Tiếp"',
                'button >> text="Next"',
                'div[aria-label="Đăng"]',
                'div[aria-label="Post"]',
                'div[aria-label="Tiếp"]',
                'div[aria-label="Next"]',
                'div[aria-label="Đăng bài"]'
            ]
            for selector in primary_selectors:
                try:
                    btn = page.locator(selector).first
                    if btn.is_visible():
                        clicked_text = btn.text_content() or ""
                        print(f"Thu nhan nut bang selector: {selector} (Text: '{clicked_text}')")
                        btn.click(force=True, timeout=3000)
                        clicked_primary = True
                        break
                except Exception:
                    continue
                    
        # 3. Fallback to searching all buttons inside any visible dialog (strict-mode safe)
        if not clicked_primary:
            try:
                dialogs = page.locator('div[role="dialog"]')
                count_dialogs = dialogs.count()
                for d_idx in range(count_dialogs):
                    d = dialogs.nth(d_idx)
                    if d.is_visible():
                        # Try exact match in dialog first
                        for word in candidate_words:
                            try:
                                btn = d.get_by_role("button", name=word, exact=True).first
                                if btn.is_visible() and btn.is_enabled():
                                    clicked_text = word
                                    print(f"Thu nhan nut trong dialog {d_idx} bang role (exact): '{word}'")
                                    btn.click(force=True, timeout=3000)
                                    clicked_primary = True
                                    break
                            except Exception:
                                pass
                            if clicked_primary:
                                break
                                
                        if not clicked_primary:
                            buttons = d.locator('div[role="button"], button')
                            count = buttons.count()
                            for i in range(count):
                                btn_text = (buttons.nth(i).text_content() or "").strip()
                                if btn_text in candidate_words:
                                    print(f"Thu nhan nut trong dialog {d_idx} bang text exact match: '{btn_text}'")
                                    buttons.nth(i).click(force=True, timeout=3000)
                                    clicked_primary = True
                                    clicked_text = btn_text
                                    break
                    if clicked_primary:
                        break
            except Exception as e:
                print(f"Co loi khi tim nut trong dialog: {e}")

        if not clicked_primary:
            print("Khong tim thay nut dang bai hoac tiep tuc. Dang chup anh man hinh debug...")
            page.screenshot(path="debug_post_button.png")
            print("Da luu anh debug_post_button.png.")
            browser.close()
            return False

        # Helper to dismiss intermediate popups (e.g. "Trò chuyện trực tiếp với mọi người" -> "Lúc khác")
        def handle_popups():
            dismiss_words = ["Lúc khác", "Not now", "Bỏ qua", "Skip", "Hủy", "Cancel"]
            for dw in dismiss_words:
                try:
                    btn = page.get_by_role("button", name=dw, exact=True).first
                    if btn.is_visible():
                        print(f"Phat hien va nhan nut bo qua hop thoai phu: '{dw}'")
                        btn.click(force=True, timeout=2000)
                        page.wait_for_timeout(1500)
                        return True
                except Exception:
                    pass
                try:
                    loc = page.locator(f'div[role="button"]:has-text("{dw}"), button:has-text("{dw}"), span:has-text("{dw}")').first
                    if loc.is_visible():
                        print(f"Phat hien va nhan nut bo qua hop thoai phu (selector): '{dw}'")
                        loc.click(force=True, timeout=2000)
                        page.wait_for_timeout(1500)
                        return True
                except Exception:
                    pass
            return False

        # Step 2: If we clicked "Tiếp" or "Next", we must click the final "Đăng" or "Share" button
        if any(x in clicked_text for x in ["Tiếp", "Next"]):
            print("Da nhan nut 'Tiếp/Next'. Dang doi hop thoai chia se hien ra...")
            page.wait_for_timeout(4000)
            
            # Dismiss any popup if present (e.g. "Thêm nút")
            handle_popups()

            final_candidate_words = ["Đăng", "Post", "Chia sẻ", "Share"]
            clicked_final = False
            
            # 1. Try exact matches with get_by_role/get_by_text
            for word in final_candidate_words:
                try:
                    btn = page.get_by_role("button", name=word, exact=True).first
                    if btn.is_visible() and btn.is_enabled():
                        print(f"Thu nhan nut hoan tat bang role (exact): '{word}'")
                        btn.click(force=True, timeout=3000)
                        clicked_final = True
                        break
                except Exception:
                    pass
                    
                try:
                    btn = page.get_by_text(word, exact=True).first
                    if btn.is_visible():
                        print(f"Thu nhan nut hoan tat bang text (exact): '{word}'")
                        btn.click(force=True, timeout=3000)
                        clicked_final = True
                        break
                except Exception:
                    pass
            
            # 2. Try selector-based exact matches
            if not clicked_final:
                final_selectors = [
                    'div[role="button"] >> text="Đăng"',
                    'div[role="button"] >> text="Post"',
                    'div[role="button"] >> text="Chia sẻ"',
                    'div[role="button"] >> text="Share"',
                    'button >> text="Đăng"',
                    'button >> text="Post"',
                    'button >> text="Chia sẻ"',
                    'button >> text="Share"',
                    'div[aria-label="Đăng"]',
                    'div[aria-label="Post"]',
                    'div[aria-label="Chia sẻ"]',
                    'div[aria-label="Share"]'
                ]
                for selector in final_selectors:
                    try:
                        btn = page.locator(selector).first
                        if btn.is_visible():
                            print(f"Thu nhan nut hoan tat bang selector: {selector}")
                            btn.click(force=True, timeout=3000)
                            clicked_final = True
                            print(f"Da click nut hoan tat dang bai bang selector: {selector}")
                            break
                    except Exception:
                        continue
                        
            # 3. Fallback to search inside any visible dialog
            if not clicked_final:
                try:
                    dialogs = page.locator('div[role="dialog"]')
                    count_dialogs = dialogs.count()
                    for d_idx in range(count_dialogs):
                        d = dialogs.nth(d_idx)
                        if d.is_visible():
                            # Try exact match in dialog first
                            for word in final_candidate_words:
                                try:
                                    btn = d.get_by_role("button", name=word, exact=True).first
                                    if btn.is_visible() and btn.is_enabled():
                                        print(f"Thu nhan nut hoan tat trong dialog {d_idx} bang role (exact): '{word}'")
                                        btn.click(force=True, timeout=3000)
                                        clicked_final = True
                                        break
                                except Exception:
                                    pass
                                if clicked_final:
                                    break
                                    
                            if not clicked_final:
                                buttons = d.locator('div[role="button"], button')
                                count = buttons.count()
                                for i in range(count):
                                    btn_text = (buttons.nth(i).text_content() or "").strip()
                                    if btn_text in final_candidate_words:
                                        print(f"Thu nhan nut hoan tat trong dialog {d_idx} bang text exact match: '{btn_text}'")
                                        buttons.nth(i).click(force=True, timeout=3000)
                                        clicked_final = True
                                        print(f"Da click nut hoan tat trong dialog {d_idx} (Text: '{btn_text}')")
                                        break
                        if clicked_final:
                            break
                except Exception as e:
                    print(f"Co loi khi tim nut hoan tat trong dialog: {e}")

            # Check again for secondary prompt popup that appears right after clicking Đăng
            page.wait_for_timeout(2000)
            handle_popups()
                    
            if not clicked_final:
                print("Khong tim thay nut hoan tat dang bai (Đăng/Share). Dang chup anh man hinh debug...")
                page.screenshot(path="debug_final_post_button.png")
                print("Da luu anh debug_final_post_button.png.")
                browser.close()
                return False
                
        # A video keeps uploading after the post button is clicked; closing the browser early would cut it off
        if is_video(image_path):
            print("Dang cho video tai len (toi da 10 phut)...")
            if not wait_for_composer_to_close(page, VIDEO_UPLOAD_TIMEOUT_MS):
                print("Video chua tai len xong sau 10 phut. Dang chup anh man hinh debug...")
                page.screenshot(path="debug_video_timeout.png")
                print("Da luu anh debug_video_timeout.png.")
                browser.close()
                return False
            print("Hop thoai soan bai da dong, video da duoc gui di.")

        print("Dang cho bai viet duoc tai len he thong (15 giay)...")
        page.wait_for_timeout(15000)
        
        # Save a screenshot to verify if it was posted successfully
        try:
            page.screenshot(path="debug_result.png")
            print("Da luu anh debug_result.png de kiem tra ket qua sau cung.")
        except Exception as e:
            print(f"Loi khi chup anh ket qua: {e}")
            
        print("Hoan thanh qua trinh dang bai!")
        browser.close()
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

    ok = make_post(content, page_url=args.page, headless=args.headless, image_path=args.image)
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())

