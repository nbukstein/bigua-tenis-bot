#!/usr/bin/env bash
# Disparado por cron en el VPS. Ver DEPLOY_VPS.md.
# El propio script maneja su log rotado (guarda las ultimas 3 corridas) — el
# crontab NO debe redirigir con >> cron.log, o el archivo crece sin limite.
set -euo pipefail
cd "$(dirname "$0")/.."

KEEP=3
[ -f "cron.log.$KEEP" ] && rm -f "cron.log.$KEEP"
for ((i = KEEP - 1; i >= 1; i--)); do
    [ -f "cron.log.$i" ] && mv "cron.log.$i" "cron.log.$((i + 1))"
done
[ -f cron.log ] && mv cron.log cron.log.1
exec > cron.log 2>&1

# Traer los cambios que el panel (Vercel) haya commiteado a config.json.
# --ff-only: si hay un conflicto raro (ej. un push a mano mal hecho), preferimos
# fallar fuerte antes que correr con un estado del repo que nadie entiende.
git pull --ff-only

set -a
source .env
set +a

# motor: 'simple' (default) usa reservar.py de siempre. 'paralelo' es
# experimental (reservar_paralelo.py, ver config.json) — no cambia nada del
# camino default.
MOTOR=$(.venv/bin/python -c "import json; print(json.load(open('config.json')).get('motor', 'simple'))")
if [ "$MOTOR" = "paralelo" ]; then
    exec .venv/bin/python reservar_paralelo.py "$@"
else
    exec .venv/bin/python reservar.py "$@"
fi
