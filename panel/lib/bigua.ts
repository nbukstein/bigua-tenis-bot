// En vez de reimplementar el login + listado de canchas de Bigua en
// TypeScript (esa version tenia un bug sin resolver: devolvia 0 resultados
// sin razon clara), le pedimos el resultado al servidor de busqueda que
// corre en el VPS de Uruguay — la MISMA secuencia que usa reservar_api.py,
// probada y funcionando.

export async function buscarCanchasEnVPS(fecha: string | null) {
  const url = process.env.VPS_BUSQUEDA_URL; // ej: http://<ip>:8811
  const secreto = process.env.VPS_BUSQUEDA_SECRETO;
  if (!url || !secreto) {
    throw new Error("Faltan VPS_BUSQUEDA_URL/VPS_BUSQUEDA_SECRETO en las variables de entorno de Vercel.");
  }

  const qs = fecha ? `?fecha=${encodeURIComponent(fecha)}` : "";
  const r = await fetch(`${url}/buscar${qs}`, {
    headers: { "X-Secret": secreto },
    signal: AbortSignal.timeout(20_000),
  });

  const texto = await r.text();
  let j: any;
  try {
    j = JSON.parse(texto);
  } catch {
    throw new Error(`Respuesta no-JSON del VPS (${r.status}): ${texto.slice(0, 300)}`);
  }
  if (!r.ok) throw new Error(j.error || `El VPS respondio ${r.status}`);
  return j.canchas as any[];
}
