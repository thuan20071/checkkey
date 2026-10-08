# YT NICHE HUNTER v2.0 PRO

Web phân tích YouTube & tìm ngách — full chức năng kiểu tool pro.

## Chạy

1. Cài **Python 3.10+** (python.org), lúc cài nhớ tick **Add python.exe to PATH**
2. Nhấp đúp **`chay_web.bat`** → trình duyệt tự mở `http://127.0.0.1:5000`
3. Lần đầu chạy tool tự cài thư viện còn thiếu (flask, requests, openpyxl)

## Lấy YouTube Data API key (miễn phí)

1. Vào https://console.cloud.google.com/ → tạo project
2. **APIs & Services → Library** → tìm **YouTube Data API v3** → **Enable**
3. **APIs & Services → Credentials** → **Create Credentials → API key**
4. Mở tool → trang **Cài đặt** → dán key → **Lưu** → bấm **Kiểm tra**

Hạn mức miễn phí: 10.000 điểm/ngày. 1 lần tìm kênh ≈ 130–250 điểm.

## Chức năng

- **Dashboard**: tổng quan, ước tính quota API đã dùng, từ khóa hot
- **Tìm kênh**: từ khóa + khu vực (15 nước) + lọc "đăng trong" + Max/KW + bộ lọc nâng cao
  (subs min/max, TB view tối thiểu, sắp xếp) + log quá trình + xuất CSV/JSON/Excel
- **Phân tích kênh**: Outlier Score, VPH từng video, biểu đồ views, % Shorts
- **Kênh tương tự**: từ 1 kênh mẫu tìm kênh cùng chủ đề (độ khớp cao/trung/thấp)
- **Thư viện**: lưu kênh tiềm năng (SQLite), xuất Excel/CSV
- **Lịch sử**: 100 lần quét gần nhất
- **AI mở rộng từ khóa** (tùy chọn): cắm API OpenAI-compatible ở trang Cài đặt

## Lưu ý

- Tool chạy **local** trên máy bạn, dữ liệu lưu ở thư mục `data/`
- Muốn dùng máy khác: copy cả thư mục này sang
- Tắt web: đóng cửa sổ đen hoặc nhấn Ctrl+C
