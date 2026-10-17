import argparse
import os
import sys

from playwright.sync_api import sync_playwright

import store

# Saved next to the scripts so post.py finds it whatever folder it runs from
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")

def is_logged_in(context):
    # c_user holds the account id and only exists once the login (including 2FA) has gone through
    return any(cookie["name"] == "c_user" for cookie in context.cookies("https://www.facebook.com"))

def save_state(context, state_path):
    # Write to a temp file then rename, so a session being replaced is never left half-written
    temp_path = state_path + ".tmp"
    context.storage_state(path=temp_path)
    os.replace(temp_path, state_path)

def run(account=""):
    """Log in by hand and save the session. `account` names an extra login (state.<account>.json)."""
    state_path = STATE_PATH if account in ("", "default") else store.state_path_for(os.path.dirname(STATE_PATH), account)
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
        while not is_logged_in(context):
            # Saving now would store a logged-out session that fails later on the first post.
            # print() rather than an input() prompt: the app only shows complete lines.
            print("\nChua thay dang nhap xong. Hay dang nhap trong trinh duyet roi nhan ENTER lan nua.")
            input()

        # Save storage state to a file
        save_state(context, state_path)
        print(f"\n[Thanh cong] Da luu thong tin phien dang nhap vao tep: {state_path}")

        browser.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dang nhap Facebook bang tay va luu phien.")
    parser.add_argument("--account", default="", help="Ten tai khoan phu (mac dinh: tai khoan state.json).")
    try:
        run(parser.parse_args().account)
    except ValueError:
        # ASCII only: this runs before any output encoding is set up
        print("Loi: ten tai khoan khong hop le (chi chu, so, gach ngang va gach duoi).")
        sys.exit(1)
