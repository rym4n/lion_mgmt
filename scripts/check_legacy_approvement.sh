#!/usr/bin/env bash
# Reject executable ticket code that still depends on the legacy approvement module.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "Checking for legacy approvement references..."

CHECK_PATHS=(
    "services/ticket"
    "tests/ticket"
    "models/ticket.py"
)

PATTERNS=(
    'from[[:space:]].*approvement'
    'import[[:space:]].*approvement'
    'models\.approvement'
    'Approvement|ApproveInfo'
)

FOUND=0

for path in "${CHECK_PATHS[@]}"; do
    if [[ ! -e "$path" ]]; then
        continue
    fi

    echo "  Checking $path ..."
    for pattern in "${PATTERNS[@]}"; do
        if matches="$(grep -RInE --include='*.py' --exclude-dir='__pycache__' "$pattern" "$path" 2>/dev/null)"; then
            printf '%s\n' "$matches"
            FOUND=1
        fi
    done
done

if [[ "$FOUND" -eq 0 ]]; then
    echo "Legacy approvement check passed."
    exit 0
fi

echo
echo "Legacy approvement check failed."
echo "Remove the references, or narrow the gate only when the reference is intentionally historical."
exit 1
