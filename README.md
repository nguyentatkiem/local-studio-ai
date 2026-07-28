# 🎬 Local Studio — AI Video Editor chạy 100% trên máy

> v1.1 — Không cloud · Không credit · Không watermark · Footage không bao giờ rời máy bạn.

**Máy mới clone repo:** chạy `./setup-binaries.sh` một lần để tải engine AI (~350MB), rồi `./start.sh`.

## Chạy ứng dụng

- **Windows**: nháy đúp `start.bat` → trình duyệt tự mở `http://127.0.0.1:8765`
- **macOS/Linux**: chạy `./start.sh` (hoặc mở **Local Studio.app** trong /Applications — tự khởi động backend)

**Tắt mật khẩu đăng nhập** (máy cá nhân, server chỉ bind 127.0.0.1): ghi `{"disabled": true}`
vào `backend/auth_config.json` rồi khởi động lại. Muốn bật lại: xoá file đó — mật khẩu mới
sẽ được sinh vào `backend/ADMIN-PASSWORD.txt`.

## Tính năng v1.1

| Tính năng | Engine | Ghi chú |
|---|---|---|
| 🔗 Chuỗi tự động (Pipeline) | nội bộ | xếp nhiều tính năng chạy nối tiếp trên 1 video, 1 job; Đạo diễn AI nối được bằng lời |
| 🔥 Phụ đề Viral 1 chạm | Whisper large-v3 + libass | nhận dạng → tách cụm → cháy preset viral + chuẩn âm -14LUFS, xuất mp4+ass+srt |
| 🎬 Đạo diễn AI | Claude (gói sub, qua CLI) | ra lệnh bằng lời → tự lập kế hoạch & xếp job, backend validate |
| 🎯 AI cắt Shorts từ video dài | Whisper + Qwen3 4B / Claude | LLM chọn khoảnh khắc → shorts 9:16 + caption, kiểu Opus Clip |
| 🎞️ Ghép B-roll tự động | Whisper + AI + Pexels API | AI chọn đoạn+từ khoá → tải cảnh stock chèn theo lời nói (cần key) |
| 📝 AI viết nội dung | Qwen3 4B local / Claude sub | 3 tiêu đề hook · mô tả SEO · hashtags · chapters |
| 📚 Bài giảng → Giáo án + Quiz | Whisper + Qwen/Claude | soạn giáo án + câu hỏi, đẩy thẳng vào AI-LMS |
| 🫥 Làm mờ mặt tự động | YuNet ONNX | blur/pixelate mọi khuôn mặt, chống nhấp nháy |
| ✨ Tự động dựng AI 1 chạm | pipeline | cắt lặng → caption karaoke → preset/9:16 · chạy batch qua đêm |
| 📝 Caption tự động (+ burn-in, .srt/.ass/.txt) | faster-whisper CPU · **MLX GPU Metal (Mac)** | tiny/base/small/medium, karaoke/6 hiệu ứng |
| ✂️ Cắt khoảng lặng / jump-cut | auto-editor | Chỉnh biên an toàn (margin) |
| 🔍 AI Upscale ×2/×3/×4 | Real-ESRGAN ncnn-vulkan (**GPU**) | Chế độ "Nhanh" (lanczos) cho video dài |
| 🎞️ Nội suy khung hình ×2 / slow-motion | RIFE ncnn-vulkan (**GPU**) | 30→60fps hoặc quay chậm 2× |
| 🪄 Tách nền video | RVM (ONNX CoreML/CPU) | nền xanh/đen/trắng + webm alpha trong suốt |
| 🗣️ Đọc văn bản TTS offline | Piper | giọng Việt + Anh, tốc độ 0.6–1.5×, wav+mp3 |
| 📐 Đổi khung 9:16 kiểu CapCut | FFmpeg | nền chính video làm mờ hoặc crop giữa |
| ⏩ Tốc độ 0.5–3× | FFmpeg | giữ cao độ âm thanh (atempo) |
| 🎨 6 filter màu | FFmpeg | vivid · warm · cool · B&W · film · sharp |
| 🎵 Nhạc nền + ducking | FFmpeg sidechain | tự nén nhạc khi có giọng nói |
| 🧷 Chống rung | FFmpeg deshake | video quay tay |
| 🎬 Ghép clip + chuyển cảnh | FFmpeg xfade | 2-8 clip · 10 hiệu ứng · thay nhạc nền |
| 🥁 Cắt theo nhịp nhạc (beat-sync) | librosa | kiểu template CapCut, xoay vòng nhiều clip |
| 🎚️ Chuẩn hoá âm thanh | FFmpeg | khử ồn + loudnorm -16 LUFS chuẩn MXH |
| 🏷️ Tiêu đề & Logo watermark | libass + overlay | title fade · chữ ký · PNG 4 góc |
| 📻 Audiogram sóng nhạc | FFmpeg showwaves | audio/TTS → video đăng MXH |
| 📤 Xuất preset | FFmpeg | TikTok 9:16 · YouTube · 1:1 · 4:5 · GIF · MP3 |
| ⚙️ Hàng đợi 40 job + hủy job + batch | ThreadPool | chọn nhiều tệp chạy hàng loạt |

## Kiến trúc

```
frontend/   React + Vite (build sẵn vào frontend/dist, backend serve luôn)
backend/    FastAPI (main.py) + venv riêng — cổng 8765, chỉ bind 127.0.0.1
binaries/   realesrgan · rife · rvm (onnx) · piper/voices · ffmpeg static (libass)
desktop/    Tauri v2 shell → Local Studio.app (tự spawn backend)
workspace/  uploads / outputs / tmp
samples/    sample.mp4 — video test có giọng nói + khoảng lặng
```

- Whisper model tải về 1 lần đầu (base ≈ 74MB) vào cache HuggingFace, sau đó offline hoàn toàn.
- API chỉ bind `127.0.0.1` — không máy nào khác truy cập được.

## Dev

```powershell
# backend (hot reload)
backend\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8765 --app-dir backend
# frontend (dev server, proxy sẵn về 8765)
cd frontend; npm run dev
# build frontend
cd frontend; npm run build
```

## Roadmap

Xem `ROADMAP.md` — tiếp theo: Tauri desktop shell (GĐ3), SAM 2 object tracking, tách nền, TTS/voice (GĐ4), sinh video local FramePack/Wan (GĐ5), Agent panel (GĐ6).
