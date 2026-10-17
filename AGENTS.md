# Facebook Auto Poster — hướng dẫn cho agent

Phần mềm tự động đăng bài lên trang cá nhân, Fanpage và nhóm Facebook bằng Playwright (Chromium), dùng phiên đăng nhập đã lưu trong `state.json`. Có giao diện Tkinter (`app.py`), chương trình nền (`daemon.py`) và các CLI / MCP server cho agent.

## Agent dùng gì

Cách gọn nhất là **MCP server** (`.mcp.json` đã trỏ sẵn tới `mcp_server.py`): các công cụ `list_pages`, `dry_run_post`, `post_now`, `schedule_post`, `list_queue`, `edit_job`, `remove_job`, `get_history`, `selftest`, `daemon_status`. Công cụ nào đăng bài thật (`post_now`, `schedule_post`, `edit_job`) đòi `confirm=true`, chỉ đặt sau khi người dùng đồng ý rõ ràng.

Nếu không dùng MCP, gọi thẳng các CLI (luôn thêm `--json`):

| Việc cần làm | Lệnh |
|---|---|
| Xem nơi đăng, mẫu, tài khoản và đã đăng nhập chưa | `python post.py --list-pages --json` |
| Thử soạn bài, **không đăng** | `python post.py --content-file bai.txt --page URL --dry-run --json` |
| Đăng ngay | `python post.py --content-file bai.txt --page URL [--image tệp ...] [--account tên] --json` |
| Kiểm tra bộ chọn giao diện còn đúng không (không đăng) | `python post.py --selftest --json` |
| Hẹn giờ | `python enqueue.py --json add --content-file bai.txt --page "Tên trang" --at "YYYY-MM-DD HH:MM"` |
| Xem / sửa / xóa bài hẹn giờ | `python enqueue.py --json list` / `edit ID --at ...` / `remove ID` |
| Nhập nhiều bài từ CSV | `python enqueue.py --json import bai.csv [--dry-run]` (`csv-template` in cột mẫu) |
| Xem lịch sử đăng | `python history.py --json [--limit 20] [--status posted]` |
| Chương trình nền | `python daemon.py run` / `status` / `install-task` |
| Thông báo thử | `python notify.py --test` |
| Trợ lý AI (soạn, dịch, kiểm tra, chuỗi bài, giải thích lỗi…) | `python ai_cli.py --json <lệnh>` — xem mục “Trợ lý AI” bên dưới |
| Sao lưu / khôi phục | `python backup.py export tệp.json` / `import tệp.json [--dry-run]` |
| Chạy test | `python -m unittest` |

Không dùng `app.py` (giao diện đồ họa, agent không điều khiển được) và không dùng `login.py`.

### Quy ước khi gọi

- Luôn viết nội dung ra tệp UTF-8 rồi dùng `--content-file`. Truyền nội dung trên dòng lệnh dễ hỏng vì emoji, xuống dòng và dấu nháy.
- `--page` của `post.py` là URL đầy đủ (lấy từ `--list-pages`); không có thì đăng lên trang cá nhân. `enqueue.py` còn nhận **tên Fanpage đã lưu** hoặc `personal`, và cho lặp `--page` nhiều lần: mỗi nơi là một bài riêng, cách nhau `--gap` phút (mặc định lấy từ `config.json`).
- Ảnh/video: lặp `--image` để đính kèm nhiều tệp (hoặc viết `a.jpg|b.jpg`). Video có thể mất tới 10 phút.
- Trong nội dung của `enqueue.py`/MCP có thể dùng `{page}`, `{language}`, `{date}`, `{time}`, `{weekday}` (điền theo từng nơi) và `{Xin chào|Chào bạn}` (chọn ngẫu nhiên). Lặp `--content-file` vài lần để các bản thay phiên nhau giữa các nơi. `--no-render` tắt việc này.
- Lặp lại: `--repeat none|daily|weekly|weekdays|monthly` hoặc `--days mon,wed,fri`; `--jitter N` lệch giờ ngẫu nhiên ± N phút mỗi lần.
- Nhiều tài khoản: khai báo tên trong `accounts` của `config.json` (hoặc tab Nâng cao của app), đăng nhập bằng `python login.py --account tên`, rồi dùng `--account tên`.
- Chạy từ thư mục nào cũng được: `state*.json`, `config.json`, `queue.json`, `history.jsonl` luôn nằm cạnh các script. Ảnh chụp lỗi nằm ở `debug/` (tên có giờ; `latest_<loại>.png` là bản mới nhất, giữ tối đa 60 ảnh).
- Dùng `--json`: stdout chỉ có đúng một dòng JSON, nhật ký tiến trình đi vào stderr.

