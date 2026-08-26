#!/usr/bin/env python3
"""
Reserva automatica de canchas de tenis del Club Bigua (bigua.uy).

Las canchas se habilitan a las 21:00 del dia anterior. Este script se despierta
unos minutos antes, hace login, espera al segundo exacto de apertura y reserva
el primer horario disponible segun la lista de preferencias.

Uso:
    python reservar.py                    # modo normal (respeta la hora de apertura)
    python reservar.py --ahora            # intenta reservar ya, sin esperar
    python reservar.py --dry-run          # busca el slot pero NO reserva
    python reservar.py --capturar         # vuelca el HTML del modal de invitacion
    python reservar.py --fecha 2026-08-21 # fuerza la fecha a jugar
"""

from __future__ import annotations

import argparse
import json
import os
import re
import smtplib
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout

RAIZ = Path(__file__).parent
CONFIG = RAIZ / "config.json"
ARTEFACTOS = RAIZ / "artefactos"

BASE = "https://bigua.uy"
URL_LOGIN = f"{BASE}/com.biguasocios.ingresosocios"
URL_TENIS = f"{BASE}/com.biguasocios.wpcanchastenis"
URL_AGENDA = f"{BASE}/com.biguasocios.wpagendaclasessocio"

DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}", flush=True)


# --------------------------------------------------------------------------
# Configuracion
# --------------------------------------------------------------------------

@dataclass
class Objetivo:
    """Que queremos reservar, ya resuelto a una fecha concreta."""
    fecha: date
    horas: list[int]
    ci_invitado: str
    canchas: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"{DIAS[self.fecha.weekday()]} {self.fecha.isoformat()} "
            f"horas={self.horas} canchas={self.canchas or 'cualquiera'}"
        )


def cargar_config() -> dict:
    with CONFIG.open(encoding="utf-8") as fh:
        return json.load(fh)


def resolver_objetivo(cfg: dict, fecha_juego: date) -> Objetivo | None:
    """Devuelve el objetivo para la fecha dada, o None si no hay nada configurado."""
    override = (cfg.get("override") or {}).get(fecha_juego.isoformat())
    base = override or (cfg.get("default_semanal") or {}).get(str(fecha_juego.weekday()))

    if not base:
        return None

    horas = base.get("horas") or []
    if not horas:
        return None

    return Objetivo(
        fecha=fecha_juego,
        horas=[int(h) for h in horas],
        ci_invitado=str(base.get("ci_invitado") or cfg.get("ci_invitado_default") or "").strip(),
        canchas=base.get("canchas") or cfg.get("canchas_preferidas") or [],
    )


def momento_apertura(cfg: dict, fecha_juego: date) -> datetime:
    """Las reservas de una fecha se abren a las 21:00 del dia anterior."""
    tz = ZoneInfo(cfg.get("tz", "America/Montevideo"))
    hh, mm = (cfg.get("apertura") or "21:00").split(":")
    return datetime.combine(
        fecha_juego - timedelta(days=1),
        datetime.min.time().replace(hour=int(hh), minute=int(mm)),
        tzinfo=tz,
    )


# --------------------------------------------------------------------------
# Interaccion con el sitio
# --------------------------------------------------------------------------

def login(page: Page, documento: str, password: str, tipo_doc: str = "1") -> None:
    log("Abriendo login…")
    page.goto(URL_LOGIN, wait_until="domcontentloaded")
    page.wait_for_selector("#vUSUARIODOCUMENTONROSTR", timeout=20_000)

    page.select_option("#vUSUARIODOCUMENTOTIPO", tipo_doc)
    page.fill("#vUSUARIODOCUMENTONROSTR", documento)
    page.fill("#vUSERPASSWORD", password)
    page.click("#BTNENTER")

    # El login redirige a wpclases. Si seguimos en ingresosocios, fallo.
    try:
        page.wait_for_url(re.compile(r"wpclases|wpagenda|wpcanchas"), timeout=25_000)
    except PWTimeout:
        raise RuntimeError(
            "El login no redirigio. Revisa documento/contrasena "
            "(o el sitio cambio el formulario)."
        )
    log("Login OK")


