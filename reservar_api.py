#!/usr/bin/env python3
"""
Variante EXPERIMENTAL: en vez de manejar un navegador, habla directo con el
backend REST que usa la app movil de Bigua (descubierto con un proxy MITM
sobre la app de iOS). Sin Playwright, sin Chromium, solo HTTP con la
libreria estandar de Python.

Aislado a proposito de reservar.py: reusa solo lo que es Python puro
(config, resolver_objetivo, elegir_slot, Objetivo, momento_apertura,
esperar_apertura, notificar, desactivar) — nada que toque un navegador,
porque este motor no tiene navegador.

Flujo (reconstruido a partir de trafico real capturado, incluyendo una
reserva real confirmada con ReservaOk=true):
  1. POST oauth/access_token          -> access_token, user_guid
  2. GET  SD_ClasesLibres_Level_Detail_GridClases -> lista de clases/horarios
  3. POST PreReservarClase            -> reserva el slot temporalmente
  4. GET  SD_SingleDobles_Level_Detail -> "plantilla" del formulario
  5. POST ReservarClaseTenis          -> la reserva real (devuelve ReservaOk)

Uso: igual que reservar.py.
    python reservar_api.py --ahora --dry-run --capturar --fecha ...
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import reservar as base  # solo funciones puras (no tocan un navegador)

RAIZ = base.RAIZ
BASE = "https://bigua.uy/biguasociossd"
CLIENT_ID = "21139fb35fa04ac7b2a0293b6f6c8cb3"
MSGAVISO = "Reserva realizada. Le recordamos que su cuota no está al día."

ULTIMA_CORRIDA = RAIZ / "ultima_corrida_api.log"
_log_fh = None


def log(msg: str) -> None:
    linea = f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}"
    print(linea, flush=True)
    if _log_fh:
        _log_fh.write(linea + "\n")
        _log_fh.flush()


# --------------------------------------------------------------------------
# Cliente HTTP minimo (stdlib, sin dependencias nuevas)
# --------------------------------------------------------------------------

def _headers_base() -> dict:
    return {
        "Host": "bigua.uy",
        "User-Agent": "SD_Bigua/2.1153 CFNetwork/3860.700.1 Darwin/25.6.0",
        "GXAppVersionName": "2.1257",
        "DeviceOSVersion": "26.6.1",
        "DevicePlatform": "iPhone",
        "GeneXus-Theme": "SmartDevicesPlusIOS",
        "DeviceType": "0",
        "DeviceOSName": "iPhone",
        "GXApplicationId": "uy.com.biguasocios",
        "GXAppVersionCode": "2.1257",
        "GeneXus-Language": "Spanish",
        "Connection": "keep-alive",
        "Accept-Language": "es-UY, es",
        "GeneXus-Agent": "SmartDevice Application",
        "Accept": "*/*",
        "GxTZOffset": "America/Montevideo",
    }


def _add_cookie(cj: http.cookiejar.CookieJar, nombre: str, valor: str) -> None:
    cj.set_cookie(http.cookiejar.Cookie(
        version=0, name=nombre, value=valor, port=None, port_specified=False,
        domain="bigua.uy", domain_specified=True, domain_initial_dot=False,
        path="/", path_specified=True, secure=True, expires=None,
        discard=True, comment=None, comment_url=None, rest={},
    ))


class ClienteBigua:
    """Una 'sesion' de la app: cookies + token compartidos entre llamadas."""

    def __init__(self, capturar: bool = False):
        self.cj = http.cookiejar.CookieJar()
        _add_cookie(self.cj, "GX_CLIENT_ID", str(uuid.uuid4()))
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))
        self.device_id = str(uuid.uuid4()).upper()
        self.token = None
        self.user_guid = None
        self.capturar = capturar

    def _llamar(self, method: str, url: str, headers: dict, cuerpo: bytes | None, timeout: float) -> dict:
        req = urllib.request.Request(url, data=cuerpo, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=timeout) as resp:
                texto = resp.read().decode("utf-8")
                status = resp.status
        except urllib.error.HTTPError as exc:
            texto = exc.read().decode("utf-8", errors="replace")
            status = exc.code
        if self.capturar:
            log(f"  <- {status} {url.split('?')[0]}: {texto[:300]}")
        if not texto:
            return {}
        try:
            return json.loads(texto)
        except json.JSONDecodeError:
            raise RuntimeError(f"Respuesta no-JSON de {url} ({status}): {texto[:200]}")

    def login(self, documento: str, password: str, tipo_doc: str) -> None:
        headers = _headers_base() | {
            "Content-Type": "application/x-www-form-urlencoded",
            "DeviceId": self.device_id,
            "redirect_urlscheme": f"gxgam{CLIENT_ID}",
        }
        cuerpo = urllib.parse.urlencode({
            "client_id": CLIENT_ID,
            "grant_type": "password",
            "username": f"{tipo_doc}{documento}",
            "password": password,
            "scope": "FullControl",
        }).encode("utf-8")
        r = self._llamar("POST", f"{BASE}/oauth/access_token", headers, cuerpo, timeout=20)
        if "access_token" not in r:
            raise RuntimeError(f"Login fallo: {r}")
        self.token = r["access_token"]
        self.user_guid = r["user_guid"]

    def _headers_auth(self) -> dict:
        return _headers_base() | {
            "Content-Type": "application/json",
            "DeviceId": self.device_id,
            "Authorization": f"Bearer {self.token}",
        }

    def listar_clases(self, tz: ZoneInfo, timeout: float = 15) -> list[dict]:
        ahora = datetime.now(tz).strftime("%Y-%m-%dT%H:%M:%S")
        qs = urllib.parse.urlencode({
            "Fechahoraactual": ahora, "Orderedby": 0, "Reservahabilitada": "true",
            "Usuarioessocio": "true", "Usuarioguid": self.user_guid,
            "start": 0, "count": 100, "gxid": 2,
        })
        r = self._llamar("GET", f"{BASE}/rest/SD_ClasesLibres_Level_Detail_GridClases?{qs}",
                          self._headers_auth(), None, timeout)
        return r if isinstance(r, list) else []

    def pre_reservar(self, clase_id: int, timeout: float = 15) -> None:
        cuerpo = json.dumps({"ClaseId": clase_id, "UsuarioGUID": self.user_guid}).encode("utf-8")
        self._llamar("POST", f"{BASE}/rest/PreReservarClase", self._headers_auth(), cuerpo, timeout)

    def obtener_plantilla(self, actividad_id: int, clase_fecha: str, clase_id: int, timeout: float = 15) -> dict:
        qs = urllib.parse.urlencode({
            "Actividadid": actividad_id, "Clasefecha": clase_fecha, "Claseid": clase_id,
            "Msgaviso": MSGAVISO, "Usuarioguid": self.user_guid, "gxid": 3,
        })
        return self._llamar("GET", f"{BASE}/rest/SD_SingleDobles_Level_Detail?{qs}",
                             self._headers_auth(), None, timeout)

    def reservar_tenis(self, clase_id: int, clase_fecha: str, actividad_id: int, ci_invitado: str, timeout: float = 20) -> dict:
        cuerpo = json.dumps({
            "ClaseId": clase_id, "ClaseFecha": clase_fecha, "EsSingles": True,
            "UsuarioDocumentoNro2": int(ci_invitado), "UsuarioDocumentoTipo2": 1,
            "UsuarioDocumentoNro3": 0, "UsuarioDocumentoTipo3": 0,
            "UsuarioDocumentoNro4": 0, "UsuarioDocumentoTipo4": 0,
            "UsuarioGUID": self.user_guid, "ActividadId": actividad_id,
        }).encode("utf-8")
        return self._llamar("POST", f"{BASE}/rest/ReservarClaseTenis", self._headers_auth(), cuerpo, timeout)


# --------------------------------------------------------------------------
# Traduccion API -> el mismo formato de slot que usa elegir_slot()
# --------------------------------------------------------------------------

def _a_slots(crudo: list[dict]) -> list[dict]:
    slots = []
    for item in crudo:
        m = base.PARSEO_SLOT.search(item.get("Clasefechahorastr") or "")
        if not m:
            continue
        h1 = int(m.group(4))
        try:
            fecha = date.fromisoformat(item["ClaseFecha"])
        except (KeyError, ValueError):
            continue
        slots.append({
            "claseid": int(item["ClaseId"]),
            "actividadid": int(item["ActividadId"]),
            "cancha": item.get("ClaseNombre", ""),
            "fecha": fecha,
            "hora": h1,
            "texto": f"{item.get('ClaseNombre','')} {item.get('Clasefechahorastr','')} {item.get('Clasecuposstr','')}",
        })
    return slots


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> int:
    global _log_fh
    _log_fh = ULTIMA_CORRIDA.open("w", encoding="utf-8")

    ap = argparse.ArgumentParser()
    ap.add_argument("--ahora", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--capturar", action="store_true", help="loguea cuerpos de request/response")
    ap.add_argument("--fecha", help="YYYY-MM-DD, por defecto manana")
    args = ap.parse_args()

    cfg = base.cargar_config()
    tz = ZoneInfo(cfg.get("tz", "America/Montevideo"))
    ahora_dt = datetime.now(tz)

    if not cfg.get("activo", True):
        log("El bot esta DESACTIVADO en config.json. Nada que hacer.")
        return 0

    if not args.dry_run:
        base.desactivar(cfg)

    fecha_juego = date.fromisoformat(args.fecha) if args.fecha else (ahora_dt.date() + timedelta(days=1))

    obj = base.resolver_objetivo(cfg, fecha_juego)
    if obj is None:
        log(f"No hay nada configurado para {fecha_juego}.")
        return 0

    if base.ya_reservado(fecha_juego):
        log(f"Ya hay una reserva confirmada para {fecha_juego}. No hago nada.")
        return 0

    log(f"[api] Objetivo: {obj}")

    documento = os.environ["BIGUA_DOCUMENTO"]
    password = os.environ["BIGUA_PASSWORD"]
    tipo_doc = os.getenv("BIGUA_TIPO_DOC", "1")

    cliente = ClienteBigua(capturar=args.capturar)

    try:
        log("login...")
        cliente.login(documento, password, tipo_doc)
        log("login OK")

        apertura = base.momento_apertura(cfg, fecha_juego) if not args.ahora else None
        if apertura:
            log(f"Apertura prevista: {apertura.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            base.esperar_apertura(apertura, tz)
            log(">>> APERTURA <<<")

        # En --ahora (prueba manual, sin esperar apertura) no tiene sentido
        # reintentar todo el minuto si viene vacio: no va a aparecer nada de
        # la nada fuera de la apertura real. En la apertura real SI seguimos
        # reintentando toda la ventana, porque los cupos tardan en publicarse.
        MAX_VACIOS_AHORA = 3
        limite = time.monotonic() + (60 if args.ahora else 150)
        elegido = None
        vuelta = 0
        vacios_seguidos = 0
        while time.monotonic() < limite:
            vuelta += 1
            try:
                slots = _a_slots(cliente.listar_clases(tz))
            except Exception as exc:
                log(f"vuelta {vuelta}: fallo ({exc}), reintento")
                time.sleep(0.2)
                continue
            if vuelta == 1 or slots:
                log(f"vuelta {vuelta}: {len(slots)} slots")
            elegido = base.elegir_slot(slots, obj)
            if elegido:
                break
            if not slots:
                vacios_seguidos += 1
                if args.ahora and vacios_seguidos >= MAX_VACIOS_AHORA:
                    log(f"{MAX_VACIOS_AHORA} vueltas vacias seguidas en --ahora, corto.")
                    break
            else:
                vacios_seguidos = 0
            time.sleep(0.2)

        if not elegido:
            msg = f"No aparecio ningun horario de la lista {obj.horas} para el {fecha_juego}."
            log(msg)
            base.notificar(cfg, "Biguá (API): no se consiguió cancha", msg)
            return 1

        log(f"Slot elegido: {elegido['cancha']} a las {elegido['hora']}h — {elegido['texto']}")

        if args.dry_run:
            log("DRY RUN: no se toca la reserva.")
            return 0

        clase_id = elegido["claseid"]
        actividad_id = elegido["actividadid"]
        clase_fecha = elegido["fecha"].isoformat()

        cliente.pre_reservar(clase_id)
        cliente.obtener_plantilla(actividad_id, clase_fecha, clase_id)
        r = cliente.reservar_tenis(clase_id, clase_fecha, actividad_id, obj.ci_invitado)

        ok = bool(r.get("ReservaOk"))
        log(f"Estado tras RESERVAR: {r}")

        detalle = (
            f"Cancha: {elegido['cancha']}\n"
            f"Cuando: {elegido['texto']}\n"
            f"Invitado (C.I.): {obj.ci_invitado or '—'}\n"
            f"ReservaOk: {ok}"
            + (f"\nError: {r.get('MsgError')}" if not ok and r.get("MsgError") else "")
            + "\n\nRecorda que el socio invitado tiene 30 minutos para aceptar "
              "la invitacion desde su agenda, o la reserva se cae."
        )
        log(detalle.replace("\n", " | "))
        if ok:
            base.marcar_reservado(fecha_juego)
        base.notificar(
            cfg,
            f"Biguá (API): {'cancha reservada' if ok else 'reserva sin confirmar'} — {elegido['texto']}",
            detalle,
        )
        return 0 if ok else 1

    except Exception as exc:
        log(f"ERROR: {exc}")
        traceback.print_exc()
        base.notificar(cfg, "Biguá (API): el bot falló", f"{exc}\n\n{traceback.format_exc()}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
