#!/usr/bin/env python3
"""
Servidor HTTP minimo (solo libreria estandar, sin dependencias nuevas) que
expone la busqueda de canchas disponibles para que el panel de Vercel la
pida corriendo la MISMA secuencia que reservar_api.py (login real +
listado), en vez de reimplementarla en TypeScript (esa version tenia un bug
sin resolver: devolvia 0 resultados sin razon clara).

Nunca reserva nada — solo hace login y lista, se corta antes de
PreReservarClase/ReservarClaseTenis.

Protegido por un secreto compartido en el header X-Secret (variable de
entorno BUSQUEDA_SECRETO). Pensado para correr como servicio systemd,
siempre arriba, en el VPS de Uruguay.

Uso: BUSQUEDA_SECRETO=... .venv/bin/python scripts/servidor_busqueda.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

import reservar as base
import reservar_api as api

SECRETO = os.environ["BUSQUEDA_SECRETO"]
PUERTO = int(os.getenv("BUSQUEDA_PUERTO", "8811"))


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, payload: dict) -> None:
        cuerpo = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self) -> None:
        if self.headers.get("X-Secret") != SECRETO:
            self._json(403, {"error": "secreto invalido"})
            return

        partes = urlparse(self.path)
        if partes.path != "/buscar":
            self._json(404, {"error": "not found"})
            return

        qs = parse_qs(partes.query)
        fecha_str = (qs.get("fecha") or [None])[0]

        try:
            cfg = base.cargar_config()
            tz = ZoneInfo(cfg.get("tz", "America/Montevideo"))
            documento = os.environ["BIGUA_DOCUMENTO"]
            password = os.environ["BIGUA_PASSWORD"]
            tipo_doc = os.getenv("BIGUA_TIPO_DOC", "1")

            cliente = api.ClienteBigua()
            cliente.login(documento, password, tipo_doc)
            slots = api._a_slots(cliente.listar_clases(tz))

            if fecha_str:
                objetivo = date.fromisoformat(fecha_str)
                slots = [s for s in slots if s["fecha"] == objetivo]

            self._json(200, {"canchas": slots})
        except Exception as exc:
            self._json(500, {"error": str(exc)})

    def log_message(self, format: str, *args) -> None:
        pass  # silencioso — no ensuciar el journal por cada request


if __name__ == "__main__":
    servidor = ThreadingHTTPServer(("0.0.0.0", PUERTO), Handler)
    print(f"servidor_busqueda escuchando en :{PUERTO}", flush=True)
    servidor.serve_forever()
