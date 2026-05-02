#!/bin/bash
set -e

# Configuration
REGISTRY_IP=${REGISTRY_IP:-"80.241.217.181"}
IMAGE_NAME="$REGISTRY_IP:5000/algovibe:latest"

echo "🚀 Starting Algovibe Build & Deployment process..."
echo "========================================="
echo "Target Registry: $REGISTRY_IP"
echo "Image Name:      $IMAGE_NAME"
echo "========================================="

# Enable BuildKit for faster, more efficient builds
export DOCKER_BUILDKIT=1

REGISTRY_USER=${REGISTRY_USER:-chandra}
if [ -z "$REGISTRY_PASS" ]; then
    echo "❌ ERROR: Set REGISTRY_PASS env var sebelum push."
    exit 1
fi

echo "🔐 Logging in to registry..."
echo "$REGISTRY_PASS" | docker login "$REGISTRY_IP:5000" -u "$REGISTRY_USER" --password-stdin

echo "📦 Step 1: Building Docker Image..."
# Build image with progress bar
docker build \
  --progress=plain \
  -t $IMAGE_NAME .

echo ""
echo "📤 Step 2: Pushing Image to Registry..."
# Push image to registry
docker push $IMAGE_NAME

echo ""
echo "✅ DONE! Deployment package ready."
echo "========================================="
echo "To deploy on target server:"
echo "1. Pull the new image: docker pull $IMAGE_NAME"
echo "2. Restart container:  docker-compose up -d"
echo "========================================="
