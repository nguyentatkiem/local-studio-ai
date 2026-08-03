# 🎬 Local Studio — AI Video Editor chạy 100% trên máy

<p align="center">
  <img src="https://img.shields.io/badge/version-2.4.0-FFB23F?style=for-the-badge" alt="version">
  <img src="https://img.shields.io/badge/t%C3%ADnh%20n%C4%83ng-63-46D6C6?style=for-the-badge" alt="features">
  <img src="https://img.shields.io/badge/cloud-0%25-FF6B57?style=for-the-badge" alt="no cloud">
  <img src="https://img.shields.io/badge/watermark-kh%C3%B4ng-7ACB6B?style=for-the-badge" alt="no watermark">
</p>

<p align="center">
  <b>Không cloud · Không credit · Không watermark · Footage không bao giờ rời máy bạn</b><br>
  <sub>Whisper · SAM 2 · CLIP · Qwen3 · Piper TTS · Real-ESRGAN · RIFE · FFmpeg — tất cả chạy local trên GPU của bạn.<br>
  Tuỳ chọn nối <b>gói sub Claude</b> làm "não cao cấp" (chỉ văn bản rời máy, video thì không).</sub>
</p>

---

## 🚀 Chạy ứng dụng

| Bước | Lệnh |
|---|---|
| 1️⃣ Máy mới clone repo | `./setup-binaries.sh` — tải engine AI một lần (~500MB) |
| 2️⃣ macOS / Linux | `./start.sh` (hoặc mở **Local Studio.app**) |
| 2️⃣ Windows | nháy đúp `start.bat` |
| 3️⃣ Trình duyệt | `http://127.0.0.1:8765` — mật khẩu trong `backend/ADMIN-PASSWORD.txt` |

> 💡 **Máy cá nhân muốn bỏ đăng nhập:** ghi `{"disabled": true}` vào `backend/auth_config.json` rồi khởi động lại (an toàn vì server chỉ bind 127.0.0.1 — **đừng** tắt khi mở ra internet qua tunnel).
> 🌐 **Phát hành web tạm qua Cloudflare Tunnel:** xem `DEPLOY-WEB.md`.
> 🎯 **Track đối tượng SAM 2** cần thêm: `pip install ultralytics` · **Tìm cảnh CLIP** cần: `pip install open_clip_torch` (trong venv backend).

## ✨ 63 tính năng (v2.4)

### 🤖 Tự động hoá TỐI ĐA (không editor nào có đủ bộ này)
| Tính năng | Mô tả |
|---|---|
| 🚀 **AutoPilot** | 1 nút: tự dò lời thoại → cắt lặng + ừm/à → đẹp màu → chuẩn âm → 9:16 bám mặt → phụ đề viral |
| 🎥 **Script-to-Video** | gõ chủ đề → AI viết cảnh + giọng đọc + B-roll Pexels → video hoàn chỉnh từ số 0 |
| 🔥 **Thư mục nóng** | thả video vào folder là TỰ xử lý theo chuỗi định sẵn |
| ⏰ **Lịch chạy đêm** | tự chạy batch cả thư mục mỗi ngày đúng giờ |
| 📦 **Gói đăng bài** | video + thumbnail AI + caption + srt → 1 file .zip |
| 🧩 **Template chuỗi** | lưu chuỗi bước đặt tên, áp 1 chạm |
| ⚡ **Batch tăng dần + resume** | chạy lại chỉ làm file MỚI; hủy giữa chừng → tiếp tục đúng chỗ |
| 🎯 **Lọc thông minh** | chỉ xử lý file khớp: ngang/dọc · dài ≥s · nặng ≥MB · có tiếng/im lặng |
| 🔁 **Chạy lại file lỗi 1 nút** | ✅/❌ từng file ngay trong Hàng đợi |
| 🗜 **Nén dọn ổ cứng** · 📤 **xuất đa phiên bản** · 🏷 **AI đặt tên + gom kho** (nghe + CLIP nhìn) | |
| 🔥 **Nhiều thư mục nóng** — mỗi folder 1 chuỗi riêng · 🚦 hàng đợi 2 làn (job nhanh không chờ job nặng) | |
| 📲 **Nhận video từ iPhone** | Apple Shortcuts share → tự xử lý qua Wi-Fi |
| 🎬 Tách cảnh tự động · 🔍 Auto punch-in theo câu · 🌏 Dịch đa ngữ 1 chạm (6 thứ tiếng/job) | |

### 🎞️ Studio dựng — timeline & lớp phủ
| Tính năng | Mô tả |
|---|---|
| ✂️ **Cắt trên timeline** | kéo IN/OUT trên dải khung hình · giữ / cắt bỏ / ghép nhiều đoạn |
| 🎬 **Multi-track xếp lớp** | đè 🖼 ảnh/logo · 🔤 chữ · 🎵 nhạc · 📹 **video PiP** theo mốc thời gian, kéo vị trí ngay trên player |
| ⤳ **Keyframe vị trí** | lớp bay từ điểm A → B · hiệu ứng bay vào (mờ dần, trượt 4 hướng) |
| 💾 **Dự án** | lưu nguyên timeline, mở lại làm tiếp |
| 🎛️ **Bảng chỉnh màu PRO** | 10 thanh trượt + 2 bánh xe **Nhuộm Tối/Sáng** · xem trước tức thì |
| 🔗 **Chuỗi tự động** | xếp nhiều tính năng chạy nối tiếp 1 job · 1–4 luồng song song |
| 📁 **Edit hàng loạt cả THƯ MỤC** | trỏ folder trong ổ cứng → chạy chuỗi bước cho mọi video → xuất `LocalStudio_Xuat` |

