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

case "$MODE" in
  morning|evening|headline|brief|landing) ;;
  *)
    echo "Usage: $0 [morning|evening|headline <COIN>|brief|landing [COIN] [COUNT]]"
    exit 1
    ;;
esac

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

# ── Build command ─────────────────────────────────────────────────────────────
CMD=(python3 -m services.news_maker.main --manual "$MODE")
if [[ "$MODE" == "headline" ]]; then
  CMD+=(--coin "${COIN,,}")
fi
if [[ "$MODE" == "landing" ]]; then
  [[ -n "$COIN" ]] && CMD+=(--coin "${COIN,,}")
  CMD+=(--count "$COUNT")
fi

# ── Colors (disabled when not a tty or NO_COLOR=1) ───────────────────────────
if [[ -t 1 && "${NO_COLOR:-}" != "1" ]]; then
  _R=$'\033[0;31m' _G=$'\033[0;32m' _Y=$'\033[0;33m' _C=$'\033[0;36m'
  _B=$'\033[1m'    _D=$'\033[2m'    _X=$'\033[0m'
else
  _R="" _G="" _Y="" _C="" _B="" _D="" _X=""
fi

# ── Spinner ───────────────────────────────────────────────────────────────────
_SPINNER_PID=""
_START_TS=0

_spinner_loop() {
  local label="$1"
  local frames='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
  local i=0
  while true; do
    local e=$(( $(date +%s) - _START_TS ))
    local t
    if (( e >= 60 )); then
      t=$(printf "%dm%02ds" $(( e / 60 )) $(( e % 60 )))
    else
      t=$(printf "%ds" "$e")
    fi
    printf "\r${_D}[%s]${_X} ${_C}%s${_X} %s  ${_Y}⏱ %s${_X}   " \
      "$(date +%H:%M:%S)" "${frames:$(( i % ${#frames} )):1}" "$label" "$t" >&2
    i=$(( i + 1 ))
    sleep 0.25
  done
}

_start_spinner() {
  [[ ! -t 2 ]] && return
  _START_TS=$(date +%s)
  _spinner_loop "$1" &
  _SPINNER_PID=$!
}

_stop_spinner() {
  [[ -z "$_SPINNER_PID" ]] && return
  kill "$_SPINNER_PID" 2>/dev/null || true
  wait "$_SPINNER_PID" 2>/dev/null || true
  _SPINNER_PID=""
  printf "\r\033[K" >&2
}

_elapsed() {
  local e=$(( $(date +%s) - _START_TS ))
  if (( e >= 60 )); then
    printf "%dm%02ds" $(( e / 60 )) $(( e % 60 ))
  else
    printf "%ds" "$e"
  fi
}

# ── Banner ────────────────────────────────────────────────────────────────────
_SEP=$(printf '─%.0s' {1..55})
printf "\n${_B}🗞  Algovibe News Generator${_X}\n"
printf "${_D}mode:${_X} ${_C}%s${_X}" "$MODE"
[[ -n "$COIN" ]]            && printf "  ${_D}coin:${_X} ${_C}%s${_X}"  "${COIN^^}"
[[ "$MODE" == "landing" ]]  && printf "  ${_D}count:${_X} ${_C}%s${_X}" "$COUNT"
printf "\n%s\n\n" "$_SEP"

# ── Spinner label ─────────────────────────────────────────────────────────────
case "$MODE" in
  morning)  _LBL="Generating Morning Briefing..." ;;
  evening)  _LBL="Generating Evening Review..." ;;
  headline) _LBL="Generating Headline — ${COIN^^}..." ;;
  brief)    _LBL="Generating Market Intelligence Brief..." ;;
  landing)  _LBL="Generating Landing Content Pack..." ;;
  *)        _LBL="Generating..." ;;
esac

_start_spinner "$_LBL"
trap '_stop_spinner' EXIT INT TERM

# ── Run ───────────────────────────────────────────────────────────────────────
EXIT_CODE=0
# Prefer running inside the news-maker container so runtime/env matches service.
# If docker CLI is unavailable (e.g. script already running inside container),
# run the Python module directly.
if command -v docker >/dev/null 2>&1 && docker compose ps --services --status=running 2>/dev/null | grep -qx "news-maker"; then
  docker compose exec -T news-maker "${CMD[@]}" || EXIT_CODE=$?
else
  "${CMD[@]}" || EXIT_CODE=$?
fi

_stop_spinner
trap - EXIT INT TERM

# ── Result ────────────────────────────────────────────────────────────────────
printf "\n%s\n" "$_SEP"
if (( EXIT_CODE == 0 )); then
  printf "${_G}${_B}✓ Done${_X}  ${_D}%s${_X}  elapsed: ${_Y}%s${_X}\n\n" "$MODE" "$(_elapsed)"
else
  printf "${_R}${_B}✗ Failed${_X}  ${_D}exit %d${_X}  elapsed: ${_Y}%s${_X}\n\n" "$EXIT_CODE" "$(_elapsed)"
fi

exit $EXIT_CODE
