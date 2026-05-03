#!/bin/bash
set -e

# --- [Guard] Validasi env vars kritis sebelum start ---
if [ -z "$SECRET_KEY" ]; then
    echo "[algovibe] ERROR: SECRET_KEY tidak ditemukan di environment!"
    echo "[algovibe] Set SECRET_KEY di .env untuk menjaga keamanan dashboard session."
    exit 1
fi

# Model AI dijamin sudah ada di volume karena 'model-downloader' service
# di docker-compose.yml selesai sebelum container ini dijalankan.
MODEL_FILE="/app/models/gemma-4-E2B-it.litertlm"
if [ ! -f "$MODEL_FILE" ]; then
    echo "[algovibe] ⚠️  WARNING: Model tidak ditemukan di $MODEL_FILE"
    echo "[algovibe] Pastikan 'model-downloader' service berhasil sebelum menjalankan bot."
    echo "[algovibe] Jalankan: docker compose up model-downloader"
    exit 1
fi
echo "[algovibe] ✅ Model AI ditemukan, siap digunakan."


# --- [Shutdown Handler] Teruskan SIGTERM ke semua proses agar graceful ---
_shutdown() {
    echo "[algovibe] Received shutdown signal. Stopping all processes..."
    kill "$BOT_PID" "$DASH_PID" "$TRAINER_PID" 2>/dev/null
    wait "$BOT_PID" "$DASH_PID" "$TRAINER_PID" 2>/dev/null
    echo "[algovibe] All processes stopped."
    exit 0
}
trap _shutdown SIGTERM SIGINT

# --- [Database Bootstrap] ---
# Pastikan migrasi selesai sepenuhnya sebelum menjalankan proses paralel
# Ini mencegah race condition "no such table" saat startup pertama kali
echo "[algovibe] Initializing databases (Live & Dry) and running migrations..."
python3 -c "from app.infrastructure.database import db; print('[algovibe] Database schema is up-to-date.')"

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
