#!/bin/sh
# ProjectMapper launcher.
# When installed as a sidecar at <project>/.project-mapper/, run from any directory.
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$SCRIPT_DIR/.venv/bin/python"
if [ ! -f "$PYTHON" ]; then
    echo "Virtual environment not found. Run setup_env.sh first." >&2
    exit 1
fi
PYTHONPATH="$SCRIPT_DIR/src${PYTHONPATH:+:$PYTHONPATH}" exec "$PYTHON" -m projectmapper "$@"
