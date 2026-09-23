#!/usr/bin/env bash
# Local development server. Host enforcement is off so http://127.0.0.1:8000
# works without the two real hostnames.
set -euo pipefail
cd "$(dirname "$0")"
export SUPLOAD_ENFORCE_HOSTS="${SUPLOAD_ENFORCE_HOSTS:-0}"
export SUPLOAD_SCHEME="${SUPLOAD_SCHEME:-http}"
export SUPLOAD_APP_HOST="${SUPLOAD_APP_HOST:-127.0.0.1:8000}"
export SUPLOAD_FILE_HOST="${SUPLOAD_FILE_HOST:-127.0.0.1:8000}"
exec ./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8000}" --reload