### Kết quả và mã thoát của `post.py`

JSON: `{"ok", "status", "exit_code", "message", "retryable", "post_url", "verified", "warnings", "debug_images"}`.

| Mã | `status` | Ý nghĩa và việc cần làm |
|---|---|---|
| 0 | `posted` | Facebook đã nhận bài. `post_url` có giá trị: bài đã hiện trên trang (`verified: true`). `verified: false`: chưa thấy bài (nhóm kiểm duyệt?) — hãy kiểm tra trên Facebook. |
| 0 | `dry_run` | Đã soạn xong nhưng chưa đăng. `warnings` cho biết nếu bài sẽ bị chặn khi đăng thật. |
| 1 | `failed` | Thất bại **sau** khi đã bấm nút Đăng, hoặc lỗi dữ liệu (tệp đính kèm không tồn tại…). Bài **có thể đã lên**: xem `get_history`/Facebook trước khi đăng lại. |
| 2 | `session_expired` | Chưa đăng nhập, phiên hết hạn hoặc bị checkpoint. **Báo người dùng chạy `python login.py` (hoặc đăng nhập trong tab Cài đặt).** Không tự làm. |
| 4 | `failed`, `retryable: true` | Thất bại **trước** khi bấm Đăng (mạng, Facebook chậm, không tìm thấy nút…): chắc chắn chưa đăng, thử lại được. Hàng đợi tự thử lại `retry_attempts` lần. |
| 5 | `blocked` | Bị chặn không mở trình duyệt: nội dung giống bài đã đăng lên cùng nơi, hoặc quá `max_per_page_per_day` / `max_per_day` trong 24 giờ. `--allow-duplicate` bỏ qua việc kiểm tra trùng, `--force` bỏ qua mọi kiểm tra; chỉ dùng khi người dùng đồng ý. |

`enqueue.py`, `backup.py`: `0` thành công, `1` lỗi tham số hoặc dữ liệu, `3` vì `app.py` đang mở (thêm/sửa/xóa không làm được; đang chạy `daemon.py` thì vẫn làm được). `daemon.py run`: `3` nếu đã có bản khác đang chạy.

Bài có thể được đăng rồi mà vẫn báo lỗi nếu lỗi xảy ra sau khi bấm Đăng — vì vậy mã 1 không bao giờ được tự thử lại.

## Trợ lý AI (Claude)

`ai_cli.py` và tab **Trợ lý AI** của app dùng Claude (SDK `anthropic`, mô hình mặc định `claude-opus-5-5`) cho: `draft` (soạn bài từ ý tưởng, mỗi nơi một bản đúng ngôn ngữ và giọng văn của trang), `localize` (dịch/địa phương hóa), `variants`, `polish fix|shorten|expand|hashtags|title|cta|emoji`, `review` (chính tả, giọng điệu, rủi ro với quy định Facebook), `series` (chuỗi bài, giờ đăng do chương trình tính chứ không để AI đoán), `times` (gợi ý giờ), `reply` (gợi ý trả lời bình luận người dùng dán vào), `describe` (alt text ảnh), `card` (ảnh chữ), `explain` (vì sao bài lỗi), `selectors` (đề xuất bộ chọn mới khi Facebook đổi giao diện) và `assistant` (một câu nói thành các đề xuất hẹn giờ). Khóa API lấy từ `ANTHROPIC_API_KEY` hoặc mục `ai.api_key` trong `config.json`; kiểm tra bằng `python ai.py`.

