# Reserva automática — Canchas de tenis Biguá

Reserva sola la cancha de tenis todas las noches a las 21:00, que es cuando el
club libera los cupos del día siguiente.

Tres piezas:

| Pieza | Dónde vive | Para qué |
|---|---|---|
| `reservar.py` | GitHub Actions | El bot. Hace login, espera al segundo exacto y reserva. |
| `config.json` | Este repo | La única fuente de verdad: si está activo, qué horarios, qué C.I. |
| `panel/` | Vercel | La web desde el celular para prender, apagar y cambiar el horario. |

---

## 1. Setup del repo

```bash
git init
git add .
git commit -m "reserva automatica de tenis"
git remote add origin git@github.com:nbukstein/bigua-tenis-bot.git
git push -u origin main
```

### Secrets (Settings → Secrets and variables → Actions)

| Secret | Qué es |
|---|---|
| `BIGUA_DOCUMENTO` | Tu número de documento, sin puntos ni guión |
| `BIGUA_PASSWORD` | Tu contraseña del sitio del Biguá |

Opcionales, para el mail de aviso:

| Secret | Ejemplo |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | tu casilla |
| `SMTP_PASS` | una *app password*, no la contraseña de la cuenta |
| `SMTP_FROM` | igual a `SMTP_USER` |

Sin SMTP el bot igual anda: el resultado queda en el resumen de cada corrida de Actions.

---

## 2. Setup del panel en Vercel

```bash
cd panel
vercel
```

Variables de entorno del proyecto en Vercel:

| Variable | Valor |
|---|---|
| `GH_REPO` | `nbukstein/bigua-tenis-bot` |
| `GH_TOKEN` | PAT *fine-grained* con permiso sobre **este repo**: `Contents: Read and write` + `Actions: Read and write` |
| `PANEL_PASSWORD` | La contraseña para entrar al panel |
| `GH_BRANCH` | `main` (opcional) |

El panel no guarda nada propio: lee y escribe `config.json` de este repo por la API
de GitHub. Cada cambio queda como un commit, así que tenés historial de todo.

---

## 3. Cómo se usa

**Desde el panel** (lo normal): prendés o apagás con el interruptor, elegís los
horarios tocando los chips — el orden en que los tocás es el orden de preferencia — y
guardás. Si se liberó una cancha a media tarde, el botón *Intentar reservar ya*
dispara el runner en el momento.

**Editando `config.json`** a mano:

```jsonc
{
  "activo": true,
  "ci_invitado_default": "1234567",

  // el horario de siempre. 0=lunes … 6=domingo
  "default_semanal": {
    "1": { "horas": [19, 20, 18] },   // martes
    "3": { "horas": [19, 20, 18] }    // jueves
  },

  // pisa al default para un día puntual (la fecha en que se JUEGA)
  "override": {
    "2026-08-25": { "horas": [20, 21], "ci_invitado": "7654321" }
  }
}
```

**Desde Claude**: *"apagá el bot"*, *"mañana quiero a las 20"*. Claude commitea el cambio.

---

## 4. Correrlo a mano

```bash
pip install -r requirements.txt
playwright install chromium
export BIGUA_DOCUMENTO=... BIGUA_PASSWORD=...

python reservar.py --dry-run --ahora          # busca el horario, no reserva
python reservar.py --ahora --capturar         # reserva ya y guarda screenshots
python reservar.py --fecha 2026-08-25         # espera hasta las 21:00 del día 24
```

---

## Detalles del club que condicionan el diseño

- Las canchas se habilitan **a las 21:00 del día anterior**.
- **Una sola cancha de tenis por día** (más dos clases de fitness).
- Hay que dar la **C.I. del socio invitado**, y esa persona tiene **30 minutos
  para aceptar** desde su agenda. Si no acepta, la reserva se cae y la cancha
  vuelve a quedar libre. Por eso conviene tener el mail de aviso prendido.

## Notas técnicas

El sitio es una app **GeneXus + WorkWithPlus**. Los slots se renderizan del hidden
`ExtrafreestylegridContainerDataV` y el botón `BTNRESERVARCLASE_00NN` dispara un POST
AJAX con `events: ["'DORESERVARCLASE'"]`.

El bot usa Playwright en vez de pegarle directo al endpoint. Es unos segundos más
lento, pero no depende de reproducir el `GXState` ni los hashes de seguridad, que
cambian en cada render y romperían el script ante cualquier cambio del sitio.

El cron arranca a las 20:25 porque el scheduler de GitHub Actions se atrasa entre 5 y
20 minutos. El script duerme hasta las 21:00:00.000 y recién ahí dispara.
