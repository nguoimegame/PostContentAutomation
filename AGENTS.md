# Facebook Auto Poster — hướng dẫn cho agent

Phần mềm tự động đăng bài lên trang cá nhân, Fanpage và nhóm Facebook bằng Playwright (Chromium), dùng phiên đăng nhập đã lưu trong `state.json`. Có giao diện Tkinter (`app.py`) cho người dùng và các CLI cho agent.

## Agent dùng gì

| Việc cần làm | Lệnh |
|---|---|
| Xem có những nơi đăng nào và đã đăng nhập chưa | `python post.py --list-pages --json` |
| Thử soạn bài, **không đăng** | `python post.py --content-file bai.txt --page URL --dry-run --json` |
| Đăng ngay | `python post.py --content-file bai.txt --page URL [--image tệp] --json` |
| Hẹn giờ (đăng khi `app.py` đang mở) | `python enqueue.py --json add --content-file bai.txt --page URL --at "YYYY-MM-DD HH:MM"` |
| Xem / xóa bài hẹn giờ | `python enqueue.py --json list` / `python enqueue.py --json remove ID` |
| Chạy test | `python -m unittest` |

Không dùng `app.py` (giao diện đồ họa, agent không điều khiển được) và không dùng `login.py`.

### Quy ước khi gọi

- Luôn viết nội dung ra tệp UTF-8 rồi dùng `--content-file`. Truyền nội dung trên dòng lệnh dễ hỏng vì emoji, xuống dòng và dấu nháy.
- `--page` là URL đầy đủ lấy từ `--list-pages` (không có thì đăng lên trang cá nhân). `enqueue.py add` cho lặp `--page` nhiều lần: mỗi nơi là một bài riêng, cách nhau `--gap` phút (mặc định lấy từ `config.json`).
- `--image` nhận một ảnh **hoặc** một video. Video có thể mất tới 10 phút.
- Chạy từ thư mục nào cũng được: `state.json`, `config.json`, `queue.json` luôn nằm cạnh các script. Riêng ảnh `debug_*.png` ghi vào thư mục đang chạy lệnh.
- Dùng `--json`: stdout chỉ có đúng một dòng JSON, nhật ký tiến trình đi vào stderr.

### Kết quả và mã thoát của `post.py`

JSON: `{"ok", "status", "exit_code", "message", "debug_images"}`.

| Mã | `status` | Ý nghĩa và việc cần làm |
|---|---|---|
| 0 | `posted` | Facebook đã nhận bài (hộp soạn bài đã đóng). Nhóm có kiểm duyệt vẫn có thể giữ bài chờ duyệt. |
| 0 | `dry_run` | Đã soạn xong nhưng chưa đăng (`--dry-run`). |
| 1 | `failed` | Thất bại (không tìm thấy nút, tệp đính kèm lỗi, hộp soạn bài vẫn mở…). Xem stderr và các tệp trong `debug_images`. **Không tự đăng lại ngay**: bài có thể đã lên. |
| 2 | `session_expired` | Chưa đăng nhập, phiên hết hạn hoặc bị checkpoint. **Báo người dùng chạy `python login.py` hoặc đăng nhập trong tab Cài đặt của app.** Không tự làm. |

`enqueue.py`: `0` thành công, `1` lỗi tham số hoặc dữ liệu, `3` vì `app.py` đang mở.

## Điều kiện cần có

1. Python >= 3.8, `pip install -r requirements.txt`, rồi `playwright install chromium`.
2. Một phiên đăng nhập hợp lệ trong `state.json`, tạo bằng `login.py` (người dùng đăng nhập tay, gồm cả 2FA). Kiểm tra bằng `python post.py --list-pages --json`: `session_saved_at` là `null` nghĩa là cần đăng nhập lại.
3. Fanpage/nhóm lưu trong `config.json` (xem `config.example.json`). Chưa có thì người dùng thêm trong tab **Fanpage & Mẫu** của app; agent vẫn đăng được bằng URL do người dùng đưa.

## Điều tuyệt đối không làm

- Không đọc, in, sao chép, commit `state.json` (chứa cookie đăng nhập). Chỉ cần biết nó có tồn tại hay không.
- Không tự chạy `login.py`, không nhập mật khẩu hay mã xác thực thay người dùng.
- Không sửa tay `queue.json` / `config.json` khi `app.py` đang mở: app giữ khóa `app.lock` và sẽ ghi đè. Dùng `enqueue.py`, nó từ chối khi app đang mở.
- Không đăng thật khi người dùng chưa đồng ý nội dung và nơi đăng. Đăng bài là hành động công khai, không thu hồi được bằng công cụ này. Nên chạy `--dry-run` trước và xin xác nhận.
- Không đăng lặp lại cùng một nội dung hoặc đăng dồn dập: Facebook coi đó là spam và có thể khóa tài khoản. Giữ giãn cách giữa các nơi (mặc định 5 phút).

## Cấu trúc mã nguồn

| Tệp | Vai trò |
|---|---|
| `post.py` | CLI và logic đăng bài (`make_post`, `publish`). Bộ chọn giao diện Facebook nằm ở `CREATE_POST_SELECTORS`, `TEXTBOX_SELECTORS`, `PUBLISH_WORDS`, `NEXT_WORDS`; sửa ở đây khi Facebook đổi giao diện. |
| `login.py` | Mở trình duyệt để người dùng đăng nhập tay và lưu `state.json`. |
| `enqueue.py` | CLI thêm/xem/xóa bài hẹn giờ trong `queue.json`. |
| `app.py` | Giao diện Tkinter, bộ hẹn giờ chạy bài khi đến giờ. |
| `scheduler.py` | Logic hẹn giờ thuần (không giao diện, không ghi đĩa). |
| `store.py` | Đọc/ghi `config.json`, `queue.json`, kiểm tra phiên, khóa `app.lock`. |
| `runner.py` | Chạy `post.py`/`login.py` như tiến trình con cho giao diện. |
| `tests/` | Unit test (`python -m unittest`, khoảng 1 phút, không cần mạng). |

Dữ liệu riêng của người dùng, đã nằm trong `.gitignore`: `state.json`, `config.json`, `queue.json`, `app.lock`, `debug_*.png`.

## Khi sửa mã

- Chạy `python -m unittest` trước khi báo xong. Test dùng trang Facebook giả cục bộ, không chạm mạng thật.
- Nhật ký của `post.py` cố ý viết tiếng Việt không dấu vì stdout có thể dùng bảng mã không hỗ trợ tiếng Việt; giao diện thì dùng tiếng Việt có dấu.
- Thêm tùy chọn CLI nào thì cập nhật bảng trong tệp này và `README.md`.
