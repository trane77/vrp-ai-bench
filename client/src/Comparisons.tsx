import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import type { Scenario, Solution, Validation } from "./types";
import { request } from "./connection";
const MapView = lazy(() => import("./MapView"));
type Case = {
  id: string;
  instance: string;
  method: string;
  budget_s: number;
  repetition: number;
  time_weight: number;
  recording: boolean;
  control: boolean;
  available?: boolean;
};
type Campaign = {
  id: string;
  status: string;
  cancel_requested: boolean;
  request: { manifest: { cases: Case[] } };
};
type Observation = {
  elapsed_s: number;
  solution: Solution;
  validation: Validation;
};
type Result = {
  case: Case;
  status: string;
  error?: string;
  termination_reason: string;
  scenario?: Scenario;
  scenario_available?: boolean;
  objective?: Scenario["objective"];
  observations?: Observation[];
  quality?: {
    feasible_within_budget: boolean;
    target_censored: boolean;
    target_time_s: number;
    best_score: number | null;
  };
  timing?: { solve_s: number; observer_s: number };
  checkpoint_hash?: string | null;
  learned?: { device: string; inference_s: number };
  training?: { training_device: string; training_duration_s?: number };
};
type Front = {
  instance: string;
  method: string;
  budget_s: number;
  repetition: number;
  alternatives: Observation[];
  hypervolume: number;
};
async function get<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await request("/api/v1/experiments" + path, method, body);
  return response.json();
}
export function compatibleCases(a: Case, b: Case) {
  return (
    a.instance === b.instance &&
    a.budget_s === b.budget_s &&
    a.time_weight === b.time_weight &&
    a.recording === b.recording
  );
}
export function incumbentAt(events: Observation[], elapsed: number) {
  return events.filter((e) => e.elapsed_s <= elapsed).at(-1) ?? null;
}
const emptyAction = () => {};
export default function Comparisons() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]),
    [id, setId] = useState(localStorage.getItem("m3-campaign") ?? ""),
    [cases, setCases] = useState<Case[]>([]),
    [results, setResults] = useState<Result[]>([]),
    [fronts, setFronts] = useState<Front[]>([]);
  const [trainingCost, setTrainingCost] = useState<{
    model_training_s: number;
    training_phase_s?: number;
    preflight_s: number;
    training_and_preflight_s: number;
  } | null>(null);
  const cache = useRef(new Map<string, Result>());
  const [error, setError] = useState(""),
    [city, setCity] = useState("bologna"),
    [method, setMethod] = useState(""),
    [budget, setBudget] = useState("5"),
    [weight, setWeight] = useState(""),
    [recording, setRecording] = useState("true"),
    [status, setStatus] = useState("");
  const [left, setLeft] = useState(""),
    [right, setRight] = useState(""),
    [elapsed, setElapsed] = useState(0),
    [maps, setMaps] = useState(false);
  const campaign = campaigns.find((c) => c.id === id);
  const [mapScenario, setMapScenario] = useState<Scenario | null>(null);
  const scenarioCache = useRef(new Map<string, Scenario>());
  useEffect(() => {
    if (!maps || !left) return;
    let alive = true;
    const selected = results.find((r) => r.case.id === left);
    if (!selected) return;
    const key = id + selected.case.instance;
    const cached = scenarioCache.current.get(key);
    if (cached) {
      setMapScenario(cached);
      return;
    }
    setMapScenario(null);
    get<Result>("/" + id + "/cases/" + left)
      .then((r) => {
        if (r.scenario) {
          scenarioCache.current.set(key, r.scenario);
          if (alive) setMapScenario(r.scenario);
        }
      })
      .catch((e) => {
        if (alive) setError(String(e));
      });
    return () => {
      alive = false;
    };
  }, [maps, id, left]);
  useEffect(() => {
    let alive = true;
    let refreshing = false;
    let terminalLoaded = false;
    const refresh = async () => {
      if (refreshing) return;
      refreshing = true;
      try {
        const jobs = await get<Campaign[]>("");
        if (!alive) return;
        setCampaigns(jobs);
        if (!id || terminalLoaded) return;
        const all = await get<Case[]>("/" + id + "/cases");
        if (!alive) return;
        setCases(all);
        setCity((current) =>
          all.some((c) => c.instance === current)
            ? current
            : (all[0]?.instance ?? ""),
        );
        const loaded = await Promise.all(
          all
            .filter((c) => c.available)
            .map((c) =>
              cache.current.has(id + c.id)
                ? Promise.resolve(cache.current.get(id + c.id)!)
                : get<Result>(
                    "/" + id + "/cases/" + c.id + "?view=summary",
                  ).then((r) => {
                    cache.current.set(id + c.id, r);
                    return r;
                  }),
            ),
        );
        if (!alive) return;
        setResults(loaded);
        if (
          jobs.find((j) => j.id === id)?.status &&
          ["completed", "cancelled", "failed"].includes(
            jobs.find((j) => j.id === id)!.status,
          )
        ) {
          const report = await get<{
            fronts: Front[];
            training_cost?: {
              model_training_s: number;
              training_phase_s?: number;
              preflight_s: number;
              training_and_preflight_s: number;
            };
          }>("/" + id + "/report");
          if (alive) {
            setFronts(report.fronts);
            setTrainingCost(report.training_cost ?? null);
            terminalLoaded = true;
          }
        }
      } catch (e) {
        if (alive) setError(String(e));
      } finally {
        refreshing = false;
      }
    };
    void refresh();
    const timer = setInterval(refresh, 5000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [id]);
  const visible = useMemo(
    () =>
      cases.filter(
        (c) =>
          (!city || c.instance === city) &&
          (!method || c.method === method) &&
          (!budget || c.budget_s === Number(budget)) &&
          (!weight || c.time_weight === Number(weight)) &&
          (!recording || String(c.recording) === recording) &&
          (!status ||
            results.find((r) => r.case.id === c.id)?.status === status),
      ),
    [cases, city, method, budget, weight, recording, status, results],
  );
  const a = results.find((r) => r.case.id === left),
    b = results.find((r) => r.case.id === right);
  const compatible = !!a && !!b && compatibleCases(a.case, b.case);
  const shownFronts = fronts.filter(
    (f) =>
      !!city &&
      !!budget &&
      (!city || f.instance === city) &&
      (!method || f.method === method) &&
      (!budget || f.budget_s === Number(budget)),
  );
  const points = shownFronts.flatMap((f) =>
    f.alternatives.map((p) => ({
      x: p.validation.metrics.fleet_elapsed_s / 60,
      y: p.validation.metrics.estimated_fuel_l,
      method: f.method,
      rep: f.repetition,
    })),
  );
  const maxX = Math.max(1, ...points.map((p) => p.x)),
    maxY = Math.max(1, ...points.map((p) => p.y));
  async function submit(file: File) {
    try {
      const manifest = JSON.parse(await file.text());
      const job = await get<Campaign>("", "POST", { manifest });
      setId(job.id);
      localStorage.setItem("m3-campaign", job.id);
      setError("");
    } catch (e) {
      setError(String(e));
    }
  }
  return (
    <section className="history-section comparison-section" id="comparisons">
      <h2>Confronti</h2>
      <p>
        Istanze costruite: nessuna generalizzazione alle città. Riferimento
        fattibile non certificato; carburante stimato e non calibrato.
      </p>
      <p>
        Il costo di training è condiviso dai casi dello stesso checkpoint e non
        va sommato per caso.
      </p>
      <label>
        Carica manifest congelato{" "}
        <input
          aria-label="Carica manifest"
          type="file"
          accept=".json"
          onChange={(e) => {
            if (e.target.files?.[0]) void submit(e.target.files[0]);
          }}
        />
      </label>
      <label>
        Campagna{" "}
        <select
          aria-label="Campagna"
          value={id}
          onChange={(e) => {
            setId(e.target.value);
            localStorage.setItem("m3-campaign", e.target.value);
            setResults([]);
            setFronts([]);
            setTrainingCost(null);
            setLeft("");
            setRight("");
          }}
        >
          <option value="">Seleziona</option>
          {campaigns.map((c) => (
            <option key={c.id} value={c.id}>
              {c.id.slice(0, 8)} · {c.status}
            </option>
          ))}
        </select>
      </label>
      {campaign && (
        <p role="status">
          {results.length}/{cases.length} casi salvati · {campaign.status}{" "}
          {campaign.cancel_requested ? "· cancellazione richiesta" : ""}{" "}
          <button
            disabled={!["queued", "running"].includes(campaign.status)}
            onClick={() =>
              void get("/" + id + "/cancel", "POST").catch((e) =>
                setError(String(e)),
              )
            }
          >
            Cancella campagna
          </button>
        </p>
      )}
      {trainingCost && (
        <p>
          Fase training:{" "}
          {(
            trainingCost.training_phase_s ?? trainingCost.model_training_s
          ).toFixed(3)}{" "}
          s · preflight CPU/GPU: {trainingCost.preflight_s.toFixed(3)} s · costo
          condiviso totale: {trainingCost.training_and_preflight_s.toFixed(3)}{" "}
          s.
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      <div className="comparison-filters">
        {(
          [
            [
              "Istanza",
              city,
              setCity,
              [...new Set(cases.map((c) => c.instance))],
            ],
            [
              "Metodo",
              method,
              setMethod,
              ["ordered", "shuffled", "learned", "ortools"],
            ],
            ["Budget", budget, setBudget, ["5", "15"]],
            ["Peso tempo", weight, setWeight, ["1", "0.5", "0"]],
            ["Registrazione", recording, setRecording, ["true", "false"]],
            ["Stato", status, setStatus, ["completed", "failed", "cancelled"]],
          ] as const
        ).map(([label, value, set, options]) => (
          <label key={label}>
            {label}
            <select
              aria-label={label}
              value={value}
              onChange={(e) => set(e.target.value)}
            >
              <option value="">Tutti</option>
              {options.map((o) => (
                <option key={o}>{o}</option>
              ))}
            </select>
          </label>
        ))}
      </div>
      <div className="comparison-table">
        <table>
          <thead>
            <tr>
              {[
                "Caso",
                "Istanza",
                "Metodo",
                "Budget s",
                "Ripetizione",
                "Peso tempo",
                "Stato",
                "Soluzione entro budget",
                "Target",
                "Durata s",
                "Checkpoint / dispositivo",
                "Inferenza s / training condiviso s",
              ].map((h) => (
                <th key={h}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((c) => {
              const r = results.find((r) => r.case.id === c.id);
              return (
                <tr key={c.id}>
                  <td>{c.id}</td>
                  <td>{c.instance}</td>
                  <td>
                    {c.method}
                    {c.control ? " · controllo" : ""}
                  </td>
                  <td>{c.budget_s}</td>
                  <td>{c.repetition}</td>
                  <td>{c.time_weight}</td>
                  <td
                    className={`case-status ${r?.status ?? campaign?.status ?? "queued"}`}
                    title={r?.error}
                  >
                    {r?.status ??
                      (c.available
                        ? "caricamento"
                        : campaign?.status === "cancelled"
                          ? "non eseguito · campagna cancellata"
                          : campaign?.status === "failed"
                            ? "non eseguito · interruzione"
                            : "in attesa")}{" "}
                    {r?.termination_reason}
                  </td>
                  <td>
                    {r?.quality
                      ? r.quality.feasible_within_budget
                        ? "fattibile"
                        : "nessuna soluzione"
                      : "—"}
                  </td>
                  <td>
                    {r?.quality
                      ? r.quality.target_censored
                        ? "non raggiunto · censurato"
                        : `${r.quality.target_time_s.toFixed(3)} s`
                      : "—"}
                  </td>
                  <td>{r?.timing?.solve_s.toFixed(3) ?? "—"}</td>
                  <td title={r?.checkpoint_hash ?? undefined}>
                    {r?.checkpoint_hash?.slice(0, 12) ?? "—"} ·{" "}
                    {r?.learned?.device ?? "cpu"}
                  </td>
                  <td>
                    {r?.learned?.inference_s.toFixed(4) ?? "—"} /{" "}
                    {r?.training?.training_duration_s?.toFixed(2) ?? "—"}
                    {r?.training
                      ? ` · training ${r.training.training_device}`
                      : ""}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {campaign &&
        ["completed", "cancelled", "failed"].includes(campaign.status) && (
          <p>
            {["json", "csv", "md"].map((format) => (
              <button
                key={format}
                onClick={() => request("/api/v1/experiments/" + id + "/export/" + format)
                  .then((response) => response.blob())
                  .then((blob) => {
                    const url = URL.createObjectURL(blob);
                    const link = document.createElement("a");
                    link.href = url; link.download = `report.${format}`; link.click();
                    setTimeout(() => URL.revokeObjectURL(url), 1000);
                  }).catch((error) => alert(error.message))}
              >
                Scarica {format.toUpperCase()}{" "}
              </button>
            ))}
          </p>
        )}
      <h3>Replay sul tempo di calcolo registrato</h3>
      <label>
        Sinistra{" "}
        <select
          aria-label="Sinistra"
          value={left}
          onChange={(e) => {
            setLeft(e.target.value);
            setRight("");
            setElapsed(0);
          }}
        >
          <option value="">Seleziona</option>
          {results
            .filter((r) => r.scenario_available && r.case.recording)
            .map((r) => (
              <option key={r.case.id} value={r.case.id}>
                {r.case.id} · {r.case.instance} · {r.case.method} ·{" "}
                {r.case.budget_s}s · peso {r.case.time_weight}
              </option>
            ))}
        </select>
      </label>
      <label>
        Destra{" "}
        <select
          aria-label="Destra"
          value={right}
          onChange={(e) => setRight(e.target.value)}
        >
          <option value="">Seleziona</option>
          {results
            .filter((r) => r.scenario_available && r.case.recording)
            .map((r) => (
              <option
                key={r.case.id}
                value={r.case.id}
                disabled={!a || !compatibleCases(a.case, r.case)}
              >
                {r.case.id} · {r.case.method} · rip. {r.case.repetition}
              </option>
            ))}
        </select>
      </label>
      {!compatible && (
        <p>
          Seleziona casi compatibili della stessa istanza, budget, preferenza e
          registrazione.
        </p>
      )}
      {compatible && (
        <>
          <label>
            Tempo di calcolo: {elapsed.toFixed(2)} s{" "}
            <input
              aria-label="Tempo di confronto"
              type="range"
              min="0"
              max={Math.max(a!.timing?.solve_s ?? 0, b!.timing?.solve_s ?? 0)}
              step="0.01"
              value={elapsed}
              onChange={(e) => setElapsed(Number(e.target.value))}
            />
          </label>
          <svg viewBox="0 0 600 180" role="img" aria-label="Curve anytime">
            <text x="10" y="15" fill="currentColor">
              Obiettivo normalizzato / tempo di calcolo (s), entro budget
            </text>
            {[a!, b!].map((r, index) => {
              const events = (r.observations ?? []).filter(
                (e) => e.elapsed_s <= r.case.budget_s,
              );
              const score = (observation: Observation) => {
                const m = observation.validation.metrics,
                  objective = r.objective!;
                return (
                  (objective.time_weight * m.fleet_elapsed_s) /
                    objective.time_scale_s +
                  (objective.fuel_weight * m.estimated_fuel_l) /
                    objective.fuel_scale_l
                );
              };
              const max = Math.max(
                1,
                ...[...(a!.observations ?? []), ...(b!.observations ?? [])].map(
                  (e) => score(e),
                ),
              );
              return (
                <g key={index}>
                  <text
                    x="10"
                    y={35 + index * 18}
                    fill={index ? "#719aff" : "#3cdbb7"}
                  >
                    {r.case.method} · {r.case.id}
                  </text>
                  <path
                    fill="none"
                    stroke={index ? "#719aff" : "#3cdbb7"}
                    d={events
                      .map(
                        (e, i) =>
                          `${i ? "H" : "M"} ${70 + (e.elapsed_s / r.case.budget_s) * 510} ${i ? "V" : ""} ${170 - (score(e) / max) * 100}`,
                      )
                      .join(" ")}
                  />
                </g>
              );
            })}
          </svg>
          <button onClick={() => setMaps(!maps)}>
            {maps ? "Chiudi mappe" : "Apri mappe sincronizzate"}
          </button>
          {maps && !mapScenario && <p>Caricamento geometrie…</p>}
          {maps && mapScenario && (
            <Suspense fallback={<p>Caricamento mappe…</p>}>
              <div className="comparison-maps">
                {[a!, b!].map((r, index) => {
                  const incumbent = incumbentAt(r.observations ?? [], elapsed);
                  return (
                    <div key={r.case.id}>
                      <p>
                        {r.case.method} ·{" "}
                        {incumbent
                          ? `${incumbent.elapsed_s.toFixed(2)} s`
                          : "nessun incumbent registrato"}
                      </p>
                      <MapView
                        center={
                          mapScenario.locations.find(
                            (p) => p.id === mapScenario.depot_id,
                          )!.snapped
                        }
                        draft={null}
                        scenario={mapScenario}
                        solution={incumbent?.solution ?? null}
                        validation={incumbent?.validation ?? null}
                        elapsed={null}
                        selected={null}
                        tool="select"
                        onSelect={emptyAction}
                        onPoint={emptyAction}
                        onFrame={emptyAction}
                      />
                      <span>{index === 0 ? "A" : "B"}</span>
                    </div>
                  );
                })}
              </div>
            </Suspense>
          )}
          <p>
            Ultimo incumbent validato disponibile al tempo indicato; nessuna
            interpolazione delle mosse. Il limite SwiftShader M2 (p95 circa 100
            ms) resta aperto.
          </p>
        </>
      )}
      <h3>Alternative non dominate trovate</h3>
      {method === "ortools" && (
        <p>
          OR-Tools partecipa alla famiglia tempo. Le alternative
          tempo/carburante sono disponibili per le politiche custom.
        </p>
      )}
      <p>
        Minuti flotta e litri stimati. Tre preferenze costano 3 × budget; ogni
        ripetizione resta separata. Il fronte ottimo completo non è noto.
        Seleziona istanza e budget per confrontare i punti.
      </p>
      <svg
        viewBox="0 0 600 240"
        role="img"
        aria-label="Diagramma tempo carburante"
      >
        <path d="M40 20V210H580" stroke="currentColor" fill="none" />
        <text x="40" y="15" fill="currentColor">
          Litri stimati (max {maxY.toFixed(2)})
        </text>
        <text x="250" y="235" fill="currentColor">
          Minuti flotta (max {maxX.toFixed(1)})
        </text>
        {points.map((p, i) => (
          <circle
            key={i}
            cx={40 + (p.x / maxX) * 530}
            cy={210 - (p.y / maxY) * 180}
            r="4"
            fill={
              p.method === "ordered"
                ? "#3cdbb7"
                : p.method === "learned"
                  ? "#ffc46b"
                  : "#719aff"
            }
          >
            <title>
              {p.method} · rip. {p.rep} · {p.x.toFixed(2)} min ·{" "}
              {p.y.toFixed(3)} L
            </title>
          </circle>
        ))}
      </svg>
      {shownFronts.map((f) => (
        <p key={[f.instance, f.method, f.budget_s, f.repetition].join("-")}>
          {f.instance} · {f.method} · {f.budget_s}s × 3 · rip. {f.repetition}:{" "}
          {f.alternatives.length} alternative · hypervolume normalizzato{" "}
          {f.hypervolume.toFixed(4)}
        </p>
      ))}
    </section>
  );
}
