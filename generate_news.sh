#!/bin/bash
# Thin wrapper: all generation logic stays in services/news_maker/main.py
# Usage:
#   ./generate_news.sh morning
#   ./generate_news.sh evening
#   ./generate_news.sh headline BTC
#   ./generate_news.sh brief
#   ./generate_news.sh landing [COIN] [COUNT]

set -euo pipefail

MODE="${1:-}"
COIN="${2:-}"
COUNT="${3:-3}"

if [[ -z "$MODE" ]]; then
  echo "Usage: $0 [morning|evening|headline <COIN>|brief|landing [COIN] [COUNT]]"
  exit 1
fi

if [[ "$MODE" != "morning" && "$MODE" != "evening" && "$MODE" != "headline" && "$MODE" != "brief" && "$MODE" != "landing" ]]; then
  echo "Usage: $0 [morning|evening|headline <COIN>|brief|landing [COIN] [COUNT]]"
  exit 1
fi

if [[ "$MODE" == "headline" && -z "$COIN" ]]; then
  echo "Usage: $0 headline <COIN>"
  exit 1
fi

if ! [[ "$COUNT" =~ ^[0-9]+$ ]]; then
  echo "COUNT must be a positive integer"
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

CMD=(python3 -m services.news_maker.main --manual "$MODE")
if [[ "$MODE" == "headline" ]]; then
  CMD+=(--coin "${COIN,,}")
fi
if [[ "$MODE" == "landing" ]]; then
  if [[ -n "$COIN" ]]; then
    CMD+=(--coin "${COIN,,}")
  fi
  CMD+=(--count "$COUNT")
fi

# Prefer running inside the news-maker container so runtime/env matches service.
# If docker CLI is unavailable (e.g. script already running inside container),
# run the Python module directly.
if command -v docker >/dev/null 2>&1 && docker compose ps --services --status=running | grep -qx "news-maker"; then
  docker compose exec -T news-maker "${CMD[@]}"
else
  "${CMD[@]}"
fi
