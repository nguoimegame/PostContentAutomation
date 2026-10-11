import sys
import os

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

import post

content = """✨ DAILY ENGLISH TIP - BÍ QUYẾT TĂNG VỐN TỪ VỰNG MỖI NGÀY ✨

Bạn muốn cải thiện khả năng giao tiếp Tiếng Anh tự nhiên hơn? Hãy cùng Hi English bỏ túi ngay 3 cụm từ cực hay cho chủ đề công việc & cuộc sống hàng ngày nhé!

1️⃣ "Hit the nail on the head" -> Nói đúng trọng tâm, chính xác.
👉 Example: You hit the nail on the head with that proposal!

2️⃣ "Break the ice" -> Tháo gỡ sự ngại ngùng ban đầu.
👉 Example: A quick game is a great way to break the ice in class.

3️⃣ "Call it a day" -> Kết thúc công việc trong ngày.
👉 Example: We've done enough work for today, let me call it a day.

💬 Đừng quên thả cảm xúc và lưu bài viết lại để luyện tập mỗi ngày nhé! 🌟
---
👉 Follow Hi English để học thêm nhiều kiến thức Tiếng Anh bổ ích mỗi ngày!"""

if __name__ == "__main__":
    print("Executing post script for https://www.facebook.com/hienglishoffical ...")
    post.make_post(text=content, page_url="https://www.facebook.com/hienglishoffical", headless=False)