PARSEO_SLOT = re.compile(
    r"(\d{2})/(\d{2})/(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*a\s*(\d{1,2}):(\d{2})"
)


def leer_slots(page: Page) -> list[dict]:
    """Lee las tarjetas de canchas disponibles que hay en pantalla."""
    crudo = page.evaluate(
        """() => [...document.querySelectorAll('input[id^=BTNRESERVARCLASE]')].map(b => {
             const suf = b.id.split('_').pop();
             const fila = document.getElementById('ExtrafreestylegridContainerRow_' + suf);
             return { suf, texto: fila ? fila.innerText : '', btn: b.id };
           })"""
    )

    slots = []
    for item in crudo:
        texto = " ".join((item.get("texto") or "").split())
        m = PARSEO_SLOT.search(texto)
        if not m:
            continue
        dd, mo, yy, h1, m1, h2, m2 = (int(x) for x in m.groups())
        cancha = ""
        mc = re.search(r"(Cancha\s*\d+)", texto, re.I)
        if mc:
            cancha = mc.group(1)
        slots.append(
            {
                "btn": item["btn"],
                "cancha": cancha,
                "fecha": date(2000 + yy, mo, dd),
                "hora": h1,
                "texto": texto,
            }
        )
    return slots


def elegir_slot(slots: list[dict], obj: Objetivo) -> dict | None:
    """Primer slot que matchee, respetando el orden de preferencia de horas."""
    candidatos = [s for s in slots if s["fecha"] == obj.fecha]

    if obj.canchas:
        pref = [s for s in candidatos if s["cancha"] in obj.canchas]
        candidatos = pref or candidatos  # si la cancha preferida no esta, no nos plantamos

    for hora in obj.horas:
        for s in candidatos:
            if s["hora"] == hora:
                return s
    return None


