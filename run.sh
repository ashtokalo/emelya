#!/bin/sh
# Clear bytecode cache to ensure latest code runs
find "$(dirname "$0")/src" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
exec python3 "$(dirname "$0")/src/__main__.py" "$@"
