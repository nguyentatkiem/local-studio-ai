#!/bin/bash
# Local Studio — khởi động một cú nhấp trên macOS/Linux
# Tự tạo venv + cài phụ thuộc + build frontend nếu thiếu, rồi mở trình duyệt.
set -e
cd "$(dirname "$0")"

PORT=8765
PY=python3.11
command -v $PY >/dev/null 2>&1 || PY=python3

# ffmpeg hệ thống (nếu binaries/ffmpeg chưa có bản đóng gói)
export PATH="/opt/homebrew/bin:$PATH"

if [ ! -x backend/.venv/bin/python ]; then
  echo "==> Tạo môi trường Python lần đầu..."
  $PY -m venv backend/.venv
  backend/.venv/bin/pip install --upgrade pip -q
  backend/.venv/bin/pip install -r backend/requirements.txt -q
  backend/.venv/bin/pip install -q mlx-whisper piper-tts || true
fi

if [ ! -f frontend/dist/index.html ]; then
  echo "==> Build giao diện lần đầu..."
  (cd frontend && npm install --silent && npm run build --silent)
fi

# Đã có server đang chạy thì chỉ mở trình duyệt
if curl -s -o /dev/null "http://127.0.0.1:$PORT/"; then
  echo "==> Local Studio đang chạy sẵn."
else
  echo "==> Khởi động Local Studio tại http://127.0.0.1:$PORT ..."
  backend/.venv/bin/python -m uvicorn main:app \
    --host 127.0.0.1 --port $PORT --app-dir backend &
  SERVER_PID=$!
  for _ in $(seq 1 40); do
    curl -s -o /dev/null "http://127.0.0.1:$PORT/" && break
    sleep 0.5
  done
fi

if [ "$(uname)" = "Darwin" ]; then open "http://127.0.0.1:$PORT"; else xdg-open "http://127.0.0.1:$PORT"; fi
echo "==> Sẵn sàng. Mật khẩu admin (hiện 1 lần khi tạo): backend/ADMIN-PASSWORD.txt"
[ -n "$SERVER_PID" ] && wait $SERVER_PID
