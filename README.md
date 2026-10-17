# Hướng dẫn Tự động hóa Đăng bài Facebook bằng Playwright (Python)

Tài liệu này hướng dẫn cách chạy hai tập lệnh Python để tự động đăng nhập và đăng bài lên Facebook. Việc sử dụng Playwright giúp khôi phục phiên hoạt động từ tệp tin đã lưu (`state.json`), giúp giảm thiểu tối đa nguy cơ bị Facebook đánh dấu là đăng nhập bất thường hoặc checkpoint.

## Yêu cầu hệ thống

Bạn cần cài đặt sẵn **Python** (phiên bản >= 3.8) trên máy tính của mình.

## Cấu trúc dự án
- [login.py](file:///d:/WorkSpace/Assistant/login.py): Dùng để mở trình duyệt thực hiện đăng nhập thủ công lần đầu và lưu trạng thái phiên đăng nhập.
- [post.py](file:///d:/WorkSpace/Assistant/post.py): Sử dụng session đã lưu ở trên để tự động vào Facebook và đăng nội dung bài viết chỉ định.
- [app.py](file:///d:/WorkSpace/Assistant/app.py): Giao diện desktop (Tkinter) để đăng nhập, soạn bài, quản lý Fanpage/mẫu nội dung và hẹn giờ đăng bài.
- [store.py](file:///d:/WorkSpace/Assistant/store.py), [scheduler.py](file:///d:/WorkSpace/Assistant/scheduler.py), [runner.py](file:///d:/WorkSpace/Assistant/runner.py): Phần lưu cấu hình, logic hẹn giờ và chạy script cho giao diện.
- [enqueue.py](file:///d:/WorkSpace/Assistant/enqueue.py): Dòng lệnh để thêm/sửa/xóa/nhập CSV bài hẹn giờ không cần mở giao diện.
- [daemon.py](file:///d:/WorkSpace/Assistant/daemon.py): Chương trình nền đăng bài đến giờ mà không cần cửa sổ.
- [notify.py](file:///d:/WorkSpace/Assistant/notify.py), [history.py](file:///d:/WorkSpace/Assistant/history.py), [spam.py](file:///d:/WorkSpace/Assistant/spam.py), [variants.py](file:///d:/WorkSpace/Assistant/variants.py), [backup.py](file:///d:/WorkSpace/Assistant/backup.py): thông báo, lịch sử đăng, chống trùng/quá nhiều, biến thể nội dung, sao lưu.
- [mcp_server.py](file:///d:/WorkSpace/Assistant/mcp_server.py): MCP server để AI agent gọi các công cụ này.
- [ai.py](file:///d:/WorkSpace/Assistant/ai.py), [ai_features.py](file:///d:/WorkSpace/Assistant/ai_features.py), [ai_cli.py](file:///d:/WorkSpace/Assistant/ai_cli.py), [cards.py](file:///d:/WorkSpace/Assistant/cards.py): tính năng AI (Claude): soạn và dịch bài, kiểm tra, chuỗi bài, trợ lý hẹn giờ, giải thích lỗi, ảnh chữ.
- [AGENTS.md](file:///d:/WorkSpace/Assistant/AGENTS.md) (`CLAUDE.md` trỏ tới cùng nội dung) và `.claude/skills/facebook-post/`: hướng dẫn cho AI agent dùng phần mềm này.
- `tests/`: Unit test, chạy bằng `python -m unittest`.

---

## Dùng giao diện (khuyến nghị)

Sau khi cài Playwright ở Bước 1 bên dưới, chạy:

```bash
python app.py
```

Giao diện có 7 tab:

| Tab | Chức năng |
|---|---|
| **Đăng bài** | Chọn một hoặc nhiều nơi đăng (trang cá nhân, Fanpage, nhóm), soạn nội dung hoặc chèn mẫu, chọn một ảnh hoặc video, rồi **Đăng ngay** hoặc **Thêm vào hàng đợi** kèm thời điểm và kiểu lặp. |
| **Hàng đợi** | Danh sách bài đã hẹn giờ và trạng thái (Chờ / Đang chạy / Xong / Lỗi / Lỡ). Có thể đăng ngay, sửa, xóa từng bài. |
| **Lịch** | Xem hàng đợi theo **tuần** hoặc **tháng**. Kéo một bài đang chờ sang ô khác để đổi giờ (tuần: làm tròn 15 phút; tháng: đổi ngày, giữ giờ), nhấp đúp để sửa. Bài lặp hiện thêm các lần chạy sau dưới dạng ô mờ. |
| **Trợ lý AI** | Soạn bài từ ý tưởng cho nhiều nơi cùng lúc (mỗi nơi một bản theo ngôn ngữ và giọng văn của nó), lập chuỗi bài, trợ lý hẹn giờ bằng hội thoại, gợi ý trả lời bình luận, mô tả ảnh, tạo ảnh chữ. Xem mục “Tính năng AI” bên dưới. |
| **Fanpage & Mẫu** | Lưu các Fanpage (tên + URL + ngôn ngữ) và các mẫu dùng lại nhiều lần. Ô **Ngôn ngữ** ghi trang thuộc nước nào và dùng ngôn ngữ gì, ví dụ "English (Mỹ)": chọn trong danh sách có sẵn hoặc tự gõ; ngôn ngữ hiện cạnh tên trang trong danh sách Fanpage và trong hộp **Chọn nơi đăng…**. Mỗi mẫu gồm nội dung và có thể kèm một ảnh hoặc video; khi chèn mẫu, tệp đính kèm được điền theo. |
| **Cài đặt** | Đăng nhập Facebook và lưu phiên, bật/tắt chế độ chạy ẩn trình duyệt, mở ảnh kết quả gần nhất. |
| **Nâng cao** | **Trợ lý AI** (khóa API, mô hình, giọng chung), **Tài khoản** (nhiều tài khoản Facebook, mỗi tài khoản một phiên), **Thông báo** (Telegram, webhook, email), **An toàn & thử lại** (số lần thử lại, giới hạn chống trùng/spam, thời gian "Lỡ") và **Sao lưu & chạy nền**. |

Lần đầu sử dụng: vào tab **Cài đặt** → **Đăng nhập Facebook…** → đăng nhập trong cửa sổ trình duyệt → quay lại bấm **Tôi đã đăng nhập xong**.

Đăng lên nhiều nơi:
- Ở mục **Đăng lên**, bấm **Chọn nơi đăng…** để mở danh sách tất cả các nơi, tích vào những nơi muốn đăng rồi bấm **OK**. Màn hình chính hiển thị các nơi đã chọn.
- Mỗi nơi được chọn thành một bài riêng trong tab **Hàng đợi** và được đăng lần lượt, cách nhau số phút ở ô **Giãn cách (phút)** (mặc định 5, đặt 0 để đăng liền nhau).
- Giãn cách được tính từ lúc bài trước **đăng xong**. Nếu bài trước tải video lâu hơn dự kiến, bài sau tự lùi lại và cột **Ghi chú** ghi "Chờ giãn cách đến …"; cột **Giờ chạy** vẫn hiện giờ dự kiến ban đầu.
- Với **Đăng ngay**, nơi đầu tiên được đăng ngay, các nơi sau chờ theo giãn cách. Với **Thêm vào hàng đợi**, nơi đầu tiên chạy đúng giờ hẹn.
- Một nơi bị lỗi không chặn các nơi sau; có thể đăng lại riêng nơi đó từ tab Hàng đợi.
- Đăng ngay lên nhiều nơi cần bật **Tự động đăng khi đến giờ** và phải để ứng dụng mở cho đến khi bài cuối chạy xong.

Lưu ý về hẹn giờ:
- Bài chỉ được đăng khi **ứng dụng đang mở** hoặc **chương trình nền** (`python daemon.py run`, xem bên dưới) đang chạy. Thời điểm nhập theo dạng `YYYY-MM-DD HH:MM`.
- Bài quá giờ hơn 5 phút (chỉnh ở Nâng cao → An toàn & thử lại, hoặc `grace_minutes`) lúc ứng dụng đang tắt sẽ được đánh dấu **Lỡ** và không tự đăng bù. Bài lặp thì chuyển sang lần kế tiếp. Điều này cũng áp dụng khi ứng dụng đang mở nhưng máy ngủ (sleep) hoặc ô **Tự động đăng khi đến giờ** bị tắt quá thời gian đó.
- Ô **Lặp** có: không lặp, hằng ngày, hằng tuần, Thứ 2 đến Thứ 6, hằng tháng (ngày 29–31 lùi về ngày cuối của tháng ngắn rồi trở lại), hoặc **Chọn các thứ…**. Ô **Lệch ±** thêm một độ lệch ngẫu nhiên tối đa ± số phút vào mỗi lần lặp để giờ đăng không đều tăm tắp.
- Có thể dùng `{page}`, `{language}`, `{date}`, `{time}`, `{weekday}` trong nội dung (điền theo từng nơi đăng) và `{Xin chào|Chào bạn|Hello}` (chọn ngẫu nhiên một cụm cho mỗi nơi). Ví dụ "Chào {page}!" đăng lên 3 trang cho ra 3 bài có tên trang khác nhau, giúp tránh bị coi là đăng lặp.
- **Tự thử lại:** nếu bài lỗi *trước* khi bấm nút Đăng (mạng, Facebook chậm…), bài được xếp lại sau vài phút, tối đa 2 lần. Lỗi *sau* khi đã bấm Đăng không bao giờ thử lại vì bài có thể đã lên và sẽ bị đăng trùng.
- **Chống spam:** `post.py` từ chối (mã 5, trạng thái "Bị chặn") khi nội dung giống ≥ 90% một bài đã đăng lên cùng nơi trong 7 ngày, hoặc đã đăng quá 3 bài/nơi hay 15 bài trong 24 giờ. Bài lặp được phép giống nhau. Chỉnh hoặc tắt (đặt 0) ở Nâng cao → An toàn & thử lại.
- **Link bài:** sau khi đăng, chương trình tìm bài vừa đăng trên trang và lưu địa chỉ vào cột **Link bài** (nút **Mở link bài**). Nếu không thấy bài (nhóm kiểm duyệt…), ghi chú nói rõ "chưa thấy bài trên trang". Bài chỉ có ảnh/video không có chữ để nhận ra nên không có link.
- Mỗi lúc chỉ đăng một bài; các bài đến giờ cùng lúc sẽ được đăng lần lượt. Bài bị trễ vì phải chờ bài khác đăng xong vẫn được đăng, không bị tính là Lỡ.
- Chỉ chạy **một** trong cửa sổ ứng dụng và chương trình nền cho mỗi thư mục. Lần chạy thứ hai sẽ báo lỗi và thoát, để tránh hai bản cùng đăng trùng một bài hẹn giờ.
- Đăng lặp lại cùng một nội dung dễ bị Facebook coi là spam.
- Mẫu chỉ lưu **đường dẫn** tới tệp ảnh/video, không sao chép tệp. Nếu tệp bị xóa hoặc chuyển chỗ, bài đăng dùng mẫu đó sẽ báo lỗi.
- Với video, script chờ hộp soạn bài đóng lại (tối đa 10 phút) rồi mới đóng trình duyệt, để không cắt ngang việc tải lên.
- Trạng thái **Xong** nghĩa là Facebook đã nhận bài: sau khi bấm Đăng, hộp soạn bài đóng lại. Nếu nút Đăng không bấm được (tệp đính kèm chưa tải xong) hoặc hộp soạn bài vẫn mở sau 2 phút, bài được đánh dấu **Lỗi** kèm ảnh trong thư mục `debug/` (`..._post_button.png`, `..._not_posted.png`; mỗi ảnh có giờ trong tên, bản mới nhất mỗi loại còn được chép thành `latest_<loại>.png`, tối đa 60 ảnh). Nhóm có kiểm duyệt vẫn có thể giữ bài chờ duyệt, nên hãy xem cột **Link bài**, mở `debug/latest_result.png` hoặc vào Facebook khi cần chắc chắn.
- Khi Facebook đăng xuất phiên đã lưu hoặc yêu cầu xác minh (checkpoint), bài được đánh dấu **Lỗi** với ghi chú "Phiên đăng nhập Facebook đã hết hạn…" và tab **Cài đặt** báo cần đăng nhập lại.

Cài đặt và hàng đợi được lưu trong `config.json` và `queue.json` cùng thư mục. Hai tệp này là dữ liệu riêng của bạn (tên nhóm, đường dẫn tệp) nên không được đưa vào git; ứng dụng tự tạo `config.json` ở lần chạy đầu, xem `config.example.json` để biết cấu trúc. Mục nào trong `queue.json` bị hỏng sẽ được bỏ qua lúc khởi động và bản gốc được giữ lại ở `queue.json.corrupt`.

---

## Các bước thực hiện

### Bước 1: Cài đặt thư viện Playwright
Mở terminal/cmd tại thư mục này và chạy các lệnh sau:

```bash
# Cài đặt thư viện Playwright cho Python
pip install -r requirements.txt

# Cài đặt trình duyệt đi kèm của Playwright
playwright install chromium
```

### Bước 2: Đăng nhập và lưu Session (`login.py`)
Mục đích của bước này là giúp bạn vượt qua cơ chế bảo mật (nhập mật khẩu, xác thực 2 lớp - 2FA) một cách an toàn nhất bằng cách đăng nhập thủ công trên trình duyệt thật.

Chạy lệnh:
```bash
python login.py
```

*Một cửa sổ trình duyệt Chromium sẽ được mở ra:*
1. Bạn hãy tiến hành đăng nhập vào Facebook như bình thường.
2. Hoàn thành các bước xác thực bảo mật (nếu có).
3. Đợi trang chủ Facebook tải hoàn tất (nhìn thấy bảng tin của bạn).
4. Quay lại cửa sổ dòng lệnh (terminal) và nhấn **ENTER** để lưu session. Nếu Facebook chưa ghi nhận đăng nhập, script sẽ báo và chờ bạn nhấn **ENTER** lần nữa thay vì lưu một phiên chưa đăng nhập.
5. Tệp tin `state.json` sẽ được tạo ra chứa cookies và thông tin đăng nhập của bạn.

> **CẢNH BÁO:**
> Tệp `state.json` chứa thông tin đăng nhập quan trạng (cookies đăng nhập của bạn). **Tuyệt đối không chia sẻ tệp này cho bất kỳ ai** để bảo vệ tài khoản cá nhân.

### Bước 3: Tự động đăng bài (`post.py`)
Sau khi đã có tệp `state.json`, bạn có thể thực hiện tự động hóa đăng bài viết bằng cách chạy lệnh:

```bash
python post.py "Nội dung bài viết bạn muốn đăng tải ở đây"
```

Các tùy chọn thêm:

| Tùy chọn | Ý nghĩa |
|---|---|
| `--page <URL>` | Đăng lên Fanpage thay vì trang cá nhân. |
| `--image <đường dẫn>` | Đính kèm ảnh hoặc video; lặp lại (hoặc `a.jpg|b.jpg`) để đính kèm nhiều tệp. Nếu không tải được tệp, script dừng lại và không đăng bài. |
| `--account <tên>` | Dùng tài khoản phụ (phiên `state.<tên>.json`, đăng nhập bằng `python login.py --account <tên>`). |
| `--headless` | Chạy ẩn trình duyệt. |
| `--content-file <tệp>` | Đọc nội dung từ tệp văn bản UTF-8 (tiện cho bài dài, nhiều dòng). |
| `--dry-run` | Soạn bài và chụp ảnh `dry_run` nhưng **không bấm Đăng**, để thử. Cảnh báo nếu bài sẽ bị chặn khi đăng thật. |
| `--allow-duplicate` / `--force` | Bỏ qua việc kiểm tra trùng nội dung / bỏ qua mọi kiểm tra chống spam. |
| `--selftest` | Mở Facebook, báo bộ chọn nào còn khớp với giao diện (nút tạo bài, ô nhập, nút Đăng) mà không đăng gì. Chạy khi bài hay lỗi để biết Facebook đã đổi giao diện chưa. |
| `--json` | In kết quả thành một dòng JSON trên stdout (`ok`, `status`, `exit_code`, `message`, `retryable`, `post_url`, `verified`, `warnings`, `debug_images`); nhật ký chuyển sang stderr. |
| `--list-pages` | Liệt kê Fanpage/nhóm và mẫu trong `config.json` cùng tình trạng phiên đăng nhập, rồi thoát (không đăng). |

Script thoát với mã `0` khi bài đã được đăng, mã `1` khi thất bại *sau* khi đã bấm Đăng (bài có thể đã lên: hãy kiểm tra), mã `2` khi chưa có `state.json` hoặc phiên đăng nhập đã hết hạn (cần chạy lại `login.py`), mã `4` khi thất bại *trước* khi bấm Đăng (chắc chắn chưa đăng, thử lại được) và mã `5` khi bị chặn bởi bộ chống spam. Mọi lần đăng đều được ghi vào `history.jsonl` (xem bằng `python history.py`). `state.json` luôn được tìm cạnh các script, chạy lệnh từ thư mục nào cũng được. Sau mỗi lần đăng thành công, `state.json` được lưu lại với cookie mới nhất để phiên dùng được lâu hơn.

*Cách thức hoạt động:*
1. Script sẽ mở trình duyệt Chromium và tải dữ liệu từ tệp `state.json` (bạn sẽ được tự động đăng nhập vào Facebook mà không cần nhập lại mật khẩu).
2. Tự động tìm kiếm nút "Bạn đang nghĩ gì thế?" / "What's on your mind?".
3. Mở khung soạn thảo văn bản, tự động điền nội dung bài đăng truyền vào từ dòng lệnh.
4. Tìm nút "Đăng" (hoặc "Post") trong hộp soạn bài, chờ nút hết bị mờ rồi nhấp chuột, sau đó chờ hộp soạn bài đóng lại để xác nhận bài đã được gửi.
5. Nếu xảy ra lỗi không tìm thấy nút, chương trình sẽ tự động chụp ảnh màn hình vào thư mục `debug/` để bạn dễ dàng tìm nguyên nhân (ví dụ: giao diện Facebook thay đổi).

### Hẹn giờ bằng dòng lệnh (`enqueue.py`)

```bash
python enqueue.py add --content-file bai.txt --page "Tên Fanpage" --page personal --at "2026-10-20 08:30"
python enqueue.py list [--status pending]
python enqueue.py edit <id> --at "2026-10-21 09:00" [--content-file moi.txt] [--page ...] [--clear-image]
python enqueue.py remove <id>
python enqueue.py csv-template > bai.csv      # xem các cột
python enqueue.py import bai.csv [--dry-run]  # nhập nhiều bài; sai một dòng là không thêm gì
```

`--page` nhận tên Fanpage đã lưu, URL hoặc `personal`; lặp lại để đăng nhiều nơi (cách nhau `--gap` phút, mặc định lấy từ `config.json`). `add` còn nhận `--image` (lặp được), `--repeat none|daily|weekly|weekdays|monthly` hoặc `--days mon,wed,fri`, `--jitter N`, `--account`, `--allow-duplicate`, `--strict` (không thêm nếu bị cảnh báo trùng) và `--content-file` lặp nhiều lần để các bản nội dung thay phiên nhau giữa các nơi. Thêm `--json` ngay sau `enqueue.py` để có kết quả dạng JSON.

Vì app giữ hàng đợi trong bộ nhớ và sẽ ghi đè `queue.json`, mọi lệnh thay đổi hàng đợi từ chối chạy (mã thoát `3`) khi **app** đang mở. Với **chương trình nền** thì không sao: nó đọc lại `queue.json` mỗi lượt nên nhận ngay bài mới.

### Chạy nền không cần cửa sổ (`daemon.py`)

```bash
python daemon.py run             # đăng các bài đến giờ; Ctrl+C để dừng (hoặc: python app.py --daemon)
python daemon.py status          # đang chạy hay không, còn bao nhiêu bài chờ
python daemon.py install-task    # Windows: tự chạy mỗi khi đăng nhập (Task Scheduler, không hiện cửa sổ)
python daemon.py uninstall-task
```

Chương trình nền cần phiên đăng nhập hợp lệ và máy đang thức: bài đến giờ lúc máy ngủ quá thời gian cho phép sẽ bị đánh dấu **Lỡ**. Nhật ký ghi ở `daemon.log`. Bạn vẫn xem và sửa hàng đợi được bằng `enqueue.py`; muốn mở cửa sổ ứng dụng thì dừng chương trình nền trước.

### Thông báo, nhiều tài khoản, sao lưu

- **Thông báo** (Nâng cao → Thông báo, hoặc mục `notifications` trong `config.json`): Telegram (tạo bot bằng @BotFather, lấy token và chat ID), webhook JSON (nối tới Zalo, Make, n8n… vì Zalo không có API gửi tin trực tiếp cho cá nhân) và email SMTP, cho các sự kiện đăng xong / lỗi / hết hạn phiên / lỡ / bị chặn. Token và mật khẩu cũng đọc được từ biến môi trường `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `NOTIFY_WEBHOOK_URL`, `SMTP_PASSWORD`. Thử bằng `python notify.py --test`.
- **Nhiều tài khoản:** thêm tên ở Nâng cao → Tài khoản, bấm "Đăng nhập tài khoản đã chọn…", rồi chọn tài khoản ở tab Đăng bài. Bài hẹn giờ nhớ tài khoản của nó.
- **Sao lưu:** `python backup.py export tệp.json` (hoặc nút ở Nâng cao → Sao lưu & chạy nền) ghi Fanpage, mẫu, cài đặt và hàng đợi; phiên đăng nhập, token và mật khẩu không bao giờ được đưa vào trừ khi thêm `--include-secrets`. `import` gộp vào dữ liệu hiện có (hoặc `--replace`), sao lưu bản cũ thành `*.bak-<giờ>` và liệt kê những tệp đính kèm chưa có trên máy mới.

### Tính năng AI (Claude)

Cần khóa API Anthropic: đặt biến môi trường `ANTHROPIC_API_KEY` hoặc nhập ở Nâng cao → Trợ lý AI (lưu trong `config.json`), rồi kiểm tra bằng `python ai.py`. Mỗi lần gọi tính phí theo lượng dùng của khóa (mô hình mặc định `claude-opus-5-5`; đổi sang `claude-sonnet-5-5` hoặc `claude-haiku-5-5` để rẻ hơn) và được ghi chi phí ước tính vào nhật ký. **Nội dung bạn nhờ AI xử lý được gửi tới Anthropic.** Không có gì được gửi nếu bạn không bấm nút AI hay chạy lệnh `ai_cli.py`.

| Tính năng | Ở đâu | Ghi chú |
|---|---|---|
| Soạn bài từ ý tưởng, mỗi nơi một bản | Trợ lý AI → Soạn bài; `ai_cli.py draft` | Dùng ngôn ngữ và **Giọng văn** của từng Fanpage (tab Fanpage & Mẫu). Mỗi nơi diễn đạt khác nhau để tránh bị coi là đăng lặp. Ô “Thông tin có thật” là những gì AI được phép dùng cho giá, ngày, địa chỉ. |
| Dịch và địa phương hóa | Menu “✨ AI giúp…”; `localize` | Dịch bài đang soạn cho các nơi đã chọn. |
| Biến thể, sửa chính tả, hashtag, mở bài, lời kêu gọi, emoji, rút gọn, mở rộng | Menu “✨ AI giúp…”; `variants`, `polish` | Một lần Ctrl+Z hoàn tác. Mở rộng không được thêm dữ kiện mới. |
| Kiểm tra trước khi đăng | Menu “✨ AI giúp…”; `review` | Chính tả, giọng điệu, tuyên bố đáng ngờ, thứ Facebook có thể hạn chế (mồi tương tác, viết hoa toàn bộ…). Chỉ là lời khuyên. |
| Chuỗi bài | Trợ lý AI → Chuỗi bài; `series` | AI viết các bài ở các góc khác nhau; **giờ đăng do chương trình tính** (từ ngày, giờ, khoảng cách hoặc các thứ). Có thể sửa từng bài rồi thêm vào hàng đợi. |
| Gợi ý giờ đăng | Chuỗi bài → “Gợi ý giờ đăng”; `times` | Gợi ý **chung** theo thói quen người dùng và lịch sử đăng của trang; chương trình không có số liệu tương tác thật nên không phải là kết quả phân tích dữ liệu. |
| Trợ lý hẹn giờ | Trợ lý AI → Trợ lý hẹn giờ; `assistant` | “Đăng bài chào hè lên Hi English 8h sáng mai”. Chỉ đề xuất, bạn xem và bấm thêm vào hàng đợi. Ngày giờ tương đối được tính theo đồng hồ máy; nơi đăng sai tên hoặc giờ đã qua bị loại. |
| Trả lời bình luận | Trợ lý AI → Công cụ; `reply` | Bạn dán bình luận vào; chương trình không đọc hay gửi bình luận. Cảnh báo bình luận spam/khiếu nại. AI được dặn không hứa giá, ngày, hoàn tiền. |
| Mô tả ảnh | Menu “✨ AI giúp…”; `describe` | Alt text và chú thích cho ảnh (không đọc video). |
| Ảnh chữ | Menu “✨ AI giúp…”; `card` | AI chọn tiêu đề, ý chính và tông màu; chương trình vẽ ảnh vuông từ mẫu có sẵn. **Không phải ảnh do AI tạo hay ảnh chụp** — Claude không tạo ảnh. |
| Giải thích lỗi | Hàng đợi → “✨ Giải thích lỗi”; `explain` | Đọc ghi chú lỗi, nhật ký và ảnh chụp lỗi mới nhất rồi nói bằng tiếng Việt: vì sao, làm gì, có thử lại được không, có cần kiểm tra Facebook không. Tùy chọn “tự giải thích trong thông báo” (mặc định tắt) làm việc này ngay khi bài lỗi. |
| Đề xuất bộ chọn khi Facebook đổi giao diện | `ai_cli.py selectors` | Chạy `post.py --selftest`, gửi cấu trúc hộp soạn bài **đã lọc** (không liên kết, kiểu dáng, ảnh; chữ cắt ngắn; không có bài trong bảng tin) cho AI, rồi tự kiểm tra từng đề xuất trên cấu trúc đó và bỏ cái không khớp. Không tự sửa `post.py`. |

Đọc kỹ mọi bản nháp trước khi đăng: AI có thể viết sai hoặc lệch giọng, và đăng hàng loạt nội dung do AI viết dễ bị Facebook coi là spam (bộ chống spam vẫn áp dụng). Nhiều nơi yêu cầu ghi rõ nội dung do AI tạo.

### Dùng cùng AI agent

Xem [AGENTS.md](file:///d:/WorkSpace/Assistant/AGENTS.md): lệnh nên dùng, mã thoát, những việc agent không được tự làm (đọc `state.json`, tự đăng nhập, đăng thật khi chưa được xác nhận).

Project có sẵn **MCP server** (`python mcp_server.py`, đã khai báo trong `.mcp.json` để Claude Code tự nhận) với các công cụ `list_pages`, `dry_run_post`, `post_now`, `schedule_post`, `list_queue`, `edit_job`, `remove_job`, `get_history`, `selftest`, `daemon_status`. Mọi công cụ đăng bài thật đòi `confirm=true`, và agent được dặn chỉ đặt sau khi bạn đồng ý rõ ràng. Cũng có skill `facebook-post` trong `.claude/skills/`.

---

## Xử lý sự cố thường gặp (Troubleshooting)

- **Lỗi không tìm thấy nút "Đăng" hoặc "Bạn đang nghĩ gì thế":** Do Facebook thường xuyên thay đổi giao diện (A/B testing hoặc đổi class name), nếu script không tìm thấy nút, chạy `python post.py --selftest` để biết bộ chọn nào đã hỏng và xem các ảnh chụp tự động trong thư mục `debug/` (`..._homepage.png`, `..._modal.png`) để thấy giao diện hiện tại, sau đó có thể bổ sung các bộ chọn CSS (Selector) mới vào mảng `CREATE_POST_SELECTORS` (nút mở hộp soạn bài) hoặc thêm nhãn nút vào `PUBLISH_WORDS` / `NEXT_WORDS` (nút Đăng, nút Tiếp) trong file `post.py`.
- **Đăng vào nhóm:** Dùng được URL nhóm (`facebook.com/groups/...`) ở ô URL như với Fanpage. Nhóm có kiểm duyệt sẽ đưa bài vào hàng chờ duyệt, nên bài có thể chưa hiện ngay dù trạng thái là "Xong".
- **Checkpoint / Yêu cầu xác minh thiết bị:** Nếu Facebook yêu cầu xác minh khi chạy `post.py`, hãy đăng nhập thủ công trên trình duyệt đó để xác nhận, sau đó chạy lại `login.py` để cập nhật lại tệp `state.json`.
