# 📜 Nhật ký cập nhật — Local Studio

<p align="center">
  <img src="https://img.shields.io/badge/version-2.2.0-FFB23F?style=for-the-badge" alt="version">
  <img src="https://img.shields.io/badge/t%C3%ADnh%20n%C4%83ng-60-46D6C6?style=for-the-badge" alt="features">
  <img src="https://img.shields.io/badge/100%25-local--first-7ACB6B?style=for-the-badge" alt="local">
</p>

> **Local Studio** — trình dựng video AI chạy **100% trên máy bạn**. Không cloud, không credit, không watermark. Footage không bao giờ rời máy.

---

## 💅 v2.2 — Giao diện CapCut toàn diện · <sub>02/08/2026</sub>

- Theme tối sâu tương phản cao, **accent cyan** kiểu CapCut cho mọi nút hành động.
- **Lưới công cụ dạng thẻ 2 cột** (icon to + tên) thay danh sách dài · **ô tìm công cụ** 🔍 · tab nhóm dính đầu khi cuộn.
- Khung xem lớn hơn, panel tuỳ chọn có tiêu đề icon, timeline/chip/segment đồng bộ màu mới.

## 🤖 v2.1 — Tự động hoá TỐI ĐA · <sub>02/08/2026</sub>

10 tính năng biến Local Studio thành dây chuyền sản xuất content:

- **🚀 AutoPilot** — 1 nút "Làm hết cho tôi": tự dò lời thoại rồi tự quyết chuỗi bước → bản đăng hoàn chỉnh.
- **🎥 Script-to-Video** (CapCut 2026) — chủ đề → AI chia cảnh + lời bình → giọng đọc AI + B-roll Pexels → video từ con số 0.
- **🔥 Thư mục nóng** — thả video vào folder là TỰ xử lý (dò file copy xong mới chạy).
- **⏰ Lịch chạy đêm** + **🧩 Template chuỗi** + **📦 Gói đăng bài .zip** (thumbnail AI + caption + srt + video).
- **📲 Nhận video từ iPhone** qua Apple Shortcuts (token riêng, tự xử lý luôn).
- **🎬 Tách cảnh** · **🔍 Auto punch-in theo câu** · **🌏 Dịch đa ngữ 1 chạm**.

## 🎬 v2.0 — Gói CapCut-parity · <sub>02/08/2026</sub>

5 tính năng đinh của CapCut 2026 — bản local, không phí thuê bao:

- **📱 Auto Reframe bám chủ thể** — AI dò mặt từng khung, khung 9:16/4:5/1:1 *lia mượt theo người nói* (CapCut Pro).
- **🧹 Tự cắt từ đệm** — nghe từng từ, cắt sạch "ừm/à/uh/um" và nối mượt (CapCut Audio Cleanup).
- **💆 Làm mịn da (retouch)** — mịn CHỈ vùng mặt, mắt/môi/nền giữ nét, bám mặt chống nhấp nháy.
- **✨ Đẹp màu 1 chạm** — tự cân bằng trắng + vibrance + nét (CapCut Enhance), 2 phong cách.
- **🎭 Đổi giọng** — sóc chuột · trầm · robot · điện thoại · vang · hang động.

Tất cả nối được vào 🔗 Chuỗi tự động + 📁 Edit cả thư mục + 🎬 Đạo diễn AI ra lệnh miệng.

## 📁 v1.9 — Edit hàng loạt cả THƯ MỤC · <sub>02/08/2026</sub>

- **📁 Xử lý cả thư mục trong ổ cứng** — trỏ vào folder bất kỳ trên máy (quét cả thư mục con), xếp chuỗi bước (chỉnh màu → phụ đề → xuất preset...), chạy cho **mọi video/audio** trong đó. Kết quả vào thư mục con `LocalStudio_Xuat` cạnh file gốc — file gốc giữ nguyên; file lỗi tự bỏ qua, cuối job có báo cáo chi tiết.

## 🛡️ v1.8.1 — Bản "thép" · <sub>02/08/2026</sub>

Tổng rà soát bằng **40 AI agent** (4 mũi đọc code + 5 mũi *dùng thật* toàn bộ API, mỗi lỗi bị kiểm chứng đối kháng độc lập) → tìm và **fix 29 lỗi**, xác nhận **85 hạng mục hoạt động chuẩn**.

| Mức | Đã sửa |
|:---:|---|
| 🔴 | Lớp nhạc trên timeline bỏ qua mốc **kết thúc** — nhạc kêu vượt chỗ người dùng kéo |
| 🔴 | Nhạc/tiếng PiP thêm vào bị **mất lặng lẽ** khi audio gốc ngắn hơn video |
| 🔴 | Chữ chứa `%` bị nuốt khỏi video · Tìm cảnh sập khi 1 file chỉ mục hỏng · 2 job song song ghi **trùng file** |
| 🟡 | Màu chữ `"red"` làm vỡ cả job render · nút timeline chạy nhầm file khi bật "Chọn nhiều" · preview 500 với video kích thước lẻ · nút Dừng đạo diễn không ăn · +20 lỗi khác |

---

## 🎞️ v1.8 — Keyframe & Dự án · <sub>01/08/2026</sub>

