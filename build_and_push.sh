#!/bin/bash
set -e

IMAGE_NAME="80.241.217.181:5000/algovibe:latest"

echo "========================================="
echo "Building Docker Image: $IMAGE_NAME"
echo "========================================="
# Build image
docker build -t $IMAGE_NAME .

echo ""
echo "========================================="
echo "Pushing Image to Registry..."
echo "========================================="
# Push image ke registry
docker push $IMAGE_NAME

echo ""
echo "========================================="
echo "DONE! Image successfully built and pushed."
echo "========================================="
echo "You can now run it using:"
echo "docker-compose up -d"
