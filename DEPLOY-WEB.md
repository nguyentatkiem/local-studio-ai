# 🌐 Local Studio — Phát hành Web qua Cloudflare

## ⚠️ Đọc trước: vì sao là Cloudflare **Tunnel**, không phải Cloudflare Pages

Local Studio xử lý AI (Whisper, Real-ESRGAN, FFmpeg) **trên GPU máy bạn**. Cloudflare
Pages/Workers chỉ chạy được static + JS — không chạy Python/GPU. Nên kiến trúc đúng là:

```
Người dùng ──HTTPS──► Cloudflare ──Tunnel──► Backend chạy trên máy bạn (GPU) ──► xử lý AI
```

App vẫn chạy 100% trên máy bạn; Cloudflare chỉ làm "cửa vào" công khai có HTTPS.
Footage vẫn nằm trên máy bạn, đúng tinh thần local-first.

## 🚀 Chạy: nháy đúp `start-web.bat`

Cửa sổ sẽ in ra một URL dạng `https://<ngau-nhien>.trycloudflare.com`.
Mở URL đó trên bất kỳ máy/điện thoại nào → hiện **màn hình đăng nhập admin**.

> URL Quick Tunnel đổi mỗi lần khởi động. Muốn URL cố định + tên miền riêng, xem mục "Nâng cấp" cuối file.

## 🔑 Mật khẩu admin

- Sinh tự động lần chạy đầu, lưu ở: **`backend\ADMIN-PASSWORD.txt`**
- Đổi mật khẩu: xoá `backend\auth_config.json` rồi khởi động lại (mật khẩu mới sẽ ghi lại vào `ADMIN-PASSWORD.txt`).
- Phiên đăng nhập kéo dài 7 ngày (cookie HttpOnly).

## 🔒 Bảo mật đã rà & vá (12/12 test PASS)

| Vá | Nội dung |
|---|---|
| Xác thực | Mọi API (trừ trang login) trả **401** nếu chưa đăng nhập; cookie phiên HttpOnly + SameSite |
| Chống dò mật khẩu | Khoá 5 phút sau 10 lần sai |
| Path traversal | `safe_upload_path()` chặn `..`, `/`, `\` ở upload/thumb/job — traversal trả **400** |
| Giới hạn upload | Tối đa 2 GB/tệp, chỉ nhận đuôi media hợp lệ |
| Chống DoS hàng đợi | Tối đa 12 job chờ/chạy cùng lúc |
| Lộ file nội bộ | `/files` chỉ mở `uploads/` + `outputs/`, **không** mở `tmp/` (log, frame) |
| Allowlist tham số | model Whisper/ESRGAN, preset, tỷ lệ... đều bị khoá vào danh sách cho phép |
| Security headers | `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Cache-Control: no-store` |

## ⚠️ Lưu ý khi mở ra internet

- **Chia sẻ URL + mật khẩu một cách thận trọng** — ai có cả hai đều dùng được GPU máy bạn.
- Máy phải **bật + chạy `start-web.bat`** thì URL mới sống. Tắt máy = web tắt.
- Đĩa C còn ~10 GB — người dùng upload nhiều có thể đầy đĩa (đã cap 2GB/tệp để giảm rủi ro).
- Đây là Quick Tunnel để demo/dùng riêng. Với sản phẩm thật, cân nhắc mục Nâng cấp.

## 🔼 Nâng cấp (khi cần URL cố định + tên miền riêng)

1. Tạo tài khoản Cloudflare (miễn phí) + thêm domain của bạn.
2. `cloudflared login` → `cloudflared tunnel create local-studio`.
3. Trỏ CNAME `app.tenmien.com` → tunnel, thêm **Cloudflare Access** (đăng nhập Google/email OTP) để có thêm 1 lớp bảo vệ trước cả app.
4. Chạy `cloudflared tunnel run local-studio`.

## Cấu trúc liên quan
- `start-web.bat` — chạy backend + tunnel
- `binaries\cloudflared.exe` — Cloudflare Tunnel
- `backend\auth_config.json` — hash mật khẩu (KHÔNG commit)
- `backend\ADMIN-PASSWORD.txt` — mật khẩu dạng chữ (KHÔNG commit, giữ kín)
