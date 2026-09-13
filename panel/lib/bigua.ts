// Cliente minimo del backend REST que usa la app movil de Bigua (el mismo
// que usa reservar_api.py del lado del bot). Solo lectura (listar clases) —
// no reserva nada desde el panel.

const BASE = "https://bigua.uy/biguasociossd";
const CLIENT_ID = "21139fb35fa04ac7b2a0293b6f6c8cb3";

function headersBase() {
  return {
    Host: "bigua.uy",
    "User-Agent": "SD_Bigua/2.1153 CFNetwork/3860.700.1 Darwin/25.6.0",
    GXAppVersionName: "2.1257",
    DeviceOSVersion: "26.6.1",
    DevicePlatform: "iPhone",
    "GeneXus-Theme": "SmartDevicesPlusIOS",
    DeviceType: "0",
    DeviceOSName: "iPhone",
    GXApplicationId: "uy.com.biguasocios",
    GXAppVersionCode: "2.1257",
    "GeneXus-Language": "Spanish",
    Connection: "keep-alive",
    "Accept-Language": "es-UY, es",
    "GeneXus-Agent": "SmartDevice Application",
    Accept: "*/*",
    GxTZOffset: "America/Montevideo",
  };
}

function cookiesDeRespuesta(r: Response): string {
  const getSetCookie = (r.headers as any).getSetCookie;
  if (typeof getSetCookie !== "function") {
    // Runtime viejo sin getSetCookie(): no hay forma confiable de separar
    // varios Set-Cookie de un solo header ya unido por comas. Mejor no
    // adivinar mal (romperiamos el valor de la cookie) que devolver algo.
    console.warn("bigua.ts: Headers.getSetCookie() no disponible en este runtime");
    return "";
  }
  const crudas: string[] = getSetCookie.call(r.headers);
  return crudas.map((c) => c.split(";")[0]).filter(Boolean).join("; ");
}

// Uruguay no tiene horario de verano: offset fijo -03.
function ahoraMontevideo(): string {
  return new Date(Date.now() - 3 * 3600_000).toISOString().slice(0, 19);
}

export async function loginBigua(documento: string, password: string, tipoDoc = "1") {
  const deviceId = crypto.randomUUID();
  const gxClientId = crypto.randomUUID();
  const headers = {
    ...headersBase(),
    "Content-Type": "application/x-www-form-urlencoded",
    DeviceId: deviceId,
    redirect_urlscheme: `gxgam${CLIENT_ID}`,
    Cookie: `GX_CLIENT_ID=${gxClientId}`,
  };
  const body = new URLSearchParams({
    client_id: CLIENT_ID,
    grant_type: "password",
    username: `${tipoDoc}${documento}`,
    password,
    scope: "FullControl",
  });
  const r = await fetch(`${BASE}/oauth/access_token`, { method: "POST", headers, body: body.toString() });
  const cookiesLogin = cookiesDeRespuesta(r);
  const j = await r.json();
  if (!j.access_token) throw new Error(`Login fallo: ${JSON.stringify(j)}`);
  return {
    token: j.access_token as string,
    userGuid: j.user_guid as string,
    cookies: [`GX_CLIENT_ID=${gxClientId}`, cookiesLogin].filter(Boolean).join("; "),
    deviceId,
  };
}

export async function listarClases(token: string, userGuid: string, cookies: string, deviceId: string) {
  const qs = new URLSearchParams({
    Fechahoraactual: ahoraMontevideo(),
    Orderedby: "0",
    Reservahabilitada: "true",
    Usuarioessocio: "true",
    Usuarioguid: userGuid,
    start: "0",
    count: "100",
    gxid: "2",
  });
  const headers = {
    ...headersBase(),
    "Content-Type": "application/json",
    DeviceId: deviceId,
    Authorization: `Bearer ${token}`,
    Cookie: cookies,
  };
  const r = await fetch(`${BASE}/rest/SD_ClasesLibres_Level_Detail_GridClases?${qs}`, { headers });
  const texto = await r.text();
  let j: unknown;
  try {
    j = JSON.parse(texto);
  } catch {
    throw new Error(`Respuesta no-JSON de SD_ClasesLibres (${r.status}): ${texto.slice(0, 300)}`);
  }
  if (!Array.isArray(j)) {
    throw new Error(`SD_ClasesLibres no devolvio una lista (${r.status}): ${JSON.stringify(j).slice(0, 300)}`);
  }
  return j;
}
