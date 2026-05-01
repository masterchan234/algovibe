#!/bin/bash

# Configuration
REMOTE_USER="root"
REMOTE_HOST="80.241.217.181"
REMOTE_DIR="/root/algovibe"
LOCAL_AUDIT_DIR="./data/audit"

echo "📥 Starting Data Fetch for Analysis from $REMOTE_HOST..."

# Ensure audit directory exists
mkdir -p $LOCAL_AUDIT_DIR

# 1. Sync Database & Logs (Remote to Local Audit folder)
echo "📦 Fetching Production Data..."
rsync -avz --progress \
    --inplace \
    --exclude='*.db-wal' \
    --exclude='*.db-shm' \
    $REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR/data/ $LOCAL_AUDIT_DIR/

# 2. Rename for Analysis
echo "📝 Renaming files for local analysis..."
if [ -f "$LOCAL_AUDIT_DIR/algotrade.db" ]; then
    mv "$LOCAL_AUDIT_DIR/algotrade.db" "$LOCAL_AUDIT_DIR/production_analysis.db"
    echo "✅ Database renamed to: production_analysis.db"
fi

if [ -f "$LOCAL_AUDIT_DIR/logs/scalper.log" ]; then
    mv "$LOCAL_AUDIT_DIR/logs/scalper.log" "$LOCAL_AUDIT_DIR/logs/production_analysis.log"
    echo "✅ Log renamed to: production_analysis.log"
fi

echo "========================================="
echo "✅ Analysis Data Ready!"
echo "Location: $LOCAL_AUDIT_DIR"
echo "DB: $LOCAL_AUDIT_DIR/production_analysis.db"
echo "LOG: $LOCAL_AUDIT_DIR/logs/production_analysis.log"
echo "========================================="
