#!/usr/bin/env bash
# scripts/build_proto.sh
# Re-generate protobuf Python stubs from proto/algovibe.proto
# Requires:  pip install grpcio-tools
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

echo "[build_proto] Generating Python stubs..."
python3 -m grpc_tools.protoc \
    -I "${REPO_ROOT}/proto" \
    --python_out="${REPO_ROOT}/shared" \
    "${REPO_ROOT}/proto/algovibe.proto"

echo "[build_proto] Done → shared/algovibe_pb2.py"
