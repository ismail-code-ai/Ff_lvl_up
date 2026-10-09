#!/usr/bin/env bash
# Start the FreeFire BR Bot web panel
set -e
cd "$(dirname "$0")"
export PYTHONUNBUFFERED=1
export PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python
exec python3 -m uvicorn server:app --host 0.0.0.0 --port "${PORT:-8000}"
