#!/usr/bin/env bash
# GitHub Action entry — reviews PR diff and writes review.md
set -euo pipefail
DIFF_FILE="${1:-/tmp/pr.diff}"
OUT="${2:-review.md}"
export PYTHONPATH="${GITHUB_ACTION_PATH:-.}/src:${PYTHONPATH:-}"
python3 -m ai_code_reviewer review --diff-file "$DIFF_FILE" --format markdown > "$OUT"
echo "Wrote $OUT"