### 🤖 AI thị giác & âm thanh (local)
| Tính năng | Engine |
|---|---|
| 🎯 **Track đối tượng** — bấm vào vật/người → bám suốt video → mờ/che ô/spotlight/tách nền | **SAM 2** (Meta) · MPS |
| 🔎 **Tìm cảnh bằng mô tả** — gõ tiếng Việt, tìm đúng khoảnh khắc cả kho | **CLIP** ViT-B-32 |
| 🔥 **Phụ đề Viral 1 chạm** — cụm 2 dòng, tô từ khoá, karaoke, 3 hiệu ứng chữ, chuẩn âm -14 LUFS | Whisper large-v3 MLX + libass |
| 💬 Caption tự động (6 hiệu ứng + AI soát chính tả) · ✂️ cắt lặng · 🎯 AI cắt Shorts | Whisper + Qwen3/Claude |
| 🫥 Làm mờ mặt (chống nhấp nháy) · 🪄 tách nền người (webm alpha) | YuNet · RVM |
| 🔍 Upscale ×2–4 · 🎞️ nội suy 60fps/slow-mo | Real-ESRGAN · RIFE (GPU) |
| 🗣️ TTS **5 giọng** (2 Việt + 3 Anh) · 📻 audiogram · 🥁 beat-sync · 🎚️ chuẩn âm (đo LUFS) | Piper · librosa · FFmpeg |
| 📱 **Auto Reframe bám chủ thể** (9:16/4:5/1:1 lia theo mặt) · 💆 **mịn da retouch** · 🧹 **tự cắt từ đệm** ừm/uh · ✨ đẹp màu 1 chạm · 🎭 đổi giọng 6 kiểu | YuNet · Whisper · FFmpeg |

### 🧠 Não Claude (gói sub — tuỳ chọn, có công tắc trong ⚙)
| Tính năng | Mô tả |
|---|---|
| 🎬 **Đạo diễn AI** | ra lệnh bằng lời → tự xếp job/chuỗi · nhớ hội thoại · hiểu video đang chọn · chat nổi mọi màn hình |
| 🌐 **Dịch phụ đề** (12 ngôn ngữ) · 🎙️ **Lồng tiếng AI** | giữ mốc thời gian · Piper đọc tiếng đích |
| 🔍 **QC video** (soi lỗi + tự cắt) · ✍️ **Bác sĩ kịch bản** (viết lại + teleprompter) | chấm điểm + timestamp |
| 🖼️ **Claude Vision chọn thumbnail** · 📢 **Tái chế nội dung** · 📝 viết tiêu đề/SEO · 📚 giáo án + quiz → AI-LMS · 🎞️ B-roll Pexels | hết hạn mức → tự chuyển AI local |

### 🛠️ Dựng cơ bản
📐 9:16 nền mờ CapCut · ⏩ tốc độ 0.5–3× · 🎨 6 filter màu · 🎵 nhạc nền + ducking (+🔇 tắt tiếng gốc) · 🧷 chống rung 2 mức · 🎬 ghép clip xfade 10 hiệu ứng · 🏷️ tiêu đề & logo · 📤 xuất preset TikTok/YouTube/1:1/4:5/GIF/MP3 · 🗑 quản lý kho media · hàng đợi 40 job + hủy + batch

📜 **Chi tiết từng bản cập nhật:** [CHANGELOG.md](CHANGELOG.md)

## 🏗️ Kiến trúc

```
frontend/   React + Vite (build sẵn vào frontend/dist, backend serve luôn)
backend/    FastAPI (main.py) + venv riêng — cổng 8765, chỉ bind 127.0.0.1
binaries/   ffmpeg static (libass) · realesrgan · rife · rvm · sam2 · clip · piper/voices · fonts
desktop/    Tauri v2 shell → Local Studio.app (tự spawn backend)
workspace/  uploads / outputs / projects / clipidx / tmp
```

- Model AI tải 1 lần đầu (Whisper/Qwen vào cache HuggingFace) → sau đó **offline hoàn toàn**.
- API chỉ bind `127.0.0.1`; Claude CLI gọi với **toàn bộ tool bị vô hiệu** (chống prompt-injection).

## 👨‍💻 Dev

```bash
# backend (hot reload)
backend/.venv/bin/python -m uvicorn main:app --reload --port 8765 --app-dir backend
# frontend (dev server, proxy sẵn về 8765)
cd frontend && npm run dev
# build frontend
cd frontend && npm run build
```

## 🗺️ Roadmap

Xem `ROADMAP.md` — tiếp theo: multi-keyframe, trim clip PiP, LLM agent panel, sinh video local (FramePack/Wan).

---

<p align="center"><sub>🤖 Xây dựng cùng <a href="https://claude.com/claude-code">Claude Code</a> · Tối ưu cho Apple Silicon</sub></p>
