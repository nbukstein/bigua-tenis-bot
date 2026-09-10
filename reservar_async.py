#!/usr/bin/env python3
"""
Variante EXPERIMENTAL A: paralelismo real dentro de UN solo proceso, usando
la API async de Playwright (asyncio.gather) en vez de varios procesos del
sistema operativo como reservar_paralelo.py.

Aislado a proposito: reusa de reservar.py solo lo que es Python puro (no
toca la pagina) — cargar_config, resolver_objetivo, elegir_slot, Objetivo,
momento_apertura, notificar, desactivar, etc. Todo lo que SI toca la pagina
(login, leer slots, completar el formulario) esta reimplementado con await,
porque las APIs sync y async de Playwright no son intercambiables.

Uso: igual que reservar.py.
    python reservar_async.py --ahora --dry-run --capturar --fecha ...
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
import traceback
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

import reservar as base  # solo funciones puras (no tocan Page)

RAIZ = base.RAIZ
ARTEFACTOS = base.ARTEFACTOS


def log_worker(idx: int, msg: str) -> None:
    linea = f"[a{idx} {datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}"
    print(linea, flush=True)
    with (RAIZ / f"async_w{idx}.log").open("a", encoding="utf-8") as fh:
        fh.write(linea + "\n")


def sesion_activa(page: Page) -> bool:
    return "ingresosocios" not in (page.url or "")


async def login(idx: int, page: Page, documento: str, password: str, tipo_doc: str) -> None:
    log_worker(idx, "login...")
    await page.goto(base.URL_LOGIN, wait_until="domcontentloaded")
    await page.wait_for_selector("#vUSUARIODOCUMENTONROSTR", timeout=20_000)
    await page.select_option("#vUSUARIODOCUMENTOTIPO", tipo_doc)
    await page.fill("#vUSUARIODOCUMENTONROSTR", documento)
    await page.fill("#vUSERPASSWORD", password)
    await page.click("#BTNENTER")
    try:
        await page.wait_for_url(re.compile(r"wpclases|wpagenda|wpcanchas"), timeout=25_000)
    except PWTimeout:
        raise RuntimeError("El login no redirigio (revisa documento/contrasena).")
    log_worker(idx, "login OK")


async def leer_slots(page: Page) -> list[dict]:
    crudo = await page.evaluate(
        """() => [...document.querySelectorAll('input[id^=BTNRESERVARCLASE]')].map(b => {
             const suf = b.id.split('_').pop();
             const fila = document.getElementById('ExtrafreestylegridContainerRow_' + suf);
             return { suf, texto: fila ? fila.innerText : '', btn: b.id };
           })"""
    )
    slots = []
    for item in crudo:
        texto = " ".join((item.get("texto") or "").split())
        m = base.PARSEO_SLOT.search(texto)
        if not m:
            continue
        dd, mo, yy, h1, m1, h2, m2 = (int(x) for x in m.groups())
        cancha = ""
        mc = re.search(r"(Cancha\s*\d+)", texto, re.I)
        if mc:
            cancha = mc.group(1)
        slots.append({
            "btn": item["btn"], "cancha": cancha,
            "fecha": date(2000 + yy, mo, dd), "hora": h1, "texto": texto,
        })
    return slots


async def volcar_modal(idx: int, page: Page, etiqueta: str) -> None:
    ARTEFACTOS.mkdir(exist_ok=True)
    sello = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    await page.screenshot(path=str(ARTEFACTOS / f"a{idx}-{etiqueta}-{sello}.png"), full_page=True)
    contenido = await page.content()
    (ARTEFACTOS / f"a{idx}-{etiqueta}-{sello}.html").write_text(contenido, encoding="utf-8")


async def completar_invitacion(idx: int, page: Page, ci: str, capturar: bool) -> str:
    # Mismos selectores reales que reservar.py (ver ese archivo para el
    # detalle de como se obtuvieron). Duplicado a proposito, no importado:
    # ver docstring del modulo.
    SEL_TIPO_DOC = "select[id^=vUSUARIODOCUMENTOTIPO]:visible"
    SEL_NRO_DOC = "input[id^=vUSUARIODOCUMENTONRO]:visible"
    SEL_CONFIRMAR = "input[id^=BTNBTNVALIDAR]:visible"
    SELECTORES_DOCUMENTO = (
        f"{SEL_NRO_DOC}, "
        "input[id*=DOCUMENTO]:visible, input[id*=CEDULA]:visible, "
        "input[id*=INVITADO]:visible, input[placeholder*='ocumento']:visible, "
        "input[placeholder*='dula']:visible"
    )

    try:
        await page.wait_for_selector(SELECTORES_DOCUMENTO, timeout=30_000, state="visible")
    except PWTimeout:
        if capturar:
            await volcar_modal(idx, page, "post-reservar")
        return "sin-modal"

    if capturar:
        await volcar_modal(idx, page, "post-reservar")

    if not ci:
        raise RuntimeError("Falta la C.I. del invitado (ci_invitado_default vacio).")

    tipo_doc_sel = page.locator(SEL_TIPO_DOC).first
    if await tipo_doc_sel.count():
        await tipo_doc_sel.select_option("1")  # C.I.

    campo = page.locator(SELECTORES_DOCUMENTO).first
    await campo.fill(ci)

    if capturar:
        await volcar_modal(idx, page, "modal-completado")

    for sel in [
        SEL_CONFIRMAR,
        "input[type=submit]:visible",
        "input[id*=CONFIRM]:visible",
        "button:has-text('CONFIRMAR')",
        "button:has-text('RESERVAR')",
        "input[id*=ACEPTAR]:visible",
    ]:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                await loc.click()
                await page.wait_for_timeout(1500)
                if capturar:
                    await volcar_modal(idx, page, "post-confirmar")
                return "confirmado"
        except Exception:
            continue
    return "sin-boton-confirmar"


async def esperar_apertura(cuando: datetime, tz: ZoneInfo) -> None:
    while True:
        falta = (cuando - datetime.now(tz)).total_seconds()
        if falta <= 0:
            return
        await asyncio.sleep(0.05 if falta <= 2 else min(falta - 1.5, 60))


def reclamar(idx: int, ganador: dict) -> bool:
    # Sin lock: asyncio es cooperativo de un solo hilo, no hay await entre
    # el chequeo y el set, asi que no hay forma de que otra tarea se cuele.
    if ganador["idx"] is None:
        ganador["idx"] = idx
        return True
    return False


async def worker(
    idx: int, obj: base.Objetivo, apertura: datetime | None, ahora: bool,
    dry_run: bool, capturar: bool, tz: ZoneInfo, navegador,
    ganador: dict, resultado: dict,
) -> None:
    documento = os.environ["BIGUA_DOCUMENTO"]
    password = os.environ["BIGUA_PASSWORD"]
    tipo_doc = os.getenv("BIGUA_TIPO_DOC", "1")

    ctx = await navegador.new_context(
        locale="es-UY", timezone_id=str(tz), viewport={"width": 1400, "height": 900},
    )
    page = await ctx.new_page()
    page.set_default_timeout(20_000)

    def on_dialog(dialog):
        log_worker(idx, f"Dialog del sitio: {dialog.type} — {dialog.message!r}")
        asyncio.create_task(dialog.accept())

    page.on("dialog", on_dialog)

    try:
        await login(idx, page, documento, password, tipo_doc)

        if apertura:
            await esperar_apertura(apertura - timedelta(seconds=15), tz)
            await page.goto(base.URL_TENIS, wait_until="domcontentloaded")
            await esperar_apertura(apertura, tz)
            log_worker(idx, ">>> APERTURA <<<")
        else:
            await page.goto(base.URL_TENIS, wait_until="domcontentloaded")

        limite = time.monotonic() + (60 if ahora else 150)
        elegido = None
        vuelta = 0
        while time.monotonic() < limite:
            if ganador["idx"] is not None:
                log_worker(idx, "otro worker ya reservo, me bajo")
                return
            vuelta += 1
            try:
                await page.goto(base.URL_TENIS, wait_until="domcontentloaded", timeout=20_000)
                if not sesion_activa(page):
                    await login(idx, page, documento, password, tipo_doc)
                    await page.goto(base.URL_TENIS, wait_until="domcontentloaded", timeout=20_000)
                slots = await leer_slots(page)
            except Exception as exc:
                log_worker(idx, f"vuelta {vuelta}: fallo ({exc}), reintento")
                await asyncio.sleep(0.2)
                continue
            if vuelta == 1 or slots:
                log_worker(idx, f"vuelta {vuelta}: {len(slots)} slots")
            elegido = base.elegir_slot(slots, obj)
            if elegido:
                break
            await asyncio.sleep(0.2)

        if not elegido:
            log_worker(idx, "no encontro nada en su ventana")
            return

        if not reclamar(idx, ganador):
            log_worker(idx, f"encontro {elegido['texto']} pero perdio la carrera")
            return

        log_worker(idx, f"GANE — reservando {elegido['texto']}")

        if dry_run:
            resultado["data"] = {"worker": idx, "dry_run": True, "elegido": elegido}
            return

        if capturar:
            await volcar_modal(idx, page, "antes")

        try:
            await page.wait_for_load_state("networkidle", timeout=5_000)
        except Exception:
            pass
        await page.wait_for_timeout(500)

        await page.click(f"#{elegido['btn']}")
        estado = await completar_invitacion(idx, page, obj.ci_invitado, capturar)
        log_worker(idx, f"Estado tras RESERVAR: {estado}")

        await page.wait_for_timeout(1500)
        if capturar:
            await volcar_modal(idx, page, "resultado")

        await page.goto(base.URL_AGENDA, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        if capturar:
            await volcar_modal(idx, page, "agenda")
        agenda = await page.inner_text("body")
        ok = elegido["cancha"].lower() in agenda.lower()

        resultado["data"] = {
            "worker": idx, "ok": ok, "elegido": elegido, "ci_invitado": obj.ci_invitado,
        }
        log_worker(idx, f"listo, ok={ok}")

    except Exception as exc:
        log_worker(idx, f"ERROR: {exc}")
        traceback.print_exc()
        try:
            if capturar:
                await volcar_modal(idx, page, "error")
        except Exception:
            pass
    finally:
        await ctx.close()


async def run() -> int:
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

    print(f"[async] Objetivo: {obj} — {n} workers")
    for i in range(n):
        (RAIZ / f"async_w{i}.log").unlink(missing_ok=True)

    ganador = {"idx": None}
    resultado = {"data": None}

    async with async_playwright() as pw:
        navegador = await pw.chromium.launch(headless=True)
        tareas = [
            worker(i, obj, apertura, args.ahora, args.dry_run, args.capturar, tz, navegador, ganador, resultado)
            for i in range(n)
        ]
        await asyncio.gather(*tareas, return_exceptions=True)
        await navegador.close()

    if resultado["data"] is None:
        msg = f"No aparecio ningun horario de la lista {obj.horas} para el {fecha_juego} (async, {n} workers)."
        print(msg)
        base.notificar(cfg, "Biguá (async): no se consiguió cancha", msg)
        return 1

    r = resultado["data"]
    elegido = r["elegido"]

    if r.get("dry_run"):
        print(f"DRY RUN — worker {r['worker']} habria reservado: {elegido['texto']}")
        return 0

    ok = r.get("ok", False)
    detalle = (
        f"Cancha: {elegido['cancha']}\n"
        f"Cuando: {elegido['texto']}\n"
        f"Invitado (C.I.): {r.get('ci_invitado') or '—'}\n"
        f"Worker ganador: {r['worker']} (de {n}, async)\n"
        f"Verificado en Mi Agenda: {'si' if ok else 'NO'}\n\n"
        "Recorda que el socio invitado tiene 30 minutos para aceptar "
        "la invitacion desde su agenda, o la reserva se cae."
    )
    print(detalle)
    if ok:
        base.marcar_reservado(fecha_juego)
    base.notificar(
        cfg,
        f"Biguá (async): {'cancha reservada' if ok else 'reserva sin confirmar'} — {elegido['texto']}",
        detalle,
    )
    return 0 if ok else 1


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
