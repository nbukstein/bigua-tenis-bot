import { NextResponse } from "next/server";
import { autenticado } from "@/lib/auth";
import { dispararWorkflow } from "@/lib/gh";

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  if (!autenticado()) return NextResponse.json({ error: "no-auth" }, { status: 401 });

  const { fecha, ahora, dry_run } = await req.json().catch(() => ({}));
  try {
    await dispararWorkflow({
      ...(fecha ? { fecha } : {}),
      ahora: ahora ? "true" : "false",
      dry_run: dry_run ? "true" : "false",
      capturar: "false",
    });
    return NextResponse.json({ ok: true });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}
