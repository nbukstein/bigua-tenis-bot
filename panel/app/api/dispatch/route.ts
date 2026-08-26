import { NextResponse } from "next/server";
import { autenticado } from "@/lib/auth";
import { dispararWorkflow } from "@/lib/gh";

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  if (!autenticado()) return NextResponse.json({ error: "no-auth" }, { status: 401 });

  const { fecha, ahora, dry_run } = await req.json().catch(() => ({}));
  try {
    // Solo mandamos los flags que estan en true. Un "false" como string es
    // truthy en las expresiones de GitHub y terminaria activando el flag.
    await dispararWorkflow({
      ...(fecha ? { fecha } : {}),
      ...(ahora ? { ahora: "true" } : {}),
      ...(dry_run ? { dry_run: "true" } : {}),
    });
    return NextResponse.json({ ok: true });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}
