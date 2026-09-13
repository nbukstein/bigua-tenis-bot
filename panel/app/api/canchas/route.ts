import { NextResponse } from "next/server";
import { autenticado } from "@/lib/auth";
import { buscarCanchasEnVPS } from "@/lib/bigua";

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  if (!autenticado()) return NextResponse.json({ error: "no-auth" }, { status: 401 });

  const { searchParams } = new URL(req.url);
  const fecha = searchParams.get("fecha");

  try {
    const canchas = await buscarCanchasEnVPS(fecha);
    return NextResponse.json({ canchas });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}
