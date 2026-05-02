#!/bin/bash
set -e

# --- [Guard] Validasi env vars kritis sebelum start ---
if [ -z "$SECRET_KEY" ]; then
    echo "[algovibe] ERROR: SECRET_KEY tidak ditemukan di environment!"
    echo "[algovibe] Set SECRET_KEY di .env untuk menjaga keamanan dashboard session."
    exit 1
fi

MODEL_DIR="/app/models"
MODEL_FILE="$MODEL_DIR/gemma-4-E2B-it.litertlm"
MODEL_URL="https://huggingface.co/litert-community/gemma-4-E2B-it-litert-lm/resolve/main/gemma-4-E2B-it.litertlm"
MODEL_EXPECTED_SIZE=2583085056

_download_model() {
    mkdir -p "$MODEL_DIR"
    local max_attempts=3
    for attempt in $(seq 1 $max_attempts); do
        echo "[algovibe] Download attempt $attempt/$max_attempts..."
        curl -L --retry 3 --retry-delay 10 --connect-timeout 30 \
            "$MODEL_URL" -o "$MODEL_FILE"

        local actual_size
        actual_size=$(stat -c%s "$MODEL_FILE" 2>/dev/null || echo 0)
        if [ "$actual_size" -ge "$MODEL_EXPECTED_SIZE" ]; then
            echo "[algovibe] Download model selesai! Size: ${actual_size} bytes."
            return 0
        fi

        echo "[algovibe] File korup atau tidak lengkap (${actual_size}/${MODEL_EXPECTED_SIZE} bytes). Retry..."
        rm -f "$MODEL_FILE"
    done

    echo "[algovibe] ERROR: Gagal download model setelah $max_attempts percobaan."
    exit 1
}

if [ ! -f "$MODEL_FILE" ]; then
    echo "[algovibe] Model AI tidak ditemukan. Mulai download otomatis (2.5GB)..."
    _download_model
else
    echo "[algovibe] Model AI ditemukan, siap digunakan."
fi

# --- [Shutdown Handler] Teruskan SIGTERM ke semua proses agar graceful ---
_shutdown() {
    echo "[algovibe] Received shutdown signal. Stopping all processes..."
    kill "$BOT_PID" "$DASH_PID" "$TRAINER_PID" 2>/dev/null
    wait "$BOT_PID" "$DASH_PID" "$TRAINER_PID" 2>/dev/null
    echo "[algovibe] All processes stopped."
    exit 0
}
trap _shutdown SIGTERM SIGINT

echo "[algovibe] Starting bot engine (main.py)..."
python -u main.py &
BOT_PID=$!

echo "[algovibe] Starting dashboard server (dashboard/server.py)..."
python -u dashboard/server.py &
DASH_PID=$!

echo "[algovibe] Starting RF auto-trainer (app/infrastructure/auto_trainer.py)..."
python -u app/infrastructure/auto_trainer.py &
TRAINER_PID=$!

# Jika bot atau dashboard mati, shutdown semua (trainer boleh crash sendiri, tidak critical)
wait -n $BOT_PID $DASH_PID
EXIT_CODE=$?

echo "[algovibe] A critical process exited (code: $EXIT_CODE). Shutting down..."
kill $BOT_PID $DASH_PID $TRAINER_PID 2>/dev/null
wait $BOT_PID $DASH_PID $TRAINER_PID 2>/dev/null
exit $EXIT_CODE
