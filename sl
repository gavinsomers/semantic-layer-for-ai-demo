#!/usr/bin/env bash
# Shortcut for Linux and macOS: ./sl <command> is uv run python -m sl <command>.
set -euo pipefail
# Use only the project's own .venv, whatever the calling shell has set.
unset PYTHONPATH VIRTUAL_ENV
cd "$(dirname "$0")"
exec uv run --quiet python -m sl "$@"
