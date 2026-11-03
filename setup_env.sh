#!/bin/sh
# One-time setup for ProjectMapper.
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "[SYSTEM] Initialising ProjectMapper environment..."
if [ ! -d "$SCRIPT_DIR/.venv" ]; then
    echo "[SYSTEM] Creating .venv..."
    python3 -m venv "$SCRIPT_DIR/.venv"
fi
"$SCRIPT_DIR/.venv/bin/pip" install --upgrade pip
if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
    "$SCRIPT_DIR/.venv/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"
fi
"$SCRIPT_DIR/.venv/bin/pip" install -e "$SCRIPT_DIR"
echo ""
echo "[SUCCESS] Environment ready. Launch with ./run.sh"