- **AI chỉ đề xuất, không đăng và không hẹn giờ.** Kết quả là bản nháp để người xem lại; `--csv` ghi ra tệp cho `enqueue.py import`, không tự thêm vào hàng đợi. Đừng đưa bản nháp vào hàng đợi hay đăng khi người dùng chưa đọc và đồng ý.
- **Tốn tiền và gửi dữ liệu ra ngoài.** Mỗi lệnh gửi nội dung (bài viết, ảnh, nhật ký lỗi, cấu trúc trang đã lọc bớt) tới Anthropic và tính phí theo khóa của người dùng; chi phí ước tính nằm trong `usage` của kết quả. Chỉ gọi khi người dùng nhờ.
- `times` chỉ là gợi ý chung theo thói quen người dùng và lịch sử đăng của trang: chương trình **không có số liệu tương tác**, đừng trình bày như kết quả phân tích dữ liệu. `card` vẽ ảnh chữ từ mẫu có sẵn, không tạo ảnh bằng AI. `describe` chỉ đọc ảnh, không đọc video. `reply` không đọc hay gửi bình luận: người dùng dán vào.
- `selectors` đề xuất rồi tự kiểm tra từng bộ chọn trên cấu trúc trang đã lưu và bỏ cái không khớp, nhưng **không sửa `post.py`**: người dùng xem rồi mới tự thêm.
- Tùy chọn `ai.explain_failures` (mặc định tắt) thêm lời giải thích của AI vào thông báo khi bài lỗi, bằng cách gửi nhật ký và ảnh chụp lỗi cho Anthropic.

## Điều kiện cần có

1. Python >= 3.8, `pip install -r requirements.txt`, rồi `playwright install chromium`.
2. Một phiên đăng nhập hợp lệ trong `state.json`, tạo bằng `login.py` (người dùng đăng nhập tay, gồm cả 2FA). Kiểm tra bằng `--list-pages`: `session_saved_at` (hoặc `accounts[].session_saved_at`) là `null` nghĩa là cần đăng nhập lại.
3. Fanpage/nhóm lưu trong `config.json` (xem `config.example.json`). Chưa có thì người dùng thêm trong tab **Fanpage & Mẫu** của app; agent vẫn đăng được bằng URL do người dùng đưa.
4. Bài hẹn giờ chỉ được đăng khi `app.py` **hoặc** `daemon.py` đang chạy (không chạy cùng lúc).

## Điều tuyệt đối không làm

- Không đọc, in, sao chép, commit `state.json` / `state.<tên>.json` (chứa cookie đăng nhập). Chỉ cần biết nó có tồn tại hay không. Token Telegram và mật khẩu email trong `config.json` cũng vậy.
- Không tự chạy `login.py`, không nhập mật khẩu hay mã xác thực thay người dùng.
- Không sửa tay `queue.json` / `config.json` khi `app.py` hoặc `daemon.py` đang chạy: app ghi đè, chương trình nền đọc lại từng lượt. Dùng `enqueue.py` / `backup.py` / MCP — chúng khóa đúng cách và từ chối khi app đang mở.
- Không đăng thật khi người dùng chưa đồng ý nội dung và nơi đăng. Đăng bài là hành động công khai, không thu hồi được bằng công cụ này. Nên chạy `--dry-run` trước và xin xác nhận.
- Không dùng `--force` / `--allow-duplicate` để vượt qua việc bị chặn mà không hỏi: Facebook coi đăng lặp và đăng dồn dập là spam và có thể khóa tài khoản. Giữ giãn cách giữa các nơi (mặc định 5 phút).
- Không đăng lại ngay sau mã 1: xem lịch sử trước.

## Cấu trúc mã nguồn

