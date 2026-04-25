#!/bin/bash

# Configuration
REMOTE_USER="root"
REMOTE_HOST="80.241.217.181"
REMOTE_DIR="/root/algovibe" # Menyesuaikan ke folder root di server

echo "🚀 Starting Deployment Sync to $REMOTE_HOST..."

# 1. Sync Database (DNA & History)
# --inplace: Berguna untuk file besar seperti DB agar tidak membuat temporary file berlebih
# --exclude: Jangan kirim file WAL/SHM yang sedang aktif agar tidak corrupt
echo "📦 Syncing Database (DNA)..."
rsync -avz --progress \
    --inplace \
    --exclude='*.db-wal' \
    --exclude='*.db-shm' \
    --exclude='logs/*.log' \
    ./data/ $REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR/data/

# 2. Sync AI Models (LiteRT)
echo "🧠 Syncing AI Models..."
rsync -avz --progress \
    ./models/ $REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR/models/

# 3. Optional: Sync Docker Compose & Env
echo "⚙️ Syncing Config Files..."
rsync -avz \
    docker-compose.yml \
    .env \
    $REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR/

echo "🐳 Restarting Docker Container on Production..."
ssh $REMOTE_USER@$REMOTE_HOST "cd $REMOTE_DIR && docker compose pull && docker compose up -d"

echo "========================================="
echo "✅ Deployment Successful!"
echo "Bot is now running on production with latest DNA."
echo "========================================="
