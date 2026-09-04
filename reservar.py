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
import subprocess
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
ESTADO = RAIZ / "estado.json"

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


def ya_reservado(fecha_juego: date) -> bool:
    """El cron puede disparar mas de una vez (colchon contra atrasos de GitHub
    Actions). Este marker, commiteado al repo, evita reintentar/duplicar una
    reserva ya confirmada para la misma fecha."""
    if not ESTADO.exists():
        return False
    try:
        return json.loads(ESTADO.read_text(encoding="utf-8")).get("ultima_reserva") == fecha_juego.isoformat()
    except (json.JSONDecodeError, OSError):
        return False


def marcar_reservado(fecha_juego: date) -> None:
    ESTADO.write_text(json.dumps({"ultima_reserva": fecha_juego.isoformat()}), encoding="utf-8")


def desactivar(cfg: dict) -> None:
    """El switch 'Activo' del panel es de un solo uso: cada activacion vale
    para UN intento (la apertura de esa noche). Se consume aca, apenas
    arranca el intento, para que si el usuario no vuelve a entrar al panel
    la proxima apertura no reserve nada por default_semanal ni por inercia
    del switch."""
    cfg["activo"] = False
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        subprocess.run(["git", "add", "config.json"], cwd=RAIZ, check=True)
        subprocess.run(
            ["git", "commit", "-m", "bot: auto-apagado tras el intento de esta noche"],
            cwd=RAIZ, check=True,
        )
        subprocess.run(["git", "push"], cwd=RAIZ, check=True)
        log("Bot desactivado y pusheado (activo=false)")
    except subprocess.CalledProcessError as exc:
        log(f"ADVERTENCIA: no se pudo commitear/pushear la desactivacion: {exc}")


def resolver_objetivo(cfg: dict, fecha_juego: date) -> Objetivo | None:
    """Devuelve el objetivo para la fecha dada, o None si no hay nada configurado.

    Solo mira 'override' (una activacion puntual para esa fecha, hecha a mano
    en el panel). 'default_semanal' es apenas una sugerencia para prellenar el
    formulario del panel — no dispara reservas por si solo, porque el bot es
    de un solo uso por activacion (ver desactivar()).
    """
    base = (cfg.get("override") or {}).get(fecha_juego.isoformat())

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


def sesion_activa(page: Page) -> bool:
    """Si el sitio nos rebota al login, la sesion murio."""
    return "ingresosocios" not in (page.url or "")


def asegurar_sesion(page: Page, documento: str, password: str, tipo_doc: str) -> bool:
    """
    Carga la pagina de tenis y, si nos encontramos deslogueados, vuelve a entrar.
    Devuelve True si hubo que re-loguearse.
    """
    page.goto(URL_TENIS, wait_until="domcontentloaded")
    if sesion_activa(page):
        return False
    log("Sesion caida — re-login")
    login(page, documento, password, tipo_doc)
    page.goto(URL_TENIS, wait_until="domcontentloaded")
    return True


