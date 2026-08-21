import { NextResponse } from "next/server";
import crypto from "crypto";
import { crearToken, NOMBRE_COOKIE, MAX_AGE } from "@/lib/auth";

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  const { password } = await req.json().catch(() => ({ password: "" }));
  const real = process.env.PANEL_PASSWORD || "";

  const a = Buffer.from(String(password));
  const b = Buffer.from(real);
  const ok = a.length === b.length && crypto.timingSafeEqual(a, b);

  // Freno simple contra fuerza bruta.
  await new Promise((r) => setTimeout(r, 400));

  if (!ok) return NextResponse.json({ error: "Contraseña incorrecta" }, { status: 401 });

  const res = NextResponse.json({ ok: true });
  res.cookies.set(NOMBRE_COOKIE, crearToken(), {
    httpOnly: true,
    secure: true,
    sameSite: "lax",
    path: "/",
    maxAge: MAX_AGE,
  });
  return res;
}

export async function DELETE() {
  const res = NextResponse.json({ ok: true });
  res.cookies.delete(NOMBRE_COOKIE);
  return res;
}
