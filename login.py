import os
from playwright.sync_api import sync_playwright

def run():
    with sync_playwright() as p:
        # Launch browser in headful mode so the user can interact
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        
        print("Dang mo trang dang nhap Facebook...")
        page.goto("https://www.facebook.com")
        
        print("\n--- HUONG DAN ---")
        print("1. Hay thuc hien dang nhap vao tai khoan Facebook cua ban tren cua so trinh duyet vua hien len.")
        print("2. Hoan thanh xac thuc 2 lop (2FA) hoac ma bao mat neu duoc yeu cau.")
        print("3. Doi cho den khi ban thay trang chu Feed cua Facebook hien ra.")
        print("4. Quay lai man hinh console nay va nhan phim ENTER de luu phien dang nhap.")
        print("-----------------\n")
        
        input("Nhan ENTER sau khi da dang nhap thanh cong...")
        
        # Save storage state to a file
        state_path = "state.json"
        context.storage_state(path=state_path)
        print(f"\n[Thanh cong] Da luu thong tin phien dang nhap vao tep: {state_path}")
        
        browser.close()

if __name__ == "__main__":
    run()