| Tệp | Vai trò |
|---|---|
| `post.py` | CLI và logic đăng bài (`make_post`, `publish`, `find_post_url`, `selftest`). Bộ chọn giao diện Facebook nằm ở `CREATE_POST_SELECTORS`, `TEXTBOX_SELECTORS`, `PUBLISH_WORDS`, `NEXT_WORDS`; sửa ở đây khi Facebook đổi giao diện (`--selftest` cho biết cái nào hỏng). |
| `login.py` | Mở trình duyệt để người dùng đăng nhập tay và lưu `state.json` (`--account` cho tài khoản phụ). |
| `enqueue.py` | CLI thêm/sửa/xóa/nhập CSV bài hẹn giờ trong `queue.json`. |
| `daemon.py` | Chương trình nền đăng bài đến giờ không cần cửa sổ; cài vào Task Scheduler. |
| `jobflow.py` | Dùng chung cho app và daemon: đổi mã thoát của `post.py` thành trạng thái bài, thử lại, thông báo. |
| `app.py`, `calendar_tab.py`, `schedule_view.py` | Giao diện Tkinter; tab Lịch (tuần/tháng, kéo thả) và phần tính ngày của nó. |
| `scheduler.py` | Logic hẹn giờ thuần: lặp (daily, weekdays, monthly, days:…), lệch giờ, thử lại, bỏ lỡ. |
| `store.py` | Đọc/ghi `config.json`, `queue.json`, kiểm tra phiên, tên tài khoản, khóa `app.lock`/`queue.lock`. |
| `history.py`, `spam.py` | Nhật ký `history.jsonl` và các giới hạn chống đăng trùng / đăng quá nhiều. |
| `notify.py` | Thông báo qua Telegram, webhook (để nối Zalo…), email. |
| `variants.py` | `{page}`, `{date}`, `{a|b}` và việc cho các bản nội dung thay phiên nhau. |
| `backup.py` | Xuất/nhập cấu hình, mẫu, Fanpage, hàng đợi (không có đăng nhập). |
| `mcp_server.py` | MCP server qua stdio (không cần thư viện ngoài). |
| `ai.py`, `ai_features.py`, `ai_cli.py`, `cards.py` | Gọi Claude (một chỗ duy nhất: `ai.AI.ask`, trả về JSON theo schema), các tính năng AI, CLI của chúng và bộ vẽ ảnh chữ. |
| `runner.py` | Chạy `post.py`/`login.py` như tiến trình con. |
| `tests/` | Unit test (`python -m unittest`, vài phút, không cần mạng; một số test mở Chromium/Tk cục bộ). |

Dữ liệu riêng của người dùng, đã nằm trong `.gitignore`: `state*.json`, `config.json` (có thể chứa khóa API), `queue.json`, `history.jsonl`, `debug/`, `cards/`, `app.lock`, `daemon.lock`, `queue.lock`, `daemon.log`, `daemon.status.json`, `*.bak-*`.

## Khi sửa mã

- Chạy `python -m unittest` trước khi báo xong. Test dùng trang Facebook giả cục bộ, không chạm mạng thật; chúng đổi `DEBUG_DIR`, `HISTORY_PATH`, `CONFIG_PATH` sang thư mục tạm — đừng viết test ghi vào thư mục dự án.
- Nhật ký của `post.py` cố ý viết tiếng Việt không dấu vì stdout có thể dùng bảng mã không hỗ trợ tiếng Việt; giao diện thì dùng tiếng Việt có dấu.
- `post.py` in thêm các dòng `POST_URL=…` và `POST_VERIFIED=yes|no` để app và daemon đọc; mã thoát và các dòng này là giao diện giữa `post.py` và `jobflow.py`/`runner.py`.
- Thêm tùy chọn CLI nào thì cập nhật bảng trong tệp này, `README.md` và công cụ tương ứng trong `mcp_server.py`.
- Code gọi Claude dùng SDK `anthropic` (không viết HTTP thô), không đặt `temperature`/`top_p`/`budget_tokens` (mô hình hiện tại từ chối), và luôn đọc `stop_reason` trước khi dùng kết quả. Test của AI dùng `FakeEngine` (`tests/test_ai_features.py`) hoặc client giả: đừng viết test gọi API thật.
