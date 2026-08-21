import crypto from "crypto";
import { cookies } from "next/headers";

const SECRETO = process.env.PANEL_PASSWORD!;
const COOKIE = "bigua_panel";
const DIAS = 30;

function firmar(payload: string) {
  return crypto.createHmac("sha256", SECRETO).update(payload).digest("hex");
}

export function crearToken() {
  const vence = Date.now() + DIAS * 864e5;
  return `${vence}.${firmar(String(vence))}`;
}

export function tokenValido(token?: string) {
  if (!token) return false;
  const [vence, firma] = token.split(".");
  if (!vence || !firma) return false;
  if (Number(vence) < Date.now()) return false;
  const esperado = firmar(vence);
  // comparacion en tiempo constante
  return (
    firma.length === esperado.length &&
    crypto.timingSafeEqual(Buffer.from(firma), Buffer.from(esperado))
  );
}

export function autenticado() {
  return tokenValido(cookies().get(COOKIE)?.value);
}

export const NOMBRE_COOKIE = COOKIE;
export const MAX_AGE = DIAS * 86400;
