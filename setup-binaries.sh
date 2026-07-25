#!/bin/bash
# Local Studio — tải bộ engine AI vào binaries/ cho máy mới clone repo.
# Hỗ trợ: macOS Apple Silicon (đầy đủ) · macOS Intel/Windows: xem ghi chú cuối file.
set -e
cd "$(dirname "$0")/binaries" 2>/dev/null || { mkdir -p "$(dirname "$0")/binaries"; cd "$(dirname "$0")/binaries"; }

OS=$(uname -s); ARCH=$(uname -m)
echo "==> Hệ: $OS $ARCH"
dl() { echo "  ↓ $2"; curl -sL -o "$1" "$2"; }

if [ "$OS" = "Darwin" ]; then
  # 1. Real-ESRGAN (GPU qua MoltenVK, binary universal)
  if [ ! -x realesrgan/realesrgan-ncnn-vulkan ]; then
    echo "==> Real-ESRGAN"
    mkdir -p realesrgan && dl /tmp/re.zip "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-macos.zip"
    unzip -oq /tmp/re.zip -d realesrgan/ && chmod +x realesrgan/realesrgan-ncnn-vulkan && rm /tmp/re.zip
  fi
  # 2. RIFE (nội suy khung hình) — chỉ giữ model v4.6
  if [ ! -x rife/rife-ncnn-vulkan ]; then
    echo "==> RIFE"
    dl /tmp/rf.zip "https://github.com/nihui/rife-ncnn-vulkan/releases/download/20221029/rife-ncnn-vulkan-20221029-macos.zip"
    mkdir -p rife-tmp && unzip -oq /tmp/rf.zip -d rife-tmp/
    mkdir -p rife && mv rife-tmp/*/rife-ncnn-vulkan rife-tmp/*/rife-v4.6 rife-tmp/*/LICENSE rife/ 2>/dev/null
    chmod +x rife/rife-ncnn-vulkan && rm -rf rife-tmp /tmp/rf.zip
  fi
  # 3. FFmpeg static đầy đủ libass (brew thường thiếu)
  if [ ! -x ffmpeg/ffmpeg ]; then
    echo "==> FFmpeg static (libass)"
    mkdir -p ffmpeg && cd ffmpeg
    A=$([ "$ARCH" = "arm64" ] && echo arm64 || echo amd64)
    dl ffmpeg.zip "https://ffmpeg.martin-riedl.de/redirect/latest/macos/$A/release/ffmpeg.zip"
    dl ffprobe.zip "https://ffmpeg.martin-riedl.de/redirect/latest/macos/$A/release/ffprobe.zip"
    unzip -oq ffmpeg.zip && unzip -oq ffprobe.zip && chmod +x ffmpeg ffprobe && rm -f ffmpeg.zip ffprobe.zip
    cd ..
  fi
fi

# 4. RVM tách nền (ONNX, mọi hệ)
if [ ! -f rvm/rvm_mobilenetv3_fp32.onnx ]; then
  echo "==> RVM (tách nền)"
  mkdir -p rvm && dl rvm/rvm_mobilenetv3_fp32.onnx "https://github.com/PeterL1n/RobustVideoMatting/releases/download/v1.0.0/rvm_mobilenetv3_fp32.onnx"
fi

# 5. Giọng Piper TTS (engine cài qua pip trong requirements.txt)
mkdir -p piper/voices && cd piper/voices
for V in "vi/vi_VN/vais1000/medium/vi_VN-vais1000-medium" "en/en_US/lessac/medium/en_US-lessac-medium"; do
  F=$(basename "$V")
  if [ ! -f "$F.onnx" ]; then
    echo "==> Giọng Piper: $F"
    dl "$F.onnx" "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/$V.onnx"
    dl "$F.onnx.json" "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/$V.onnx.json"
  fi
done
cd ../..

echo "==> Xong. Chạy ./start.sh để khởi động."
# Windows: tải bản windows của realesrgan/rife từ cùng trang GitHub releases,
# ffmpeg full từ gyan.dev (bản có libass), đặt vào binaries/ cùng cấu trúc thư mục.
