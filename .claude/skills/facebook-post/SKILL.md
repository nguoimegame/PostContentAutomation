---
name: facebook-post
description: Use when the user wants to publish, test, schedule, edit or import Facebook posts (personal page, Fanpage or group) with this project, or asks which pages are available, whether the login is valid, what was posted, or why a post failed or was blocked.
---

# Đăng bài Facebook bằng project này

Đọc `AGENTS.md` ở thư mục gốc để biết đầy đủ lệnh, mã thoát và các điều cấm. Nếu MCP server `facebook-poster` (`.mcp.json`) đã bật thì dùng các công cụ của nó; không thì dùng các CLI tương ứng với `--json`. Tóm tắt quy trình:

1. **Kiểm tra điều kiện:** `list_pages` / `python post.py --list-pages --json`. Nếu tài khoản cần dùng chưa đăng nhập (`session_saved_at` là `null`), dừng lại và nhờ người dùng đăng nhập (`python login.py [--account tên]`); không tự làm.
2. **Chọn nơi đăng:** lấy tên hoặc URL trong `pages`. Nếu người dùng gọi tên trang, đối chiếu theo `name` và hỏi lại khi không chắc. `personal` là trang cá nhân.
3. **Chuẩn bị nội dung:** nội dung dài ghi ra tệp UTF-8 và dùng `--content-file`. Kiểm tra tệp ảnh/video tồn tại. Muốn mỗi trang một bản khác nhau thì dùng `{page}`, `{language}`, `{a|b|c}` hoặc nhiều `--content-file`.
4. **Thử trước:** `dry_run_post` / `post.py --dry-run`, rồi cho người dùng xem lại nội dung, nơi đăng, tệp đính kèm và các `warnings` (trùng bài, quá giới hạn).
5. **Chỉ đăng thật sau khi người dùng xác nhận rõ:** `post_now` với `confirm=true`, hoặc bỏ `--dry-run`. Đăng là hành động công khai, không thu hồi được.
   - Hẹn giờ: `schedule_post` / `python enqueue.py --json add …` (bài chỉ chạy khi `app.py` hoặc `daemon.py` đang chạy; kiểm tra bằng `daemon_status`; mã thoát 3 nghĩa là app đang mở nên không thêm được).
   - Nhiều bài một lúc: `enqueue.py import bai.csv --dry-run` rồi bỏ `--dry-run`.
6. **Đọc kết quả:**
   - `posted` → báo thành công kèm `post_url` nếu có; `verified: false` thì nói rõ chưa thấy bài trên trang (nhóm kiểm duyệt?).
   - `failed` + `retryable: true` (mã 4) → chắc chắn chưa đăng, có thể thử lại một lần.
   - `failed` còn lại (mã 1) → **bài có thể đã lên**: xem `get_history` và `debug_images` trước, không tự đăng lại.
   - `blocked` (mã 5) → trùng nội dung hoặc đăng quá nhiều; nói lý do với người dùng, chỉ dùng `allow_duplicate`/`--force` khi họ đồng ý.
   - `session_expired` (mã 2) → nhờ người dùng đăng nhập lại.
7. **Posts keep failing?** Chạy `selftest` / `post.py --selftest` để biết bộ chọn nào của Facebook đã đổi.
8. **Cần soạn nội dung?** `python ai_cli.py --json draft "ý tưởng" --page "Tên trang" …` (hoặc `localize`, `series`, `review`, `polish`, `reply`, `describe`, `card`, `explain`). Nó tốn tiền theo khóa API của người dùng và gửi nội dung tới Anthropic, nên chỉ chạy khi người dùng nhờ. Kết quả là **bản nháp**: cho người dùng đọc và sửa, rồi mới `enqueue.py import` hoặc `post_now`; không tự đưa vào hàng đợi.

Không bao giờ đọc hay in `state*.json` hay token/mật khẩu/khóa API trong `config.json`.
