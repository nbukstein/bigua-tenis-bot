#!/usr/bin/env bash
# Disparado por cron en el VPS. Ver DEPLOY_VPS.md.
set -euo pipefail
cd "$(dirname "$0")/.."

set -a
source .env
set +a

exec .venv/bin/python reservar.py "$@"
