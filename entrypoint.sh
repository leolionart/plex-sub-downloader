#!/bin/sh
set -e

# Fix ownership of bind-mounted directories
mkdir -p /app/data /tmp/plex-subtitles
chown -R appuser:appuser /app/data /tmp/plex-subtitles

# Drop to appuser and exec CMD
exec gosu appuser "$@"
