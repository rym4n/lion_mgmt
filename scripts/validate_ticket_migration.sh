#!/usr/bin/env bash
# Validate the ticket migration's required files and focused test suite.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "Validating ticket migration..."

REQUIRED_FILES=(
    "services/ticket/views.py"
    "services/ticket/engine.py"
    "services/ticket/permissions.py"
    "models/ticket.py"
    "tests/ticket/test_api_action.py"
)

MISSING=0
for file in "${REQUIRED_FILES[@]}"; do
    if [[ ! -f "$file" ]]; then
        echo "Missing required file: $file"
        MISSING=1
    fi
done

if [[ "$MISSING" -ne 0 ]]; then
    echo "Ticket migration validation failed: required files are incomplete."
    exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Ticket migration validation failed: Python is not executable: $PYTHON_BIN"
    exit 1
fi

echo "  Running ticket tests ..."
"$PYTHON_BIN" -m pytest tests/ticket/ -q --tb=no

echo "Ticket migration validation passed."
