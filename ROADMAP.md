# Local Studio — Roadmap

## ✅ ĐÃ XONG — MVP 0.1 (GĐ 0-2, build 2026-07-24, máy Windows RTX 3060)

- Kiến trúc: FastAPI backend (port 8765, chỉ bind 127.0.0.1) + React/Vite frontend build sẵn + FFmpeg
- Upload + thư viện media + preview + probe metadata · job queue nền (ThreadPool 2 luồng)
- 📝 Caption faster-whisper · ✂️ auto-editor · 🔍 Real-ESRGAN · 📤 Export preset · start.bat

## ✅ ĐÃ XONG — v0.4 (build 2026-07-25, máy Mac M4 16GB — chạy đa nền tảng)

### Nền tảng Mac + Đợt 1-2
- [x] Binary theo hệ điều hành (`_ncnn_bin`): Real-ESRGAN/RIFE bản macOS (GPU Metal qua MoltenVK)
- [x] FFmpeg static đóng gói trong `binaries/ffmpeg/` (đầy đủ **libass** — brew của máy thiếu) + tự thêm vào PATH
- [x] `start.sh` một-cú-nhấp cho macOS/Linux (tự tạo venv + build frontend lần đầu)
- [x] Hủy job đang chạy (`POST /api/jobs/{id}/cancel` — kill subprocess, trạng thái `cancelled`)
- [x] Cache probe metadata (key theo mtime+size) · hàng đợi 40 job cho render qua đêm
- [x] Batch: chọn nhiều tệp trong kho media → chạy hàng loạt mọi công cụ
- [x] Whisper engine **MLX (GPU Metal)** bên cạnh faster-whisper CPU · thêm model medium
- [x] Preset export mới: Reels 4:5 · GIF 480p (palette 2 lượt) · tách MP3 320k
- [x] GPU chip Apple hiển thị đúng (RAM hợp nhất)

### Đợt 3 — AI nâng cao
- [x] 🪄 Tách nền video RVM (ONNX CoreML/CPU) — nền xanh/đen/trắng + **webm alpha trong suốt**
- [x] 🎞️ RIFE nội suy khung hình ×2 (GPU) — chế độ mượt (fps×2) + slow-motion (âm thanh giãn 0.5×)
- [x] 🗣️ Piper TTS offline — giọng vi_VN-vais1000 + en_US-lessac, tốc độ 0.6–1.5×, xuất wav+mp3
  (dùng pip `piper-tts` vì binary GitHub chỉ có x86_64)

### Đợt 4 — Desktop
- [x] Rust toolchain + Tauri v2 shell → `Local Studio.app` (tự spawn backend, tắt khi đóng app)

### Đợt 5 — CapCut-style + AI tự động (yêu cầu 2026-07-25)
- [x] ✨ **Tự động dựng 1 chạm** (`auto_edit`): cắt lặng → caption karaoke → preset/reframe — batch được
- [x] 📐 Đổi khung 9:16: nền mờ kiểu CapCut (video giữa + chính nó blur làm nền) hoặc crop giữa
- [x] ⏩ Tốc độ 0.5–3× (setpts + atempo chain, giữ cao độ)
- [x] 🎨 6 filter màu: Rực rỡ · Ấm · Lạnh · Đen trắng · Film cổ điển (vignette+grain) · Nét căng
- [x] 🎵 Nhạc nền + **ducking** (sidechaincompress tự nén nhạc khi có giọng nói), loop nhạc, chống clip
- [x] 🧷 Chống rung deshake (ffmpeg static không có vidstab)

## TIẾP THEO (đề xuất)

### GĐ 6 — SAM 2 track đối tượng (đã đánh giá khả thi 2026-07-25)
Khả thi trên M4 16GB qua PyTorch MPS với `sam2-hiera-small` (~185MB), nhưng là tính năng lớn nhất:
cần torch (~2.5GB disk), UI click-chọn trên canvas, propagation qua video, composite blur/che.
→ Làm thành sprint riêng; ưu tiên sau khi dùng thử v0.4.

### GĐ 6+ — Agent & khác
- [ ] Panel "đạo diễn" — LLM local (llama.cpp + Qwen 7B Q4 chạy tốt trên M4 16GB)
- [ ] Search kho footage bằng ngôn ngữ tự nhiên (CLIP embedding local)
- [ ] Beat-sync cắt theo nhạc (librosa onset) · text overlay/sticker · LUT .cube tùy chỉnh
- [ ] Whisper GPU CUDA khi quay lại máy Windows (cuBLAS/cuDNN)
- [ ] Sinh video local (FramePack/Wan/LTX) — **hoãn trên máy 16GB RAM**, chờ máy mạnh hơn

## Ghi chú kỹ thuật quan trọng
- realesrgan/rife PHẢI chạy với `cwd` = thư mục chứa binary (tìm `models/` cạnh exe); stderr ghi ra file, không PIPE
- ffmpeg hệ thống (brew formula tùy chỉnh của máy) THIẾU libass → luôn ưu tiên `binaries/ffmpeg/`
- Burn subtitle dùng cwd-trick với tên file .ass tương đối
- mlx-whisper gọi `ffmpeg` từ PATH → backend tự prepend `binaries/ffmpeg` vào PATH khi khởi động
- Windows: `backend\.venv\Scripts\python.exe` · macOS: `backend/.venv/bin/python` (Python 3.11)

## Máy hiện tại (Mac)
- Apple M4 · 16GB RAM hợp nhất · Metal/MoltenVK OK · Python 3.11 · Node 22 · disk trống ~100GB
