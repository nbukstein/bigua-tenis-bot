# Correr en un VPS propio (reloj propio, sin depender del scheduler de GH Actions)

Por que: el `schedule:` de GitHub Actions no tiene SLA de horario y se observo
un atraso de ~1h40 en un dia real, lo cual hizo perder la apertura de las
21:00. El script en si espera el segundo exacto con precision (ver
`esperar_apertura` en `reservar.py`); el problema era el arranque del job, no
la logica de espera. Un cron en un servidor que vos controlas no tiene ese
intermediario.

## Setup (una vez)

En el VPS:

```bash
apt update
apt install -y git python3-venv build-essential
git clone https://github.com/nbukstein/bigua-tenis-bot.git
cd bigua-tenis-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install --with-deps chromium

cp .env.example .env
nano .env   # completar BIGUA_DOCUMENTO, BIGUA_PASSWORD, SMTP_*
```

### Identidad y credenciales de git (necesario para el auto-apagado)

El switch "Activo" del panel es de un solo uso: `reservar.py` se auto-apaga
(`desactivar()`) apenas arranca un intento real, y para que eso se refleje en
GitHub (y no se re-arme solo en la proxima corrida) necesita poder pushear.

```bash
git config --global user.name  "bigua-bot"
git config --global user.email "bigua-bot@users.noreply.github.com"
```

Y darle un token de escritura al remote. Generar un Personal Access Token en
GitHub (Settings → Developer settings → Fine-grained tokens, con permiso
Contents: Read and write sobre este repo) y:

```bash
git remote set-url origin https://<tu-usuario>:<TOKEN>@github.com/nbukstein/bigua-tenis-bot.git
```

(El token queda en `.git/config`, legible solo por root en este server — no
lo commitees ni lo compartas.)

## Cron

Uruguay no tiene horario de verano, asi que el offset a UTC es siempre -3.
La apertura es a las 21:00 -03 = 00:00 UTC del dia siguiente; arrancamos a
las 20:55 -03 = 23:55 UTC, con 5 min de colchon para el login antes de que el
script se ponga a esperar el segundo exacto. **El horario del cron va en UTC
directo** — un `TZ=...` puesto en la linea del cron NO cambia cuando dispara,
solo se pasa como variable de entorno al comando (el propio `reservar.py` ya
usa la zona horaria de `config.json` para todos sus calculos, asi que no hace
falta de todas formas).

```bash
echo "55 23 * * * /root/bigua-tenis-bot/scripts/run_vps.sh >> /root/bigua-tenis-bot/cron.log 2>&1" | crontab -
crontab -l   # confirmar que quedo esa unica linea
```

## Notas

- `run_vps.sh` hace `git pull --ff-only` antes de cada corrida, para traer
  los cambios que el panel (Vercel, vía GitHub API) haya commiteado a
  `config.json` — asi un cambio de horario/activacion hecho a la tarde llega
  a tiempo para esa misma noche.
- `estado.json` es estado local del VPS (esta en `.gitignore`, no se
  versiona): evita reservar dos veces el mismo dia si el cron dispara mas de
  una vez.
- `config.json`'s `activo` es de un solo uso: se apaga solo apenas arranca un
  intento real (no en `--dry-run`). `default_semanal` en el config ya no
  dispara reservas por si solo — solo sirve para prellenar el formulario del
  panel; lo que efectivamente dispara una reserva es `override` para la
  fecha puntual, activado a mano desde el panel.
- El workflow de GitHub Actions (`reservar.yml`) queda activo solo para
  `workflow_dispatch` manual (probar cambios, `--dry-run`, etc.), ya no tiene
  `schedule:`.
