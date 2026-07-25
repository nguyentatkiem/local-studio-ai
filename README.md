# 🎬 Local Studio — AI Video Editor chạy 100% trên máy

> v0.5 — Không cloud · Không credit · Không watermark · Footage không bao giờ rời máy bạn.

**Máy mới clone repo:** chạy `./setup-binaries.sh` một lần để tải engine AI (~350MB), rồi `./start.sh`.

## Chạy ứng dụng

- **Windows**: nháy đúp `start.bat` → trình duyệt tự mở `http://127.0.0.1:8765`
- **macOS/Linux**: chạy `./start.sh` (hoặc mở **Local Studio.app** trong /Applications — tự khởi động backend)

## Tính năng v0.4

| Tính năng | Engine | Ghi chú |
|---|---|---|
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
