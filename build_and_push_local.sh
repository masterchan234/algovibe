#!/bin/bash
set -e

# Configuration - Diubah ke 127.0.0.1 untuk speed maksimal
REGISTRY_IP=${REGISTRY_IP:-"127.0.0.1"}
IMAGE_NAME="$REGISTRY_IP:5000/algovibe:latest"

echo "🚀 Starting Algovibe Build & Deployment process (LOCAL)..."
echo "========================================="
echo "Target Registry: $REGISTRY_IP"
echo "Image Name:      $IMAGE_NAME"
echo "========================================="

echo "🔄 Step 0: Pulling latest source from git..."
git pull

# Enable BuildKit
export DOCKER_BUILDKIT=1

# Login tetap ada jika wiraworks-registry kamu menggunakan auth
REGISTRY_USER=${REGISTRY_USER:-chandra}
if [ -z "$REGISTRY_PASS" ]; then
    read -r -s -p "🔑 Registry password: " REGISTRY_PASS
    echo ""
    if [ -z "$REGISTRY_PASS" ]; then
        echo "❌ ERROR: Password tidak boleh kosong."
        exit 1
    fi
fi

echo "🔐 Step 1: Logging in to registry..."
echo "$REGISTRY_PASS" | docker login "$REGISTRY_IP:5000" -u "$REGISTRY_USER" --password-stdin

echo "📦 Step 2: Building Docker image (no cache)..."
docker build \
  --no-cache \
  --progress=plain \
  -t $IMAGE_NAME .

echo ""
echo "📤 Step 3: Pushing image to local registry..."
# Proses ini sekarang akan sangat cepat (disk-to-disk)
docker push $IMAGE_NAME

echo ""
echo "🚀 Step 4: Starting containers with Docker Compose..."
docker compose up -d

echo ""
echo "✅ DONE! Build, push, and compose startup completed."
echo "========================================="
echo "Summary:"
echo "- Source updated from git"
echo "- Fresh image built without cache"
echo "- Image pushed to $IMAGE_NAME"
echo "- Containers started via docker compose"
echo "========================================="