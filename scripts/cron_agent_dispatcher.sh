#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

export PYTHONPATH="$PROJECT_DIR"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(command -v python3)"
fi
export PATH="${WORKFLOW_CODEX_BIN_DIR:+$WORKFLOW_CODEX_BIN_DIR:}/home/naoufel/.local/bin:/usr/local/bin:$PATH"

LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"
TODAY="$(date +%Y-%m-%d)"
LOG_FILE="$LOG_DIR/outreach_cron_${TODAY}.log"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting task: $*" >> "$LOG_FILE"
if [[ " $* " == *" --service "* && " $* " == *" --action "* ]]; then
    "$PYTHON_BIN" -m scripts.compact_workflow "$@" >> "$LOG_FILE" 2>&1
elif [[ " $* " == *" --task linkedin_"* ]]; then
    "$PYTHON_BIN" -m scripts.outreach_dispatcher "$@" >> "$LOG_FILE" 2>&1
else
    "$PYTHON_BIN" -m scripts.workflow "$@" >> "$LOG_FILE" 2>&1
fi
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Finished task: $*" >> "$LOG_FILE"
