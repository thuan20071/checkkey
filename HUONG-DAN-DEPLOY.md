# Hướng dẫn đưa YT NICHE HUNTER lên mạng (có link public)

## Vì sao không up lên InfinityFree được?

InfinityFree (host bạn đang dùng cho `tmmedia.page.gd`) **chỉ chạy PHP**,
không chạy được Python/Flask. Tool này viết bằng Flask nên phải up lên
host hỗ trợ Python. Cách dễ + miễn phí nhất là **Render.com**.

## Cách 1: Deploy lên Render (khuyên dùng, free)

### Bước 1 — Đưa code lên GitHub
1. Vào https://github.com → đăng nhập → **New repository**,
   đặt tên `yt-niche-hunter`, để Public, bấm **Create**.
2. Trong repo mới bấm **uploading an existing file** → kéo-thả các file
   trong thư mục `yt-niche-hunter-v2/` vào:
   - `yt_niche_hunter.py`
   - `requirements.txt`
   - `render.yaml`
   - `chay_web.bat`, `README.md` (không bắt buộc)
3. Bấm **Commit changes**.

### Bước 2 — Tạo Web Service trên Render
1. Vào https://render.com → đăng ký/đăng nhập (nên **Sign up with GitHub**).
2. Dashboard → **New +** → **Web Service** → chọn repo `yt-niche-hunter`
   → **Connect**.
3. Điền như sau:
   - **Name:** `yt-niche-hunter` (hoặc tên bạn thích)
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:**
     `gunicorn yt_niche_hunter:app --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 120`
   - **Plan:** Free
4. (Khuyên dùng) Mục **Environment Variables** → **Add**:
   - Key: `YT_API_KEY` — Value: dán YouTube API key của bạn.
     Làm vậy thì deploy lại cũng không phải nhập lại key.
5. Bấm **Create Web Service** → đợi 2–5 phút cho build xong.
   Khi hiện **Live** là có link dạng:
   `https://yt-niche-hunter-xxxx.onrender.com` ← đây là link web của bạn.

### Bước 3 — Dùng
- Mở link trên là xài như ở localhost.
- Nếu không đặt `YT_API_KEY` ở bước 2: vào trang **Cài đặt** trong web
  để nhập key (lưu ý bên dưới).

## Cách 2: PythonAnywhere (free, không cần GitHub)
1. Đăng ký https://www.pythonanywhere.com (free).
2. Tab **Files** → upload `yt_niche_hunter.py`.
3. Tab **Web** → **Add a new web app** → **Flask** → Python 3.12.
4. Mở file `wsgi.py` của app, sửa thành:
   ```python
   import sys
   sys.path.insert(0, '/home/<username>/yt-niche-hunter')
   from yt_niche_hunter import app as application
   ```
   (upload file vào đúng thư mục đó)
5. **Reload** web app là xong.

## Gắn vào domain tmmedia.page.gd của bạn
- Đơn giản nhất: mở web `tmmedia.page.gd` trên InfinityFree, thêm 1 nút/link
  trỏ tới link Render (vd: `https://yt-niche-hunter-xxxx.onrender.com`).
- Muốn tên miền đẹp kiểu `tool.tmmedia.page.gd`: cần quản lý DNS của tên
  miền (qua Cloudflare free) rồi trỏ CNAME về địa chỉ Render. Hơi nâng cao,
  làm sau cũng được — link Render xài ngay vẫn ổn.

## Lưu ý quan trọng (bản free)
- **Render free ngủ sau 15 phút không ai dùng** → lần mở đầu tiên sau
  khi ngủ sẽ quay ~30–60 giây mới lên, các lần sau nhanh bình thường.
- **Dữ liệu Thư viện / Lịch sử / API key lưu trong SQLite sẽ mất khi
  deploy lại** (ổ đĩa free bị reset). Đặt `YT_API_KEY` trong Environment
  Variables thì không lo mất key.
- Quota YouTube API: **10.000 điểm/ngày** cho cả key. Link public mà share
  lung tung thì người khác quét sẽ đốt quota của bạn — nên để link
  riêng mình dùng, hoặc theo dõi ở Dashboard.
