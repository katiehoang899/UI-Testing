#!/usr/bin/env bash
# Chạy UI Spec Checker trên máy (macOS / Linux): ./run.sh  rồi mở http://localhost:8000
set -e
cd "$(dirname "$0")"
PORT="${PORT:-8000}"

if ! command -v tesseract >/dev/null 2>&1 && [ -z "$TESSERACT_CMD" ]; then
  echo "Chưa có Tesseract OCR. Cài đặt:"
  echo "  macOS : brew install tesseract tesseract-lang"
  echo "  Ubuntu: sudo apt install tesseract-ocr tesseract-ocr-vie"
  exit 1
fi
if ! tesseract --list-langs 2>/dev/null | grep -q '^vie$'; then
  echo "Cảnh báo: thiếu gói tiếng Việt cho Tesseract (vie) - text tiếng Việt sẽ đọc kém hơn."
fi

PY="$(command -v python3 || command -v python)"
if [ ! -d .venv ]; then
  echo "Tạo môi trường Python (.venv)..."
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt

echo "UI Spec Checker đang chạy: http://localhost:$PORT  (Ctrl+C để dừng)"
( sleep 2; (command -v open >/dev/null && open "http://localhost:$PORT") || (command -v xdg-open >/dev/null && xdg-open "http://localhost:$PORT") ) >/dev/null 2>&1 &
exec python -m uvicorn uicheck.server:app --host 127.0.0.1 --port "$PORT"
