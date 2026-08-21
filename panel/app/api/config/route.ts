import { NextResponse } from "next/server";
import { autenticado } from "@/lib/auth";
import { leerConfig, escribirConfig, ultimasCorridas, type Config } from "@/lib/gh";

export const dynamic = "force-dynamic";

export async function GET() {
  if (!autenticado()) return NextResponse.json({ error: "no-auth" }, { status: 401 });
  try {
    const { config } = await leerConfig();
    const corridas = await ultimasCorridas(5);
    return NextResponse.json({ config, corridas });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}

export async function PATCH(req: Request) {
  if (!autenticado()) return NextResponse.json({ error: "no-auth" }, { status: 401 });

  const cambio = await req.json();
  try {
    const { config, sha } = await leerConfig();
    const nuevo: Config = { ...config };
    let mensaje = "panel: cambio de configuracion";

    if (typeof cambio.activo === "boolean") {
      nuevo.activo = cambio.activo;
      mensaje = `panel: bot ${cambio.activo ? "activado" : "desactivado"}`;
    }

    if (cambio.override) {
      const { fecha, horas, ci_invitado } = cambio.override;
      nuevo.override = { ...(nuevo.override || {}) };
      if (!horas || horas.length === 0) {
        delete nuevo.override[fecha];
        mensaje = `panel: quitar override del ${fecha}`;
      } else {
        nuevo.override[fecha] = {
          horas,
          ...(ci_invitado ? { ci_invitado } : {}),
        };
        mensaje = `panel: ${fecha} -> ${horas.join(", ")}h`;
      }
    }

    if (cambio.ci_invitado_default !== undefined) {
      nuevo.ci_invitado_default = String(cambio.ci_invitado_default);
      mensaje = "panel: cambio de C.I. por defecto";
    }

    // Limpieza: sacamos overrides de fechas ya pasadas.
    const hoy = new Date().toISOString().slice(0, 10);
    for (const f of Object.keys(nuevo.override || {})) {
      if (f < hoy) delete nuevo.override[f];
    }

    await escribirConfig(nuevo, sha, mensaje);
    return NextResponse.json({ ok: true, config: nuevo });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}
