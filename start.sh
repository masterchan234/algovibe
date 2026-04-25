#!/bin/bash
set -e

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
