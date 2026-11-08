#!/bin/sh
# Copyright (c) 2026 Thanush Priyan R.. All Rights Reserved.
# CustomMail | https://github.com/thanush0/custommail
set -eu
cd "$(dirname "$0")/.."
docker compose config --quiet
docker compose ps
curl -fsS http://127.0.0.1:8000/health
echo
