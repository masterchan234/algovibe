#!/bin/bash
# ==============================================================================
# AlgoVibe Installation & Orchestration Script
# ==============================================================================
set -e

# Colors for modern styling
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Default config file
COMPOSE_FILE="docker-compose-dev.yml"
MODE="Development"

# Parse arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --prod|-p) COMPOSE_FILE="docker-compose.yml"; MODE="Production"; shift ;;
        --dev|-d) COMPOSE_FILE="docker-compose-dev.yml"; MODE="Development"; shift ;;
        *) echo "Unknown parameter: $1"; exit 1 ;;
    esac
done

echo -e "${BLUE}${BOLD}======================================================================${NC}"
echo -e "${BLUE}${BOLD}   🚀 AlgoVibe — Fresh Installation & Orchestration Script            ${NC}"
echo -e "${BLUE}${BOLD}   Mode: $MODE ($COMPOSE_FILE)                                        ${NC}"
echo -e "${BLUE}${BOLD}======================================================================${NC}"

# 1) Environment Check
echo -e "\n${BLUE}[1/6] Checking environment requirements...${NC}"
if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ Error: Docker is not installed. Please install Docker first.${NC}"
    exit 1
fi

if ! command -v docker compose &> /dev/null; then
    echo -e "${RED}❌ Error: Docker Compose v2 is not installed. Please install it first.${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Docker and Docker Compose are available.${NC}"

# 2) Setup Env File
echo -e "\n${BLUE}[2/6] Setting up environment variables...${NC}"
if [ ! -f .env ]; then
    if [ -f .env-example ]; then
        cp .env-example .env
        echo -e "${YELLOW}⚠️  Created .env from .env-example. Please update your API credentials in .env!${NC}"
    else
        echo -e "${RED}❌ Error: .env-example not found. Please create .env manually.${NC}"
        exit 1
    fi
else
    echo -e "${GREEN}✅ .env file already exists.${NC}"
fi

# 3) Setup External Docker Networks
echo -e "\n${BLUE}[3/6] Setting up external Docker networks...${NC}"
if ! docker network inspect npm_default &> /dev/null; then
    echo -e "${YELLOW}⚠️  External network 'npm_default' not found. Creating it...${NC}"
    docker network create npm_default
    echo -e "${GREEN}✅ External network 'npm_default' successfully created.${NC}"
else
    echo -e "${GREEN}✅ External network 'npm_default' already exists.${NC}"
fi

# 4) Build Frontend Dashboard Assets
echo -e "\n${BLUE}[4/6] Building frontend dashboard assets via Docker (no host node/npm required)...${NC}"
docker run --rm \
  -v "$(pwd)/dashboard/frontend:/app" \
  -v "$(pwd)/dashboard/static:/static" \
  -w /app \
  node:20-alpine \
  sh -c "npm install && npm run build"
echo -e "${GREEN}✅ Frontend dashboard built successfully!${NC}"

# 5) Launch Database & Apply Migrations First
echo -e "\n${BLUE}[5/6] Launching PostgreSQL/TimescaleDB & Running Migrations...${NC}"
docker compose -f "$COMPOSE_FILE" up -d timescaledb

echo -e "${BLUE}⏳ Waiting for Database to become healthy...${NC}"
for i in {1..30}; do
    if docker inspect --format='{{json .State.Health.Status}}' algovibe-timescaledb 2>/dev/null | grep -q '"healthy"'; then
        echo -e "\n${GREEN}✅ Database is healthy and accepting connections!${NC}"
        break
    fi
    echo -n "."
    sleep 2
done

# Run migrations using the temporary container
echo -e "\n${BLUE}🚀 Applying Alembic database migrations...${NC}"
docker compose -f "$COMPOSE_FILE" run --rm executive-core python3 -m app.infrastructure.migrations

# 6) Bring Up Remaining Services
echo -e "\n${BLUE}[6/6] Bringing up AlgoVibe microservices...${NC}"
docker compose -f "$COMPOSE_FILE" up -d

echo -e "\n${GREEN}${BOLD}======================================================================${NC}"
echo -e "${GREEN}${BOLD}   🎉 AlgoVibe Setup Completed Successfully!                           ${NC}"
echo -e "${GREEN}${BOLD}======================================================================${NC}"
echo -e "${BLUE}Useful commands:${NC}"
echo -e "  - View live logs:      ${BOLD}docker compose -f $COMPOSE_FILE logs -f${NC}"
echo -e "  - Stop application:    ${BOLD}docker compose -f $COMPOSE_FILE down${NC}"
echo -e "  - Dashboard address:   ${BOLD}http://localhost:8080${NC}"
echo -e "${BLUE}======================================================================${NC}"
