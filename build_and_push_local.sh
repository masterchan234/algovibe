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

echo "🔐 Logging in to registry..."
echo "$REGISTRY_PASS" | docker login "$REGISTRY_IP:5000" -u "$REGISTRY_USER" --password-stdin

echo "📦 Step 1: Building Docker Image..."
docker build \
  --progress=plain \
  -t $IMAGE_NAME .

echo ""
echo "📤 Step 2: Pushing Image to Registry (Local Path)..."
# Proses ini sekarang akan sangat cepat (disk-to-disk)
docker push $IMAGE_NAME

echo ""
echo "✅ DONE! Deployment package ready."
echo "========================================="
echo "To deploy on this server:"
echo "1. Pull the new image: docker pull $IMAGE_NAME"
echo "2. Restart container:  docker-compose up -d"
echo "========================================="