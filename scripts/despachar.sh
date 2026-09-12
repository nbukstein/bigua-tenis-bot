#!/usr/bin/env bash
# Disparado por el cron del VPS de Uruguay. Decide donde correr la reserva:
# si la Mac de Nicolas esta prendida y conectada (via Tailscale), dispara ahi
# (tiene la latencia mas baja); si no, corre el fallback en este mismo VPS.
set -euo pipefail
cd "$(dirname "$0")/.."

MAC_HOST="bukstein@100.102.151.35"
MAC_KEY="$HOME/.ssh/id_ed25519_mac"
MAC_REPO="/Users/bukstein/development/bigua-tenis-bot"
SSH_OPTS=(-i "$MAC_KEY" -o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=accept-new)

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Chequeando si la Mac esta disponible..." >> despacho.log

if ssh "${SSH_OPTS[@]}" "$MAC_HOST" true 2>>despacho.log; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Mac disponible -> disparando ahi" >> despacho.log
    exec ssh "${SSH_OPTS[@]}" "$MAC_HOST" "cd '$MAC_REPO' && ./scripts/run_vps.sh $*"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Mac no disponible -> corriendo en el VPS" >> despacho.log
    exec ./scripts/run_vps.sh "$@"
fi
