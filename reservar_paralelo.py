#!/usr/bin/env python3
"""
Variante EXPERIMENTAL de reservar.py: en vez de un unico intento, lanza N
procesos del sistema operativo (multiprocessing, no threads — la API sync de
Playwright no soporta threads) cada uno con su propio login y su propio
navegador, todos apuntando al mismo objetivo. El primero que encuentra el
horario se queda con un "ticket" (archivo de lock atomico) y es el unico que
sigue adelante con el click de RESERVAR; los demas se bajan solos.

Aislado a proposito de reservar.py: no lo toca, solo importa sus funciones
ya probadas (login, parseo de slots, completar el formulario) para no
reinventar lo que ya funciona.

Uso: igual que reservar.py.
    python reservar_paralelo.py --ahora --dry-run --capturar --fecha ...
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
import traceback
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, Page

import reservar as base

RAIZ = base.RAIZ
LOCK = RAIZ / "paralelo_ticket.lock"
RESULTADO = RAIZ / "paralelo_resultado.json"


def log_worker(idx: int, msg: str) -> None:
    linea = f"[w{idx} {datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}"
    print(linea, flush=True)
    with (RAIZ / f"paralelo_w{idx}.log").open("a", encoding="utf-8") as fh:
        fh.write(linea + "\n")


def reclamar_ticket(idx: int) -> bool:
    """Atomico: el primer worker que llega gana, el resto se entera al toque."""
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(idx).encode())
        os.close(fd)
        return True
    except FileExistsError:
        return False


def worker(
    idx: int,
    obj: base.Objetivo,
    fecha_juego: date,
    apertura: datetime | None,
    ahora: bool,
    dry_run: bool,
    capturar: bool,
    tz_nombre: str,
) -> None:
    tz = ZoneInfo(tz_nombre)
    documento = os.environ["BIGUA_DOCUMENTO"]
    password = os.environ["BIGUA_PASSWORD"]
    tipo_doc = os.getenv("BIGUA_TIPO_DOC", "1")

    with sync_playwright() as pw:
        navegador = pw.chromium.launch(headless=True)
        ctx = navegador.new_context(
            locale="es-UY",
            timezone_id=tz_nombre,
            viewport={"width": 1400, "height": 900},
        )
        page = ctx.new_page()
        page.set_default_timeout(20_000)

        def aceptar_dialog(dialog):
            log_worker(idx, f"Dialog del sitio: {dialog.type} — {dialog.message!r}")
            dialog.accept()

        page.on("dialog", aceptar_dialog)

        try:
            log_worker(idx, "login...")
            base.login(page, documento, password, tipo_doc)
            log_worker(idx, "login OK")

            if apertura:
                base.esperar_apertura(apertura - timedelta(seconds=15), tz)
                base.asegurar_sesion(page, documento, password, tipo_doc)
                base.esperar_apertura(apertura, tz)
                log_worker(idx, ">>> APERTURA <<<")
            else:
                page.goto(base.URL_TENIS, wait_until="domcontentloaded")

            limite = time.monotonic() + (60 if ahora else 150)
            elegido = None
            vuelta = 0
            while time.monotonic() < limite:
                if LOCK.exists():
                    log_worker(idx, "otro worker ya reservo, me bajo")
                    return
                vuelta += 1
                try:
                    page.goto(base.URL_TENIS, wait_until="domcontentloaded", timeout=20_000)
                    if not base.sesion_activa(page):
                        base.login(page, documento, password, tipo_doc)
                        page.goto(base.URL_TENIS, wait_until="domcontentloaded", timeout=20_000)
                    slots = base.leer_slots(page)
                except Exception as exc:
                    log_worker(idx, f"vuelta {vuelta}: fallo ({exc}), reintento")
                    time.sleep(1.0)
                    continue
                if vuelta == 1 or slots:
                    log_worker(idx, f"vuelta {vuelta}: {len(slots)} slots")
                elegido = base.elegir_slot(slots, obj)
                if elegido:
                    break
                time.sleep(1.0)

            if not elegido:
                log_worker(idx, "no encontro nada en su ventana")
                return

            if not reclamar_ticket(idx):
                log_worker(idx, f"encontro {elegido['texto']} pero perdio la carrera por el ticket")
                return

            log_worker(idx, f"GANE — reservando {elegido['texto']}")

            if dry_run:
                RESULTADO.write_text(json.dumps({
                    "worker": idx, "dry_run": True, "elegido": elegido,
                }), encoding="utf-8")
                return

            if capturar:
                base.volcar_modal(page, f"w{idx}-antes")

            try:
                page.wait_for_load_state("networkidle", timeout=5_000)
            except Exception:
                pass
            page.wait_for_timeout(500)

            page.click(f"#{elegido['btn']}")
            estado = base.completar_invitacion(page, obj.ci_invitado, capturar=capturar)
            log_worker(idx, f"Estado tras RESERVAR: {estado}")

            page.wait_for_timeout(1500)
            if capturar:
                base.volcar_modal(page, f"w{idx}-resultado")

            page.goto(base.URL_AGENDA, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            if capturar:
                base.volcar_modal(page, f"w{idx}-agenda")
            agenda = page.inner_text("body")
            ok = elegido["cancha"].lower() in agenda.lower()

            RESULTADO.write_text(json.dumps({
                "worker": idx, "ok": ok, "elegido": elegido,
                "ci_invitado": obj.ci_invitado,
            }), encoding="utf-8")
            log_worker(idx, f"listo, ok={ok}")

        except Exception as exc:
            log_worker(idx, f"ERROR: {exc}")
            traceback.print_exc()
            try:
                if capturar:
                    base.volcar_modal(page, f"w{idx}-error")
            except Exception:
                pass
        finally:
            ctx.close()
            navegador.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ahora", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--capturar", action="store_true")
    ap.add_argument("--fecha", help="YYYY-MM-DD, por defecto manana")
    args = ap.parse_args()

    cfg = base.cargar_config()
    tz = ZoneInfo(cfg.get("tz", "America/Montevideo"))
    ahora_dt = datetime.now(tz)

    if not cfg.get("activo", True):
        print("El bot esta DESACTIVADO en config.json. Nada que hacer.")
        return 0

    if not args.dry_run:
        base.desactivar(cfg)

    fecha_juego = date.fromisoformat(args.fecha) if args.fecha else (ahora_dt.date() + timedelta(days=1))

    obj = base.resolver_objetivo(cfg, fecha_juego)
    if obj is None:
        print(f"No hay nada configurado para {fecha_juego}.")
        return 0

    if base.ya_reservado(fecha_juego):
        print(f"Ya hay una reserva confirmada para {fecha_juego}. No hago nada.")
        return 0

    n = int(cfg.get("paralelo_n", 3))
    apertura = base.momento_apertura(cfg, fecha_juego) if not args.ahora else None

    print(f"[paralelo] Objetivo: {obj} — {n} workers")
    LOCK.unlink(missing_ok=True)
    RESULTADO.unlink(missing_ok=True)
    for i in range(n):
        (RAIZ / f"paralelo_w{i}.log").unlink(missing_ok=True)

    procesos = [
        mp.Process(
            target=worker,
            args=(i, obj, fecha_juego, apertura, args.ahora, args.dry_run, args.capturar, cfg.get("tz", "America/Montevideo")),
        )
        for i in range(n)
    ]
    for p in procesos:
        p.start()
    for p in procesos:
        p.join()

    if not RESULTADO.exists():
        msg = f"No aparecio ningun horario de la lista {obj.horas} para el {fecha_juego} (con {n} workers en paralelo)."
        print(msg)
        base.notificar(cfg, "Biguá (paralelo): no se consiguió cancha", msg)
        return 1

    r = json.loads(RESULTADO.read_text(encoding="utf-8"))
    elegido = r["elegido"]

    if r.get("dry_run"):
        print(f"DRY RUN — worker {r['worker']} habria reservado: {elegido['texto']}")
        return 0

    ok = r.get("ok", False)
    detalle = (
        f"Cancha: {elegido['cancha']}\n"
        f"Cuando: {elegido['texto']}\n"
        f"Invitado (C.I.): {r.get('ci_invitado') or '—'}\n"
        f"Worker ganador: {r['worker']} (de {n} en paralelo)\n"
        f"Verificado en Mi Agenda: {'si' if ok else 'NO'}\n\n"
        "Recorda que el socio invitado tiene 30 minutos para aceptar "
        "la invitacion desde su agenda, o la reserva se cae."
    )
    print(detalle)
    if ok:
        base.marcar_reservado(fecha_juego)
    base.notificar(
        cfg,
        f"Biguá (paralelo): {'cancha reservada' if ok else 'reserva sin confirmar'} — {elegido['texto']}",
        detalle,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
