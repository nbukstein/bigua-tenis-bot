# Correr en un VPS propio (reloj propio, sin depender del scheduler de GH Actions)

Por que: el `schedule:` de GitHub Actions no tiene SLA de horario y se observo
un atraso de ~1h40 en un dia real, lo cual hizo perder la apertura de las
21:00. El script en si espera el segundo exacto con precision (ver
`esperar_apertura` en `reservar.py`); el problema era el arranque del job, no
la logica de espera. Un cron en un servidor que vos controlas no tiene ese
intermediario.

## Setup (una vez)

En el VPS (cualquier Ubuntu/Debian chico alcanza, ej. $5/mes):

```bash
git clone <tu-repo> bigua-tenis-bot
cd bigua-tenis-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install --with-deps chromium

cp .env.example .env
# editar .env con BIGUA_DOCUMENTO, BIGUA_PASSWORD, etc.
```

## Cron

`crontab -e` y agregar (el VPS debe tener su reloj/timezone correctos —
verificar con `timedatectl`, o forzar TZ en la linea de cron):

```
55 20 * * * TZ=America/Montevideo /ruta/a/bigua-tenis-bot/scripts/run_vps.sh >> /ruta/a/bigua-tenis-bot/cron.log 2>&1
```

Arranca a las 20:55 -03, 5 minutos de colchon para el login antes de que el
script se ponga a esperar el segundo exacto de apertura (21:00:00).

## Notas

- `estado.json` (en el repo, no en `.env`) evita reservar dos veces el mismo
  dia si por lo que sea el cron dispara mas de una vez.
- El workflow de GitHub Actions (`reservar.yml`) queda activo solo para
  `workflow_dispatch` manual (probar cambios, `--dry-run`, etc), ya no tiene
  `schedule:`.
- Actualizar el repo del VPS con `git pull` cuando cambie `config.json` o el
  script (o agregar un `git pull` al principio de `run_vps.sh` si preferis
  que se autoactualice).
