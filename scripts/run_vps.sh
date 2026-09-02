#!/usr/bin/env bash
# Disparado por cron en el VPS. Ver DEPLOY_VPS.md.
set -euo pipefail
cd "$(dirname "$0")/.."

# Traer los cambios que el panel (Vercel) haya commiteado a config.json.
# --ff-only: si hay un conflicto raro (ej. un push a mano mal hecho), preferimos
# fallar fuerte antes que correr con un estado del repo que nadie entiende.
git pull --ff-only

set -a
source .env
set +a

exec .venv/bin/python reservar.py "$@"
