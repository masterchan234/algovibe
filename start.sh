#!/bin/bash
set -e


MODEL_DIR="/app/models"
MODEL_FILE="$MODEL_DIR/gemma-4-E2B-it.litertlm"

if [ ! -f "$MODEL_FILE" ]; then
    echo "[algovibe] Model AI tidak ditemukan di $MODEL_FILE"
    echo "[algovibe] Memulai proses download otomatis dari HuggingFace (2.5GB)..."
    
    mkdir -p "$MODEL_DIR"
    
    # Download file dengan curl mengikuti redirect (-L) dan menampilkan progress bar (-O)
    curl -L "https://huggingface.co/litert-community/gemma-4-E2B-it-litert-lm/resolve/main/gemma-4-E2B-it.litertlm" -o "$MODEL_FILE"
    
    echo "[algovibe] Download model selesai!"
else
    echo "[algovibe] Model AI ditemukan, siap digunakan."
fi

echo "[algovibe] Starting bot engine (main.py)..."
python -u main.py &
BOT_PID=$!

echo "[algovibe] Starting dashboard server (dashboard/server.py)..."
python -u dashboard/server.py &
DASH_PID=$!

# Jika salah satu proses mati, matikan yang lain dan exit
wait -n $BOT_PID $DASH_PID
EXIT_CODE=$?

echo "[algovibe] A process exited (code: $EXIT_CODE). Shutting down..."
kill $BOT_PID $DASH_PID 2>/dev/null
exit $EXIT_CODE