def esperar_apertura(cuando: datetime, tz: ZoneInfo, latido=None, cada: float = 60) -> None:
    """
    Duerme grueso y despues afina, para clavar el segundo exacto.

    `latido` se llama cada `cada` segundos durante la espera. Lo usamos para
    mantener viva la sesion del sitio, que se cae sola por inactividad.
    """
    proximo_latido = time.monotonic()
    while True:
        falta = (cuando - datetime.now(tz)).total_seconds()
        if falta <= 0:
            return

        if latido and falta > 8 and time.monotonic() >= proximo_latido:
            try:
                latido()
            except Exception as exc:
                log(f"El latido fallo (sigo igual): {exc}")
            proximo_latido = time.monotonic() + cada
            continue

        if falta > 120:
            restante = proximo_latido - time.monotonic()
            log(f"Faltan {falta/60:.1f} min para la apertura…")
            time.sleep(max(1.0, min(falta - 5, restante if latido else 300)))
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

    if not args.dry_run:
        desactivar(cfg)

    fecha_juego = (
        date.fromisoformat(args.fecha) if args.fecha else (ahora.date() + timedelta(days=1))
    )

    obj = resolver_objetivo(cfg, fecha_juego)
    if obj is None:
        log(f"No hay nada configurado para {fecha_juego} ({DIAS[fecha_juego.weekday()]}).")
        resumen_actions(f"### Sin configuracion para {fecha_juego}\nNo se intento ninguna reserva.")
        return 0

    if ya_reservado(fecha_juego):
        log(f"Ya hay una reserva confirmada para {fecha_juego} (estado.json). No hago nada.")
        resumen_actions(f"### Ya reservado\n{fecha_juego} ya estaba confirmado, se salta este disparo.")
        return 0

    log(f"Objetivo: {obj}")
    log(
        "MODO: INMEDIATO (--ahora), no espera la apertura"
        if args.ahora
        else f"MODO: espera hasta la apertura ({momento_apertura(cfg, fecha_juego):%H:%M:%S %Z})"
    )

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

        # El sitio es GeneXus: los botones (RESERVAR incluido) suelen disparar
        # un confirm() nativo antes de mandar el post. Sin este handler,
        # Playwright descarta cualquier dialog no manejado por default — el
        # click "funciona" (no tira error) pero no reserva nada, sin dejar
        # rastro visible en pantalla ni en el HTML.
        def aceptar_dialog(dialog):
            log(f"Dialog del sitio: {dialog.type} — {dialog.message!r}")
            dialog.accept()

        page.on("dialog", aceptar_dialog)

        try:
            apertura = momento_apertura(cfg, fecha_juego) if not args.ahora else None

            # Nos logueamos ya: si las credenciales estan mal queremos enterarnos
            # ahora y no a las 21:00 con la ventana abierta.
            login(page, documento, password, tipo_doc)

            if apertura:
                log(f"Apertura prevista: {apertura.strftime('%Y-%m-%d %H:%M:%S %Z')}")

                # La sesion del sitio se cae sola por inactividad, asi que durante
                # la espera le pegamos un toque cada 60 s. Si aun asi se cayo, el
                # latido mismo vuelve a entrar.
                def latido():
                    if asegurar_sesion(page, documento, password, tipo_doc):
                        log("Sesion recuperada por el latido")
                    else:
                        log("Latido: sesion viva")

                # Paramos 15 s antes para dejar la sesion verificada y la pagina
                # cargada. A las 21:00:00 no queremos gastar ni un request en eso.
                esperar_apertura(apertura - timedelta(seconds=15), tz, latido=latido, cada=60)
                if asegurar_sesion(page, documento, password, tipo_doc):
                    log("Re-login sobre la hora")
                else:
                    log("Sesion verificada, esperando el segundo exacto")

                esperar_apertura(apertura, tz)
                log(">>> APERTURA <<<")

            # Poll hasta que aparezca el slot (los cupos tardan unos segundos en publicarse).
            # A las 21:00:00 en punto el sitio recibe a todos los socios juntos y se pone
            # lento — un timeout de una vuelta no puede tirar abajo el intento entero,
            # tiene que reintentar mientras quede tiempo.
            limite = time.monotonic() + (20 if args.ahora else 150)
            elegido = None
            vuelta = 0
            while time.monotonic() < limite:
                vuelta += 1
                try:
                    page.goto(URL_TENIS, wait_until="domcontentloaded")
                    if not sesion_activa(page):
                        log("Nos deslogueo en pleno poll — reentrando")
                        login(page, documento, password, tipo_doc)
                        page.goto(URL_TENIS, wait_until="domcontentloaded")
                    slots = leer_slots(page)
                except Exception as exc:
                    log(f"vuelta {vuelta}: fallo ({exc}), reintento")
                    time.sleep(1.0)
                    continue
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
            if ok:
                marcar_reservado(fecha_juego)
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
