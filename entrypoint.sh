#!/bin/sh
set -e

# Fix ownership of bind-mounted directories (tolerate filesystems where chown fails)
mkdir -p /app/data /tmp/plex-subtitles
chown -R appuser:appuser /app/data /tmp/plex-subtitles 2>/dev/null || true

# Self-healing watchdog: auto-kills frozen process so Docker restart policy triggers
(
  sleep 45
  failures=0
  port="${PORT:-8000}"
  while true; do
    sleep 15
    if python -c "import urllib.request; urllib.request.urlopen('http://localhost:${port}/health', timeout=10).read()" >/dev/null 2>&1; then
      failures=0
    else
      failures=$((failures + 1))
      if [ "$failures" -ge 4 ]; then
        echo "[Docker Watchdog] Health check failed 4 consecutive times. Terminating to trigger Docker restart..." >&2
        kill -s TERM 1 2>/dev/null || kill -9 1 2>/dev/null
        exit 1
      fi
    fi
  done
) &

# Drop to appuser and exec CMD
exec gosu appuser "$@"
