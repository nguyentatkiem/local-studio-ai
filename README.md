# 🎬 Local Studio — AI Video Editor chạy 100% trên máy

> MVP 0.1 — Không cloud · Không credit · Không watermark · Footage không bao giờ rời máy bạn.

## Chạy ứng dụng

**Nháy đúp `start.bat`** → trình duyệt tự mở `http://127.0.0.1:8765`.

## Tính năng MVP

| Tính năng | Engine | Ghi chú |
|---|---|---|
| 📝 Caption tự động (+ burn-in, xuất .srt/.txt) | faster-whisper (CPU int8) | Model tiny/base/small, tự nhận diện ngôn ngữ |
| ✂️ Cắt khoảng lặng / jump-cut | auto-editor | Chỉnh biên an toàn (margin) |
| 🔍 AI Upscale ×2/×3/×4 | Real-ESRGAN ncnn-vulkan (**GPU**) | Chế độ "Nhanh" (lanczos) cho video dài |
| 📤 Xuất preset TikTok 9:16 / YouTube / vuông | FFmpeg | Không watermark |
| ⚙️ Hàng đợi job nền | ThreadPool | Xếp nhiều job, chạy tuần tự 2 luồng |

## Kiến trúc

```
frontend/   React + Vite (build sẵn vào frontend/dist, backend serve luôn)
backend/    FastAPI (main.py) + venv riêng — cổng 8765, chỉ bind 127.0.0.1
binaries/   realesrgan-ncnn-vulkan (GPU Vulkan)
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
