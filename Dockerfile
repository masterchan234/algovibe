# --- Stage 1: Frontend Builder ---
FROM node:20-slim AS frontend-builder
WORKDIR /app/dashboard/frontend

# Install dependencies first (better caching)
COPY dashboard/frontend/package*.json ./
RUN npm install --frozen-lockfile

# Copy source and build
COPY dashboard/frontend/ .
RUN npm run build

# --- Stage 2: Python Runtime ---
FROM python:3.12-slim

# Timezone & System Deps
ENV TZ=Asia/Jakarta
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata curl ca-certificates && \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy everything else
# NOTE: Model AI (~2.5GB) TIDAK dimasukkan ke image.
# Model di-download saat deploy via 'model-downloader' service di docker-compose.yml.
COPY . .

# Copy built frontend assets from Stage 1
# This ensures we always have the latest UI inside the image
COPY --from=frontend-builder /app/dashboard/static/dist /app/dashboard/static/dist

# Ensure runtime directories exist with correct permissions
RUN mkdir -p /app/data/logs /app/models/cache

# Startup script permissions
RUN chmod +x /app/start.sh

# Security: Create non-root user
RUN useradd -m -u 1000 algovibe && \
    chown -R algovibe:algovibe /app

# Switch to non-root user
USER algovibe

# Dashboard port
EXPOSE 8080

# Run bot + dashboard
CMD ["/app/start.sh"]
