const REPO = process.env.GH_REPO!;          // "nbukstein/bigua-tenis-bot"
const TOKEN = process.env.GH_TOKEN!;        // PAT fine-grained: Contents RW + Actions RW
const RAMA = process.env.GH_BRANCH || "main";
const RUTA_CONFIG = "config.json";
const WORKFLOW = "reservar.yml";

const API = "https://api.github.com";

function headers() {
  return {
    Authorization: `Bearer ${TOKEN}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "Content-Type": "application/json",
  };
}

export type Config = {
  activo: boolean;
  tz: string;
  apertura: string;
  ci_invitado_default: string;
  canchas_preferidas: string[];
  notificar_a: string[];
  default_semanal: Record<string, { horas: number[] }>;
  override: Record<string, { horas: number[]; ci_invitado?: string; canchas?: string[] }>;
  [k: string]: unknown;
};

export async function leerConfig(): Promise<{ config: Config; sha: string }> {
  const r = await fetch(
    `${API}/repos/${REPO}/contents/${RUTA_CONFIG}?ref=${RAMA}`,
    { headers: headers(), cache: "no-store" }
  );
  if (!r.ok) throw new Error(`GitHub ${r.status}: ${await r.text()}`);
  const j = await r.json();
  const texto = Buffer.from(j.content, "base64").toString("utf-8");
  return { config: JSON.parse(texto) as Config, sha: j.sha };
}

export async function escribirConfig(config: Config, sha: string, mensaje: string) {
  const contenido = Buffer.from(JSON.stringify(config, null, 2) + "\n").toString("base64");
  const r = await fetch(`${API}/repos/${REPO}/contents/${RUTA_CONFIG}`, {
    method: "PUT",
    headers: headers(),
    body: JSON.stringify({ message: mensaje, content: contenido, sha, branch: RAMA }),
  });
  if (!r.ok) throw new Error(`GitHub ${r.status}: ${await r.text()}`);
  return r.json();
}

export async function dispararWorkflow(inputs: Record<string, string>) {
  const r = await fetch(
    `${API}/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
    { method: "POST", headers: headers(), body: JSON.stringify({ ref: RAMA, inputs }) }
  );
  if (!r.ok) throw new Error(`GitHub ${r.status}: ${await r.text()}`);
}

export async function ultimasCorridas(n = 5) {
  const r = await fetch(
    `${API}/repos/${REPO}/actions/workflows/${WORKFLOW}/runs?per_page=${n}`,
    { headers: headers(), cache: "no-store" }
  );
  if (!r.ok) return [];
  const j = await r.json();
  return (j.workflow_runs || []).map((w: any) => ({
    id: w.id,
    estado: w.status,
    resultado: w.conclusion,
    cuando: w.created_at,
    url: w.html_url,
  }));
}
