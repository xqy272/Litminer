#!/usr/bin/env sh
set -eu

PROFILE=quick
if [ "$#" -gt 0 ]; then
  PROFILE=$1
  shift
fi

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
  PYTHON="$PROJECT_ROOT/.venv/bin/python"
else
  PYTHON=python3
fi

exec "$PYTHON" "$PROJECT_ROOT/scripts/run_ci.py" --profile "$PROFILE" "$@"
