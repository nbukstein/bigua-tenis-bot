import { NextResponse } from "next/server";
import { autenticado } from "@/lib/auth";
import { loginBigua, listarClases } from "@/lib/bigua";

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  if (!autenticado()) return NextResponse.json({ error: "no-auth" }, { status: 401 });

  const { searchParams } = new URL(req.url);
  const fecha = searchParams.get("fecha");

  try {
    const documento = process.env.BIGUA_DOCUMENTO;
    const password = process.env.BIGUA_PASSWORD;
    const tipoDoc = process.env.BIGUA_TIPO_DOC || "1";

    if (!documento || !password) {
      return NextResponse.json(
        { error: "Faltan BIGUA_DOCUMENTO/BIGUA_PASSWORD en las variables de entorno de Vercel (revisa que esten en Production y redeployea)." },
        { status: 500 }
      );
    }

    const { token, userGuid, cookies, deviceId } = await loginBigua(documento, password, tipoDoc);
    const crudo = await listarClases(token, userGuid, cookies, deviceId);
    const canchas = fecha ? crudo.filter((c: any) => c.ClaseFecha === fecha) : crudo;

    return NextResponse.json({ canchas });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}
