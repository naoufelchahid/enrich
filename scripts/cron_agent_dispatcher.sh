#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/home/naoufel/projects/Enrich"
cd "$PROJECT_DIR"

if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

export PYTHONPATH="$PROJECT_DIR"
PYTHON_BIN="$(which python3)"

LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"
TODAY="$(date +%Y-%m-%d)"
LOG_FILE="$LOG_DIR/outreach_cron_${TODAY}.log"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting task: $*" >> "$LOG_FILE"
"$PYTHON_BIN" "$PROJECT_DIR/scripts/outreach_dispatcher.py" "$@" >> "$LOG_FILE" 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Finished task: $*" >> "$LOG_FILE"