- **⤳ Keyframe vị trí** — lớp ảnh/chữ/PiP *bay* từ điểm A → điểm B: kéo 2 điểm ngay trên player, preview thấy chuyển động tức thì.
- **💾 Lưu / Mở dự án** — lưu nguyên timeline (lớp phủ + keyframe + bảng màu + bánh xe nhuộm), hôm sau mở lại làm tiếp.

## 🔎 v1.7 — PiP · Bay vào · Tìm cảnh AI · <sub>01/08/2026</sub>

- **📹 Video-trong-video (PiP)** — đè video thứ 2 lên góc (reaction/facecam), chỉnh cỡ + tiếng riêng.
- **✨ Hiệu ứng bay vào** — mờ dần / trượt 4 hướng cho mọi lớp phủ.
- **🔎 Tìm cảnh bằng AI (CLIP)** — gõ mô tả tiếng Việt → tìm đúng khoảnh khắc trong **cả kho video**, bấm là tua đúng giây.

## 🎯 v1.6 — Multi-track & SAM 2 · <sub>01/08/2026</sub>

- **🎬 Multi-track xếp lớp** — kéo-thả ảnh/logo, chữ, nhạc lên timeline; dời/co giãn block; kéo lớp trực tiếp trên video để đặt vị trí.
- **🎯 Track đối tượng (SAM 2 — Meta AI)** — bấm vào vật/người bất kỳ → AI bám theo suốt video → làm mờ / che ô / spotlight / tách nền xanh.

## ✂️ v1.5 — Timeline NLE & Bánh xe màu · <sub>01/08/2026</sub>

- **✂️ Cắt trực tiếp trên timeline** — kéo tay cầm IN/OUT trên dải khung hình, playhead đồng bộ 2 chiều; giữ đoạn / cắt bỏ / ghép nhiều đoạn.
- **🎨 Bánh xe Nhuộm Tối / Nhuộm Sáng** — chỉnh màu điện ảnh (teal-orange...) bằng 2 vòng tròn màu kéo tay.

## 🎨 v1.4 — Lắng nghe người dùng & Giao diện mới · <sub>01/08/2026</sub>

Fix **14 nhóm góp ý** từ người dùng thật + giao diện mới theo phong cách dashboard chuyên nghiệp:

- 🗑 Xoá được media · 🎞 tự tạo bản xem thử H.264 khi trình duyệt không phát được (mkv/hevc)
- 🎛️ **Bảng chỉnh màu PRO** 10 thanh trượt, xem trước tức thì · tab nhóm công cụ + chip đánh số màu
- ✨ AI soát chính tả phụ đề · model turbo cho caption chuẩn hơn · 3 hiệu ứng chữ viral (Pop 💥 / Hộp nền 🔲)
- 🗣 **5 giọng đọc** (2 Việt + 3 Anh) · 🔇 tắt tiếng gốc khi trộn nhạc · đo LUFS trước/sau · chống rung mức Mạnh
- 🎬 Đạo diễn AI tự hiểu file đang chọn + nút ⏹ Dừng · tự chuyển não local khi Claude hết hạn mức

## 🤖 v1.2 – v1.3 — Sức mạnh gói Claude · <sub>29–30/07/2026</sub>

- **⚙️ Cài đặt gói sub Claude** — bật/tắt, chọn model (haiku/sonnet/opus), test kết nối; tắt là mọi thứ tự chạy AI local.
- 7 tính năng Claude: **🌐 Dịch phụ đề** (12 ngôn ngữ, cháy vào video) · **📢 Tái chế nội dung** (tiêu đề, caption, tweet, LinkedIn...) · **🎙️ Lồng tiếng AI** · **🔍 QC video** (soi lỗi + tự cắt) · **✍️ Bác sĩ kịch bản** (viết lại + teleprompter) · **🖼️ Claude Vision chọn thumbnail** · nâng cấp Đạo diễn AI.

## 🔥 v1.0 – v1.1 — Phụ đề Viral & Chuỗi tự động · <sub>26–28/07/2026</sub>

- **🔥 Phụ đề Viral 1 chạm** — Whisper large-v3 word-level → cụm ≤2 dòng → preset "Viral Trắng Viền Đen" (font Be Vietnam Pro, tô từ khoá vàng, karaoke) → chuẩn âm -14 LUFS → mp4 + ass + srt. Có editor sửa cụm + chạy **offline** hoàn toàn.
- **🔗 Chuỗi tự động (Pipeline)** — xếp nhiều tính năng chạy nối tiếp trên 1 video, 1 job; Đạo diễn AI nối chuỗi bằng lời. Chỉnh 1–4 luồng song song.

## 🌱 v0.3 – v0.9 — Nền móng · <sub>25/07/2026</sub>

Caption Whisper (CPU + **MLX Metal**) · cắt lặng · Real-ESRGAN upscale · RIFE nội suy · tách nền RVM · Piper TTS · dựng 1 chạm · reframe 9:16 · tốc độ · filter màu · nhạc nền ducking · chống rung · merge xfade · beat-sync · chuẩn âm · logo/tiêu đề · audiogram · làm mờ mặt YuNet · não AI cục bộ **Qwen3-4B** · Đạo diễn AI (Claude) · giáo án + quiz đẩy AI-LMS · B-roll Pexels.

---

<p align="center"><sub>🎬 Dựng bằng <b>Claude Code</b> · Chạy trên Apple Silicon (M-series) · FastAPI + React + FFmpeg + Whisper + SAM 2 + CLIP + Qwen3 + Piper</sub></p>
