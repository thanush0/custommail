#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
docker compose config --quiet
docker compose ps
curl -fsS http://127.0.0.1:8000/health
echo
