# Local Studio — Roadmap

## ✅ ĐÃ XONG — MVP 0.1 (GĐ 0-2, build 2026-07-24)

- Kiến trúc: FastAPI backend (port 8765, chỉ bind 127.0.0.1) + React/Vite frontend build sẵn + FFmpeg hệ thống
- Nhận diện GPU (nvidia-smi), health/feature detection
- Upload + thư viện media + preview + probe metadata
- Job queue nền (ThreadPool 2 luồng), poll tiến trình realtime
- 📝 Caption: faster-whisper (tiny/base/small, CPU int8) → .srt/.txt + burn-in
- ✂️ Cắt khoảng lặng: auto-editor (margin tùy chỉnh)
- 🔍 Upscale: Real-ESRGAN ncnn-vulkan trên GPU (x2/x3/x4, 2 model) + chế độ nhanh lanczos
- 📤 Export preset: TikTok 9:16 / YouTube 1080p / vuông 1:1
- start.bat một-cú-nhấp

### Ghi chú kỹ thuật quan trọng
- realesrgan stderr PHẢI ghi ra file, không dùng PIPE (buffer đầy → treo) — đã fix
- Burn subtitle dùng cwd-trick để né escape đường dẫn Windows trong filter
- `python` PATH trỏ vào venv hermes → luôn dùng `backend\.venv\Scripts\python.exe`

## GĐ 3 — v1.0 (tiếp theo)
- [ ] Cài Rust + đóng gói Tauri desktop shell (installer .exe) — máy hiện CHƯA có Rust
- [ ] Batch nhiều file + "render qua đêm" UI
- [ ] Whisper GPU (CTranslate2 CUDA cần cuBLAS/cuDNN) hoặc whisper.cpp Vulkan
- [ ] Cache probe, xử lý lỗi/edge-case, hủy job đang chạy
- [ ] License offline + tier Free/Pro

## GĐ 4 — AI nâng cao
- [ ] SAM 2: click-chọn & track đối tượng (mask/blur/che)
- [ ] Tách nền video (RVM/BiRefNet) — cần cân disk (onnxruntime)
- [ ] RIFE frame interpolation · GFPGAN face restore
- [ ] Voice isolation + TTS local

## GĐ 5 — Sinh video local
- [ ] ComfyUI service riêng (GPL) + FramePack (6GB VRAM) / Wan2.2 5B / LTX

## GĐ 6 — Agent + Search
- [ ] Panel "đạo diễn" — LLM local (llama.cpp + Qwen)
- [ ] Search kho footage bằng ngôn ngữ tự nhiên (embedding local)

## Ràng buộc máy hiện tại
- RTX 3060 12GB (Vulkan OK) · Python 3.11 · FFmpeg 8.1.2 full · Node 24
- ⚠️ Disk C chỉ còn ~10GB → cân nhắc trước khi thêm model nặng
- ⚠️ Chưa có Rust/cargo → Tauri phải cài toolchain trước