def volcar_modal(page: Page, etiqueta: str) -> None:
    """Guarda screenshot + HTML de lo que haya en pantalla. Para descubrir el paso de la C.I."""
    ARTEFACTOS.mkdir(exist_ok=True)
    sello = datetime.now().strftime("%Y%m%d-%H%M%S")
    page.screenshot(path=str(ARTEFACTOS / f"{etiqueta}-{sello}.png"), full_page=True)
    (ARTEFACTOS / f"{etiqueta}-{sello}.html").write_text(page.content(), encoding="utf-8")

    inputs = page.evaluate(
        """() => [...document.querySelectorAll('input,select,textarea')]
             .filter(e => e.offsetParent !== null && e.type !== 'hidden')
             .map(e => ({tag:e.tagName, type:e.type, id:e.id, name:e.name,
                         ph:e.placeholder, label:(e.labels&&e.labels[0]||{}).innerText || ''}))"""
    )
    (ARTEFACTOS / f"{etiqueta}-{sello}-campos.json").write_text(
        json.dumps(inputs, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    log(f"Artefactos guardados: {etiqueta}-{sello}.*  ({len(inputs)} campos visibles)")


def completar_invitacion(page: Page, ci: str, capturar: bool = False) -> str:
    """
    Tras tocar RESERVAR el sitio pide la C.I. del socio invitado.
    La forma exacta del modal todavia no esta confirmada, asi que buscamos
    de forma generica un campo de documento y un boton de confirmacion.
    """
    page.wait_for_timeout(1200)

    if capturar:
        volcar_modal(page, "post-reservar")

    campo = None
    for sel in [
        "input[id*=DOCUMENTO]:visible",
        "input[id*=CEDULA]:visible",
        "input[id*=INVITADO]:visible",
        "input[placeholder*='ocumento']:visible",
        "input[placeholder*='dula']:visible",
    ]:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                campo = loc
                break
        except Exception:
            continue

    if campo is None:
        return "sin-modal"

    if not ci:
        raise RuntimeError(
            "El sitio pide la C.I. del socio invitado pero no hay ninguna "
            "configurada (ci_invitado_default esta vacio)."
        )

    log(f"Modal de invitacion detectado, completando C.I.")
    campo.fill(ci)

    for sel in [
        "input[type=submit]:visible",
        "input[id*=CONFIRM]:visible",
        "button:has-text('CONFIRMAR')",
        "button:has-text('RESERVAR')",
        "input[id*=ACEPTAR]:visible",
    ]:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click()
                page.wait_for_timeout(1500)
                return "confirmado"
        except Exception:
            continue

    return "modal-sin-boton"


def esperar_apertura(cuando: datetime, tz: ZoneInfo) -> None:
    """Duerme grueso y despues afina, para clavar el segundo exacto."""
    while True:
        falta = (cuando - datetime.now(tz)).total_seconds()
        if falta <= 0:
            return
        if falta > 120:
            log(f"Faltan {falta/60:.1f} min para la apertura…")
            time.sleep(min(falta - 60, 300))
        elif falta > 2:
            time.sleep(falta - 1.5)
        else:
            time.sleep(0.05)


# --------------------------------------------------------------------------
# Notificacion
# --------------------------------------------------------------------------

def notificar(cfg: dict, asunto: str, cuerpo: str) -> None:
    destinos = cfg.get("notificar_a") or []
    host = os.getenv("SMTP_HOST")
    if not destinos or not host:
        log("(sin SMTP configurado, no se manda mail)")
        return

    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"] = os.getenv("SMTP_FROM") or os.getenv("SMTP_USER", "")
    msg["To"] = ", ".join(destinos)
    msg.set_content(cuerpo)

    try:
        with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=20) as s:
            s.starttls()
            s.login(os.getenv("SMTP_USER", ""), os.getenv("SMTP_PASS", ""))
            s.send_message(msg)
        log(f"Mail enviado a {destinos}")
    except Exception as exc:
        log(f"No se pudo mandar el mail: {exc}")


def resumen_actions(texto: str) -> None:
    ruta = os.getenv("GITHUB_STEP_SUMMARY")
    if ruta:
        with open(ruta, "a", encoding="utf-8") as fh:
            fh.write(texto + "\n")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ahora", action="store_true", help="no esperar a la hora de apertura")
    ap.add_argument("--dry-run", action="store_true", help="buscar el slot pero no reservar")
    ap.add_argument("--capturar", action="store_true", help="volcar HTML/screenshots del flujo")
    ap.add_argument("--fecha", help="fecha a jugar (YYYY-MM-DD), por defecto manana")
    args = ap.parse_args()

    cfg = cargar_config()
    tz = ZoneInfo(cfg.get("tz", "America/Montevideo"))
    ahora = datetime.now(tz)

    if not cfg.get("activo", True):
        log("El bot esta DESACTIVADO en config.json. Nada que hacer.")
        resumen_actions("### Bot desactivado\nNo se intento ninguna reserva.")
        return 0

    fecha_juego = (
        date.fromisoformat(args.fecha) if args.fecha else (ahora.date() + timedelta(days=1))
    )

    obj = resolver_objetivo(cfg, fecha_juego)
    if obj is None:
        log(f"No hay nada configurado para {fecha_juego} ({DIAS[fecha_juego.weekday()]}).")
        resumen_actions(f"### Sin configuracion para {fecha_juego}\nNo se intento ninguna reserva.")
        return 0

    log(f"Objetivo: {obj}")

    documento = os.environ["BIGUA_DOCUMENTO"]
    password = os.environ["BIGUA_PASSWORD"]
    tipo_doc = os.getenv("BIGUA_TIPO_DOC", "1")

    with sync_playwright() as pw:
        navegador = pw.chromium.launch(headless=True)
        ctx = navegador.new_context(
            locale="es-UY",
            timezone_id=cfg.get("tz", "America/Montevideo"),
            viewport={"width": 1400, "height": 900},
        )
        page = ctx.new_page()
        page.set_default_timeout(20_000)

        try:
            apertura = momento_apertura(cfg, fecha_juego) if not args.ahora else None

            # Esperamos casi hasta la apertura ANTES de loguearnos, para que la
            # sesion este fresca y no expire mientras hacemos tiempo.
            if apertura:
                log(f"Apertura prevista: {apertura.strftime('%Y-%m-%d %H:%M:%S %Z')}")
                esperar_apertura(apertura - timedelta(seconds=180), tz)

            login(page, documento, password, tipo_doc)

            if apertura:
                # Dejamos la pagina de tenis cargada para que el primer refresh vuele.
                page.goto(URL_TENIS, wait_until="domcontentloaded")
                esperar_apertura(apertura, tz)
                log(">>> APERTURA <<<")

            # Poll hasta que aparezca el slot (los cupos tardan unos segundos en publicarse)
            limite = time.monotonic() + (20 if args.ahora else 90)
            elegido = None
            vuelta = 0
            while time.monotonic() < limite:
                vuelta += 1
                page.goto(URL_TENIS, wait_until="domcontentloaded")
                slots = leer_slots(page)
                if vuelta == 1 or slots:
                    log(f"vuelta {vuelta}: {len(slots)} slots — "
                        + ", ".join(f"{s['cancha']} {s['hora']}h {s['fecha']}" for s in slots[:8]))
                elegido = elegir_slot(slots, obj)
                if elegido:
                    break
                time.sleep(1.0)

            if not elegido:
                msg = f"No aparecio ningun horario de la lista {obj.horas} para el {fecha_juego}."
                log(msg)
                resumen_actions(f"### Sin cupo\n{msg}")
                notificar(cfg, "Biguá: no se consiguió cancha", msg)
                return 1

            log(f"Slot elegido: {elegido['cancha']} a las {elegido['hora']}h — {elegido['texto']}")

            if args.dry_run:
                log("DRY RUN: no se toca RESERVAR.")
                resumen_actions(f"### Dry run\nHabria reservado: {elegido['texto']}")
                return 0

            page.click(f"#{elegido['btn']}")
            estado = completar_invitacion(page, obj.ci_invitado, capturar=args.capturar)
            log(f"Estado tras RESERVAR: {estado}")

            page.wait_for_timeout(1500)
            if args.capturar:
                volcar_modal(page, "resultado")

            # Verificacion: la reserva tiene que aparecer en Mi Agenda
            page.goto(URL_AGENDA, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            agenda = page.inner_text("body")
            ok = elegido["cancha"].lower() in agenda.lower()

            detalle = (
                f"Cancha: {elegido['cancha']}\n"
                f"Cuando: {elegido['texto']}\n"
                f"Invitado (C.I.): {obj.ci_invitado or '—'}\n"
                f"Verificado en Mi Agenda: {'si' if ok else 'NO'}\n\n"
                "Recorda que el socio invitado tiene 30 minutos para aceptar "
                "la invitacion desde su agenda, o la reserva se cae."
            )
            log(detalle.replace("\n", " | "))
            resumen_actions(
                f"### {'Reserva confirmada' if ok else 'Reserva dudosa'}\n```\n{detalle}\n```"
            )
            notificar(
                cfg,
                f"Biguá: {'cancha reservada' if ok else 'reserva sin confirmar'} — {elegido['texto']}",
                detalle,
            )
            return 0 if ok else 1

        except Exception as exc:
            log(f"ERROR: {exc}")
            traceback.print_exc()
            try:
                volcar_modal(page, "error")
            except Exception:
                pass
            resumen_actions(f"### Error\n```\n{exc}\n```")
            notificar(cfg, "Biguá: el bot falló", f"{exc}\n\n{traceback.format_exc()}")
            return 2
        finally:
            ctx.close()
            navegador.close()


if __name__ == "__main__":
    sys.exit(main())
