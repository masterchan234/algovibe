#!/bin/bash
set -e

# Configuration
REMOTE_USER="root"
REMOTE_HOST="${REMOTE_HOST:-80.241.217.181}"
REMOTE_DIR="/root/algovibe"
REGISTRY_IP="${REGISTRY_IP:-80.241.217.181}"
IMAGE_NAME="$REGISTRY_IP:5000/algovibe:latest"
REGISTRY_USER="${REGISTRY_USER:-chandra}"

if [ -z "$REGISTRY_PASS" ]; then
    echo "❌ ERROR: Set REGISTRY_PASS env var sebelum deploy."
    exit 1
fi

echo "🚀 Starting Algovibe Deployment to $REMOTE_HOST..."
echo "========================================="

# 1. Pull latest image on remote server
echo "📥 Step 1: Pulling latest image on remote server..."
ssh "$REMOTE_USER@$REMOTE_HOST" \
    "echo '$REGISTRY_PASS' | docker login '$REGISTRY_IP:5000' -u '$REGISTRY_USER' --password-stdin && docker pull $IMAGE_NAME"

# 2. Restart container with zero-downtime
echo "🔄 Step 2: Restarting container..."
ssh "$REMOTE_USER@$REMOTE_HOST" \
    "cd $REMOTE_DIR && docker compose up -d --force-recreate"

# 3. Wait for healthcheck to pass
echo "⏳ Step 3: Waiting for health check to pass (max 60s)..."
for i in $(seq 1 12); do
    STATUS=$(ssh "$REMOTE_USER@$REMOTE_HOST" \
        "docker inspect --format='{{.State.Health.Status}}' algovibe 2>/dev/null || echo 'starting'")
    echo "  Health: $STATUS (attempt $i/12)"
    if [ "$STATUS" = "healthy" ]; then
        break
    fi
    if [ "$STATUS" = "unhealthy" ]; then
        echo "❌ Container is unhealthy! Fetching logs..."
        ssh "$REMOTE_USER@$REMOTE_HOST" "docker logs --tail 50 algovibe"
        exit 1
    fi
    sleep 5
done

if [ "$STATUS" != "healthy" ]; then
    echo "⚠️  Health check did not pass in time. Check logs manually."
    ssh "$REMOTE_USER@$REMOTE_HOST" "docker logs --tail 30 algovibe"
    exit 1
fi

echo ""
echo "✅ Deployment complete! Container is healthy."
echo "========================================="
echo "Dashboard: http://$REMOTE_HOST:8080"
echo "Logs:      ssh $REMOTE_USER@$REMOTE_HOST 'docker logs -f algovibe'"
echo "========================================="
