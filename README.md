# Hướng dẫn Tự động hóa Đăng bài Facebook bằng Playwright (Python)

Tài liệu này hướng dẫn cách chạy hai tập lệnh Python để tự động đăng nhập và đăng bài lên Facebook. Việc sử dụng Playwright giúp khôi phục phiên hoạt động từ tệp tin đã lưu (`state.json`), giúp giảm thiểu tối đa nguy cơ bị Facebook đánh dấu là đăng nhập bất thường hoặc checkpoint.

## Yêu cầu hệ thống

Bạn cần cài đặt sẵn **Python** (phiên bản >= 3.8) trên máy tính của mình.

## Cấu trúc dự án
- [login.py](file:///d:/WorkSpace/Assistant/login.py): Dùng để mở trình duyệt thực hiện đăng nhập thủ công lần đầu và lưu trạng thái phiên đăng nhập.
- [post.py](file:///d:/WorkSpace/Assistant/post.py): Sử dụng session đã lưu ở trên để tự động vào Facebook và đăng nội dung bài viết chỉ định.
- [app.py](file:///d:/WorkSpace/Assistant/app.py): Giao diện desktop (Tkinter) để đăng nhập, soạn bài, quản lý Fanpage/mẫu nội dung và hẹn giờ đăng bài.
- [store.py](file:///d:/WorkSpace/Assistant/store.py), [scheduler.py](file:///d:/WorkSpace/Assistant/scheduler.py), [runner.py](file:///d:/WorkSpace/Assistant/runner.py): Phần lưu cấu hình, logic hẹn giờ và chạy script cho giao diện.
- `tests/`: Unit test, chạy bằng `python -m unittest`.

---

## Dùng giao diện (khuyến nghị)

Sau khi cài Playwright ở Bước 1 bên dưới, chạy:

```bash
python app.py
```

Giao diện có 4 tab:

| Tab | Chức năng |
|---|---|
| **Đăng bài** | Chọn một hoặc nhiều nơi đăng (trang cá nhân, Fanpage, nhóm), soạn nội dung hoặc chèn mẫu, chọn một ảnh hoặc video, rồi **Đăng ngay** hoặc **Thêm vào hàng đợi** kèm thời điểm và kiểu lặp. |
| **Hàng đợi** | Danh sách bài đã hẹn giờ và trạng thái (Chờ / Đang chạy / Xong / Lỗi / Lỡ). Có thể đăng ngay, sửa, xóa từng bài. |
| **Fanpage & Mẫu** | Lưu các Fanpage (tên + URL) và các mẫu dùng lại nhiều lần. Mỗi mẫu gồm nội dung và có thể kèm một ảnh hoặc video; khi chèn mẫu, tệp đính kèm được điền theo. |
| **Cài đặt** | Đăng nhập Facebook và lưu phiên, bật/tắt chế độ chạy ẩn trình duyệt, mở ảnh kết quả gần nhất. |

Lần đầu sử dụng: vào tab **Cài đặt** → **Đăng nhập Facebook…** → đăng nhập trong cửa sổ trình duyệt → quay lại bấm **Tôi đã đăng nhập xong**.

Đăng lên nhiều nơi:
- Ở mục **Đăng lên**, bấm **Chọn nơi đăng…** để mở danh sách tất cả các nơi, tích vào những nơi muốn đăng rồi bấm **OK**. Màn hình chính hiển thị các nơi đã chọn.
- Mỗi nơi được chọn thành một bài riêng trong tab **Hàng đợi** và được đăng lần lượt, cách nhau số phút ở ô **Giãn cách (phút)** (mặc định 5, đặt 0 để đăng liền nhau).
- Với **Đăng ngay**, nơi đầu tiên được đăng ngay, các nơi sau chờ theo giãn cách. Với **Thêm vào hàng đợi**, nơi đầu tiên chạy đúng giờ hẹn.
- Một nơi bị lỗi không chặn các nơi sau; có thể đăng lại riêng nơi đó từ tab Hàng đợi.
- Đăng ngay lên nhiều nơi cần bật **Tự động đăng khi đến giờ** và phải để ứng dụng mở cho đến khi bài cuối chạy xong.

Lưu ý về hẹn giờ:
- Bài chỉ được đăng khi **ứng dụng đang mở**. Thời điểm nhập theo dạng `YYYY-MM-DD HH:MM`.
- Bài quá giờ hơn 5 phút lúc ứng dụng đang tắt sẽ được đánh dấu **Lỡ** và không tự đăng bù. Bài lặp thì chuyển sang lần kế tiếp.
- Mỗi lúc chỉ đăng một bài; các bài đến giờ cùng lúc sẽ được đăng lần lượt.
- Đăng lặp lại cùng một nội dung dễ bị Facebook coi là spam.
- Mẫu chỉ lưu **đường dẫn** tới tệp ảnh/video, không sao chép tệp. Nếu tệp bị xóa hoặc chuyển chỗ, bài đăng dùng mẫu đó sẽ báo lỗi.
- Với video, script chờ hộp soạn bài đóng lại (tối đa 10 phút) rồi mới đóng trình duyệt, để không cắt ngang việc tải lên.
- Trạng thái **Xong** chỉ có nghĩa là script chạy hết mà không báo lỗi. Hãy mở `debug_result.png` hoặc vào Facebook để chắc chắn bài đã lên.

Cài đặt và hàng đợi được lưu trong `config.json` và `queue.json` cùng thư mục.

---

## Các bước thực hiện

### Bước 1: Cài đặt thư viện Playwright
Mở terminal/cmd tại thư mục này và chạy các lệnh sau:

```bash
# Cài đặt thư viện Playwright cho Python
pip install playwright

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
4. Quay lại cửa sổ dòng lệnh (terminal) và nhấn **ENTER** để lưu session.
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
| `--image <đường dẫn>` | Đính kèm một ảnh hoặc một video. Nếu không tải được tệp, script dừng lại và không đăng bài. |
| `--headless` | Chạy ẩn trình duyệt. |
| `--content-file <tệp>` | Đọc nội dung từ tệp văn bản UTF-8 (tiện cho bài dài, nhiều dòng). |

Script thoát với mã `0` khi chạy hết không lỗi và mã `1` khi thất bại.

*Cách thức hoạt động:*
1. Script sẽ mở trình duyệt Chromium và tải dữ liệu từ tệp `state.json` (bạn sẽ được tự động đăng nhập vào Facebook mà không cần nhập lại mật khẩu).
2. Tự động tìm kiếm nút "Bạn đang nghĩ gì thế?" / "What's on your mind?".
3. Mở khung soạn thảo văn bản, tự động điền nội dung bài đăng truyền vào từ dòng lệnh.
4. Tìm nút "Đăng" (hoặc "Post") và nhấp chuột để hoàn tất đăng bài.
5. Nếu xảy ra lỗi không tìm thấy nút, chương trình sẽ tự động chụp ảnh màn hình dạng `debug_*.png` trong thư mục để bạn dễ dàng tìm nguyên nhân (ví dụ: giao diện Facebook thay đổi).

---

## Xử lý sự cố thường gặp (Troubleshooting)

- **Lỗi không tìm thấy nút "Đăng" hoặc "Bạn đang nghĩ gì thế":** Do Facebook thường xuyên thay đổi giao diện (A/B testing hoặc đổi class name), nếu script không tìm thấy nút, hãy kiểm tra các tệp ảnh chụp màn hình tự động (`debug_homepage.png`, `debug_modal.png`) để xem giao diện hiện tại của bạn như thế nào, sau đó có thể bổ sung các bộ chọn CSS (Selector) mới vào mảng `CREATE_POST_SELECTORS` (nút mở hộp soạn bài) hoặc `primary_selectors` (nút Đăng) trong file `post.py`.
- **Đăng vào nhóm:** Dùng được URL nhóm (`facebook.com/groups/...`) ở ô URL như với Fanpage. Nhóm có kiểm duyệt sẽ đưa bài vào hàng chờ duyệt, nên bài có thể chưa hiện ngay dù trạng thái là "Xong".
- **Checkpoint / Yêu cầu xác minh thiết bị:** Nếu Facebook yêu cầu xác minh khi chạy `post.py`, hãy đăng nhập thủ công trên trình duyệt đó để xác nhận, sau đó chạy lại `login.py` để cập nhật lại tệp `state.json`.
