"use client";

import { useEffect, useState } from "react";

const HORAS = [7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21];
const DIAS = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"];

function manana() {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  return d.toISOString().slice(0, 10);
}

function etiquetaFecha(iso: string) {
  const [y, m, d] = iso.split("-").map(Number);
  const f = new Date(y, m - 1, d);
  return `${DIAS[f.getDay()]} ${d}/${m}`;
}

type Corrida = { id: number; estado: string; resultado: string | null; cuando: string; url: string };

export default function Panel() {
  const [auth, setAuth] = useState<boolean | null>(null);
  const [pass, setPass] = useState("");
  const [cfg, setCfg] = useState<any>(null);
  const [corridas, setCorridas] = useState<Corrida[]>([]);
  const [fecha, setFecha] = useState(manana());
  const [horas, setHoras] = useState<number[]>([]);
  const [ci, setCi] = useState("");
  const [ocupado, setOcupado] = useState(false);
  const [aviso, setAviso] = useState<{ tipo: "ok" | "mal"; txt: string } | null>(null);

  async function cargar() {
    const r = await fetch("/api/config");
    if (r.status === 401) return setAuth(false);
    const j = await r.json();
    setAuth(true);
    setCfg(j.config);
    setCorridas(j.corridas || []);
    setCi(j.config?.ci_invitado_default || "");
    aplicarFecha(fecha, j.config);
  }

  function aplicarFecha(f: string, config = cfg) {
    if (!config) return;
    const ov = config.override?.[f];
    const [y, m, d] = f.split("-").map(Number);
    const dow = (new Date(y, m - 1, d).getDay() + 6) % 7; // 0 = lunes
    const base = ov || config.default_semanal?.[String(dow)];
    setHoras(base?.horas || []);
    if (ov?.ci_invitado) setCi(ov.ci_invitado);
  }

  useEffect(() => { cargar(); }, []);
  useEffect(() => { aplicarFecha(fecha); }, [fecha]);

  function mostrar(tipo: "ok" | "mal", txt: string) {
    setAviso({ tipo, txt });
    setTimeout(() => setAviso(null), 4000);
  }

  async function entrar(e: React.FormEvent) {
    e.preventDefault();
    setOcupado(true);
    const r = await fetch("/api/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pass }),
    });
    setOcupado(false);
    if (r.ok) { setPass(""); cargar(); }
    else mostrar("mal", "Contraseña incorrecta");
  }

  async function patch(cuerpo: any, exito: string) {
    setOcupado(true);
    const r = await fetch("/api/config", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cuerpo),
    });
    const j = await r.json();
    setOcupado(false);
    if (r.ok) { setCfg(j.config); mostrar("ok", exito); }
    else mostrar("mal", j.error || "Falló el guardado");
  }

  function toggleHora(h: number) {
    setHoras((prev) => (prev.includes(h) ? prev.filter((x) => x !== h) : [...prev, h]));
  }

  async function reservarAhora() {
    setOcupado(true);
    const r = await fetch("/api/dispatch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fecha, ahora: true }),
    });
    setOcupado(false);
    if (r.ok) { mostrar("ok", "Runner disparado — mirá el resultado abajo en un minuto"); setTimeout(cargar, 8000); }
    else mostrar("mal", "No se pudo disparar el runner");
  }

  // ---------------------------------------------------------------- login
  if (auth === null) return <div className="wrap"><p style={{ color: "var(--suave)" }}>Cargando…</p></div>;

  if (auth === false)
    return (
      <div className="wrap login">
        <h1 style={{ marginBottom: 16 }}>Cancha Biguá</h1>
        {aviso && <div className={`aviso ${aviso.tipo === "ok" ? "ok" : "mal"}`}>{aviso.txt}</div>}
        <form onSubmit={entrar} className="card">
          <h2>Contraseña</h2>
          <input type="password" value={pass} onChange={(e) => setPass(e.target.value)} autoFocus />
          <div style={{ height: 12 }} />
          <button className="accion" disabled={ocupado || !pass}>Entrar</button>
        </form>
      </div>
    );

  // ---------------------------------------------------------------- panel
  const activo = !!cfg?.activo;
  const hayOverride = !!cfg?.override?.[fecha];

  return (
    <div className="wrap">
      <header>
        <h1>Cancha Biguá</h1>
        <button className="salir" onClick={async () => { await fetch("/api/auth", { method: "DELETE" }); setAuth(false); }}>
          salir
        </button>
      </header>

      {aviso && <div className={`aviso ${aviso.tipo === "ok" ? "ok" : "mal"}`}>{aviso.txt}</div>}

      <div className="card">
        <div className="fila">
          <div>
            <div className={`estado ${activo ? "on" : "off"}`}>
              {activo ? "Activo" : "Apagado"}
            </div>
            <div className="sub">
              {activo
                ? "Va a intentar reservar hoy a las 21:00. Es de un solo uso: despues del intento se apaga solo."
                : "No va a reservar nada hasta que lo prendas para la proxima apertura"}
            </div>
          </div>
          <button
            className="sw"
            data-on={activo}
            disabled={ocupado}
            aria-label={activo ? "Apagar" : "Prender"}
            onClick={() => patch({ activo: !activo }, activo ? "Bot apagado" : "Bot activado")}
          />
        </div>
      </div>

      <div className="card">
        <h2>Horario</h2>
        <input type="date" value={fecha} min={manana()} onChange={(e) => setFecha(e.target.value)} />
        <div className="sub" style={{ marginBottom: 12 }}>
          Para jugar el <strong>{etiquetaFecha(fecha)}</strong> — se reserva a las 21:00 del día anterior.
          {hayOverride ? " Hay un horario puntual guardado para este día." : " Usando el horario habitual."}
        </div>

        <div className="horas">
          {HORAS.map((h) => {
            const i = horas.indexOf(h);
            return (
              <button key={h} className="chip" data-sel={i >= 0} onClick={() => toggleHora(h)}>
                {h}:00
                {i >= 0 && <span className="orden">{i + 1}</span>}
              </button>
            );
          })}
        </div>
        <div className="sub" style={{ marginTop: 10 }}>
          {horas.length
            ? `Intenta en este orden: ${horas.map((h) => h + ":00").join(" → ")}`
            : "Sin horarios elegidos: ese día no reserva nada."}
        </div>

        <div style={{ height: 14 }} />
        <h2>C.I. del invitado</h2>
        <input type="text" inputMode="numeric" value={ci} onChange={(e) => setCi(e.target.value)} placeholder="sin puntos ni guión" />
        <div className="sub">Tiene 30 minutos para aceptar la invitación o la reserva se cae.</div>

        <div style={{ height: 14 }} />
        <button
          className="accion"
          disabled={ocupado}
          onClick={() => patch({ override: { fecha, horas, ci_invitado: ci } }, "Guardado")}
        >
          Guardar para el {etiquetaFecha(fecha)}
        </button>
      </div>

      <div className="card">
        <h2>Ahora mismo</h2>
        <button className="accion fantasma" disabled={ocupado} onClick={reservarAhora}>
          Intentar reservar ya
        </button>
        <div className="sub" style={{ marginTop: 8 }}>
          Corre el runner en el momento, sin esperar a las 21:00. Sirve si se liberó una cancha.
        </div>
      </div>

      <div className="card">
        <h2>Últimas corridas</h2>
        {corridas.length === 0 && <div className="sub">Todavía no corrió ninguna vez.</div>}
        {corridas.map((c) => {
          const color =
            c.resultado === "success" ? "var(--ok)"
            : c.resultado === "failure" ? "var(--alerta)"
            : "var(--off)";
          return (
            <div className="corrida" key={c.id}>
              <span>
                <span className="punto" style={{ background: color }} />
                {new Date(c.cuando).toLocaleString("es-UY", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}
              </span>
              <a href={c.url} target="_blank" rel="noreferrer">
                {c.resultado || c.estado} ↗
              </a>
            </div>
          );
        })}
      </div>
    </div>
  );
}
