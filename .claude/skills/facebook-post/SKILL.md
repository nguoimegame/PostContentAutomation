---
name: facebook-post
description: Use when the user wants to publish, test or schedule a Facebook post (personal page, Fanpage or group) with this project's post.py / enqueue.py, or asks which pages are available or whether the Facebook login is still valid.
---

# Đăng bài Facebook bằng project này

Đọc `AGENTS.md` ở thư mục gốc để biết đầy đủ lệnh, mã thoát và các điều cấm. Tóm tắt quy trình:

1. **Kiểm tra điều kiện:** `python post.py --list-pages --json`. Nếu `session_saved_at` là `null`, dừng lại và nhờ người dùng đăng nhập (`python login.py`); không tự làm.
2. **Chọn nơi đăng:** lấy URL trong `pages` của kết quả trên. Nếu người dùng gọi tên trang, đối chiếu theo `name` và hỏi lại khi có nhiều kết quả khớp.
3. **Chuẩn bị nội dung:** ghi ra tệp UTF-8 và dùng `--content-file`. Kiểm tra tệp ảnh/video tồn tại.
4. **Thử trước:** `python post.py --content-file <tệp> --page <URL> --dry-run --json`, rồi cho người dùng xem lại nội dung, nơi đăng và tệp đính kèm.
5. **Chỉ đăng thật sau khi người dùng xác nhận rõ:** bỏ `--dry-run`. Đăng là hành động công khai, không thu hồi được.
   - Hẹn giờ: `python enqueue.py --json add --content-file <tệp> --page <URL> --at "YYYY-MM-DD HH:MM"` (bài chỉ chạy khi `app.py` đang mở; mã thoát 3 nghĩa là app đang mở nên không thêm được).
6. **Đọc kết quả JSON:** `status` là `posted` thì báo thành công; `failed` thì đọc `message` và `debug_images`, không tự đăng lại; `session_expired` thì nhờ người dùng đăng nhập lại.

Không bao giờ đọc hay in `state.json`.
