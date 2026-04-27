#!/bin/bash

# Configuration (Same as deploy_sync.sh)
REMOTE_USER="root"
REMOTE_HOST="80.241.217.181"
REMOTE_DIR="/root/algovibe"

echo "📥 Starting Data Fetch from $REMOTE_HOST..."

# 1. Sync Database & Logs (Remote to Local)
# --inplace: Useful for large files
# --exclude: Avoid active WAL/SHM files to prevent corruption
echo "📦 Fetching Database and Logs..."
rsync -avz --progress \
    --inplace \
    --exclude='*.db-wal' \
    --exclude='*.db-shm' \
    $REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR/data/ ./data/

echo "========================================="
echo "✅ Data Fetch Successful!"
echo "Local database and logs are now synced with production."
echo "========================================="
