#!/usr/bin/env bash
# Starts the Mini Discord server.
#
# run on bash shell with:
#   ./run.sh --host 127.0.0.1 --tcp-port 9000 --http-port 8080
#
# Installs dependencies from requirements.txt on first run
# then execs server.py ahd forwards every argument straight through.

# exit immediately if any command fails
set -e

# Run from this script's own directory
cd "$(dirname "$0")"

# Find a usable Python interpreter; assumes python3
# but python and py cover other setups if applicable.

# command -v onwards basically checks if the command exists and is executable
PYTHON=""
for candidate in python3 python py; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" --version >/dev/null 2>&1; then
        PYTHON="$candidate"
        break
    fi
done

# -z checks if the variable is empty, meaning no interpreter was found
if [ -z "$PYTHON" ]; then
    echo "Error: no working Python interpreter found (tried python3, python, py)." >&2
    exit 1
fi

# Check if Flask is installed; if not, install dependencies from requirements.txt
if ! "$PYTHON" -c "import flask" >/dev/null 2>&1; then
    echo "Installing dependencies from requirements.txt..."
    "$PYTHON" -m pip install -r requirements.txt
fi

# exec replaces this shell process with the interpreter, so server.py
# receives signals (e.g. Ctrl+C) directly instead of them being
# swallowed by an intermediate bash process
exec "$PYTHON" server.py "$@"