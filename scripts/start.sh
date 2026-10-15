#!/bin/sh
# Container entrypoint for hosted deploys (Render): migrate, then serve.
set -e

PORT="${PORT:-8000}"

# All-in-one mode: the in-process worker calls the simulator, and the simulator sends its
# webhooks back, both over localhost on this same server, never out through the internet.
if [ "$MOUNT_SIMULATOR" = "true" ]; then
  export VOICE_API_URL="${VOICE_API_URL:-http://127.0.0.1:$PORT/sim}"
  export PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-http://127.0.0.1:$PORT}"
fi

# Safe on every start: migrations already applied are skipped.
alembic upgrade head

# exec: uvicorn replaces the shell, so it receives the platform's SIGTERM and shuts down cleanly.
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --proxy-headers
