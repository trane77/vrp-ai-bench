import { useEffect, useState } from "react";
import { FlaskConical, ShieldAlert, Cpu, Play, CheckCircle2, AlertTriangle, GitBranch, Gauge } from "lucide-react";
import { request } from "./connection";
import type { Parameters } from "./types";

type SummaryStats = {
  mean: number;
  p50: number;
  p95: number;
  max: number;
};

type StopRisk = {
  stop_id: string;
  window_open_s: number;
  window_close_s: number;
  nominal_arrival_s: number;
  nominal_service_start_s: number;
  nominal_slack_s: number;
  late_rate: number;
  mean_lateness_s: number;
  p95_lateness_s: number;
  p95_arrival_s: number;
};

type VehicleRisk = {
  vehicle_id: string;
  stops_count: number;
  nominal_duration_s: number;
  duration_cap_s: number | null;
  cap_violation_rate: number;
  mean_duration_s: number;
  p95_duration_s: number;
};

type StochasticReport = {
  schema_version: number;
  scenario_id: string;
  model: {
    distribution: string;
    seed: number;
    samples: number;
    travel_cv: number;
    service_cv: number;
    label: string;
  };
  baseline_metrics: {
    fleet_elapsed_s: number;
    completion_s: number;
    estimated_fuel_l: number;
    used_vans: number;
  };
  feasible_rate: number;
  window_compliance_rate: number;
  duration_cap_compliance_rate: number;
  metrics: {
    fleet_elapsed_s: SummaryStats;
    completion_s: SummaryStats;
    waiting_s: SummaryStats;
    total_lateness_s: SummaryStats;
    estimated_fuel_l: SummaryStats;
  };
  stop_risks: StopRisk[];
  vehicle_risks: VehicleRisk[];
};

type LearnedModelInfo = {
  model_hash: string;
  schema_version: number;
  architecture: number[];
  feature_version: number;
  feature_count: number;
  training_device: string;
  seed: number | null;
  epoch: number | null;
  validation_groups: number | null;
  training_groups: number | null;
  dataset_hash: string | null;
  source_id: string;
};

type PathModeMetrics = {
  feasible: boolean;
  objective_score: number | null;
  fleet_elapsed_s: number | null;
  travel_s: number | null;
  distance_m: number | null;
  estimated_fuel_l: number | null;
  used_vans: number;
  alt_paths_selected: number;
};

type PathComparisonReport = {
  schema_version: number;
  scenario_id: string;
  city_id: string;
  stop_count: number;
  total_legs: number;
  legs_with_alternatives: number;
  fuel_label: string;
  comparisons: {
    time_weight: number;
    fuel_weight: number;
    single_path: PathModeMetrics;
    multi_path: PathModeMetrics;
    delta: {
      objective_score: number | null;
      travel_s: number | null;
      estimated_fuel_l: number | null;
    };
  }[];
};

type MicrosimReport = {
  schema_version: number;
  scenario_id: string;
  simulator: {
    name: string;
    seed: number;
    congestion_factor: number;
    signal_delay_scale: number;
    curb_mass_kg: number;
    telemetry_disclaimer: string;
  };
  timing_drift: {
    nominal_travel_s: number;
    microsim_travel_s: number;
    signal_delay_s: number;
    travel_drift_s: number;
    travel_drift_pct: number;
    window_compliance_rate: number;
    duration_cap_ok: boolean;
  };
  fuel_comparison: {
    uncalibrated_parametric: {
      fuel_l: number;
      label: string;
      signed_error_l: number;
      abs_error_pct: number;
    };
    microsim_reference: {
      fuel_l: number;
      co2_kg: number;
      label: string;
    };
    calibrated_parametric: {
      fuel_l: number;
      label: string;
      signed_error_l: number;
      abs_error_pct: number;
    };
  };
  calibration: {
    calibrated_fuel_model: Parameters["fuel_model"];
    category_diagnostics: Record<
      string,
      {
        uncalibrated_base_l_per_km: number;
        uncalibrated_load_l_per_km_kg: number;
        calibrated_base_l_per_km: number;
        calibrated_load_l_per_km_kg: number;
        sample_count: number;
        rmse_l_per_km: number;
        mae_l_per_km: number;
        r2: number;
      }
    >;
    residuals: {
      rmse_l: number;
      mae_l: number;
      relative_bias_pct: number;
      mean_sample_fuel_l: number;
    };
    validity_bounds: {
      mass_kg_min: number;
      mass_kg_max: number;
      speed_kmh_min: number;
      speed_kmh_max: number;
    };
  };
  vehicle_reports: {
    vehicle_id: string;
    stops_count: number;
    nominal_travel_s: number;
    microsim_travel_s: number;
    signal_delay_s: number;
    microsim_duration_s: number;
    duration_cap_ok: boolean;
    uncalibrated_fuel_l: number;
    microsim_fuel_l: number;
    calibrated_fuel_l: number;
    co2_kg: number;
  }[];
};

const fmtNum = (n: number, digits = 1) =>
  n.toLocaleString("it-IT", { maximumFractionDigits: digits, minimumFractionDigits: 0 });

const fmtTime = (s: number) =>
  `${Math.floor(s / 60)}:${Math.floor(s % 60).toString().padStart(2, "0")}`;

export default function RnDView({
  solveJobId,
  preparedJobId,
  city,
  stopCount,
  defaultVehicles,
  defaultServiceMode,
  onApplyCalibratedFuel,
}: {
  solveJobId: string | null;
  preparedJobId: string | null;
  city: string;
  stopCount: number;
  defaultVehicles: number;
  defaultServiceMode: "constant" | "packages";
  onApplyCalibratedFuel?: (fuelModel: Parameters["fuel_model"]) => void;
}) {
  const [tab, setTab] = useState<"stochastic" | "campaigns" | "models" | "paths" | "microsim">("stochastic");
  const [pathReport, setPathReport] = useState<PathComparisonReport | null>(null);
  const [pathBusy, setPathBusy] = useState(false);
  const [microsimReport, setMicrosimReport] = useState<MicrosimReport | null>(null);
  const [microsimBusy, setMicrosimBusy] = useState(false);
  const [congestionText, setCongestionText] = useState("1,15");
  const [signalScaleText, setSignalScaleText] = useState("1,0");
  const [appliedCalibrated, setAppliedCalibrated] = useState(false);
  const [samples, setSamples] = useState(100);
  const [travelCvText, setTravelCvText] = useState("0,15");
  const [serviceCvText, setServiceCvText] = useState("0,20");
  const [stochSeed, setStochSeed] = useState(42);
  const [stochReport, setStochReport] = useState<StochasticReport | null>(null);
  const [stochBusy, setStochBusy] = useState(false);
  const [error, setError] = useState("");

  const [models, setModels] = useState<LearnedModelInfo[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);

  const [campVehicles, setCampVehicles] = useState(defaultVehicles);
  const [campServiceMode, setCampServiceMode] = useState<"constant" | "packages">(defaultServiceMode);
  const [campDurationCap, setCampDurationCap] = useState("");
  const [campMethods, setCampMethods] = useState<Record<string, boolean>>({
    ordered: true,
    shuffled: true,
    learned: true,
  });
  const [campBudgets, setCampBudgets] = useState<Record<number, boolean>>({
    5: true,
    15: true,
  });
  const [campBusy, setCampBusy] = useState(false);
  const [launchedCampaign, setLaunchedCampaign] = useState<{
    id: string;
    status: string;
    total: number;
  } | null>(null);

  useEffect(() => {
    setCampVehicles(defaultVehicles);
  }, [defaultVehicles]);

  useEffect(() => {
    setCampServiceMode(defaultServiceMode);
  }, [defaultServiceMode]);

  async function loadModels() {
    setModelsLoading(true);
    try {
      const resp = await request("/api/v1/rnd/models");
      const data: LearnedModelInfo[] = await resp.json();
      setModels(data);
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setModelsLoading(false);
    }
  }

  useEffect(() => {
    loadModels();
  }, []);

  async function runStochastic() {
    if (!solveJobId) return;
    setStochBusy(true);
    setError("");
    try {
      const travel_cv = Math.max(0, Math.min(2, Number(travelCvText.replace(",", ".")) || 0));
      const service_cv = Math.max(0, Math.min(2, Number(serviceCvText.replace(",", ".")) || 0));
      const resp = await request(`/api/v1/jobs/${solveJobId}/stochastic`, "POST", {
        seed: stochSeed,
        samples: Math.max(1, Math.min(1000, Math.floor(samples))),
        travel_cv,
        service_cv,
      });
      setStochReport(await resp.json());
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setStochBusy(false);
    }
  }

  async function launchScaleCampaign() {
    if (!preparedJobId) return;
    const selectedMethods = Object.entries(campMethods)
      .filter(([, v]) => v)
      .map(([k]) => k);
    const selectedBudgets = Object.entries(campBudgets)
      .filter(([, v]) => v)
      .map(([k]) => Number(k));
    if (!selectedMethods.length || !selectedBudgets.length) {
      setError("Seleziona almeno un metodo e un budget per la campagna.");
      return;
    }
    setCampBusy(true);
    setError("");
    try {
      const capNum = campDurationCap.trim() === "" ? null : Number(campDurationCap.replace(",", "."));
      const resp = await request("/api/v1/rnd/campaigns", "POST", {
        prepared_job_id: preparedJobId,
        vehicles: campVehicles,
        service_mode: campServiceMode,
        duration_cap_s: capNum,
        methods: selectedMethods,
        budgets: selectedBudgets,
        weights: [1.0, 0.5, 0.0],
        repetitions: 1,
      });
      const job = await resp.json();
      const total = job.request?.manifest?.cases?.length ?? selectedMethods.length * selectedBudgets.length * 3;
      setLaunchedCampaign({ id: job.id, status: job.status, total });
      localStorage.setItem("m3-campaign", job.id);
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setCampBusy(false);
    }
  }

  async function runPathComparison() {
    if (!preparedJobId) return;
    setPathBusy(true);
    setError("");
    try {
      const resp = await request("/api/v1/rnd/path-comparison", "POST", {
        prepared_job_id: preparedJobId,
        solve_job_id: solveJobId,
        seed: stochSeed,
        budget_s: 2.0,
        max_evaluations: 400,
      });
      setPathReport(await resp.json());
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setPathBusy(false);
    }
  }

  async function runMicrosim() {
    if (!solveJobId) return;
    setMicrosimBusy(true);
    setAppliedCalibrated(false);
    setError("");
    try {
      const congestion_factor = Math.max(0.8, Math.min(2.5, Number(congestionText.replace(",", ".")) || 1.15));
      const signal_delay_scale = Math.max(0.0, Math.min(3.0, Number(signalScaleText.replace(",", ".")) || 1.0));
      const resp = await request(`/api/v1/jobs/${solveJobId}/microsim`, "POST", {
        seed: stochSeed,
        congestion_factor,
        signal_delay_scale,
      });
      setMicrosimReport(await resp.json());
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setMicrosimBusy(false);
    }
  }

  return (
    <section className="rnd-section" id="rnd-lab" aria-label="Laboratorio R&D">
      <div className="rnd-header">
        <div>
          <div className="eyebrow">
            RESEARCH &amp; DEVELOPMENT <span>/ M6–M8</span>
          </div>
          <h2>
            <FlaskConical size={20} /> Laboratorio R&amp;D: Robustezza, Percorsi Alternativi, Microsimulazione e Modelli
          </h2>
          <p>
            Stress-test Monte Carlo (M6), confronto multi-percorso OSRM (M7), microsimulazione longitudinale e
            calibrazione carburante (M8), ispezione modelli appresi e lancio campagne su Ai360.
          </p>
        </div>
        <div className="rnd-tabs" role="tablist">
          <button
            role="tab"
            aria-selected={tab === "stochastic"}
            className={tab === "stochastic" ? "chosen" : ""}
            onClick={() => setTab("stochastic")}
          >
            <ShieldAlert size={14} /> Robustezza Stocastica
          </button>
          <button
            role="tab"
            aria-selected={tab === "paths"}
            className={tab === "paths" ? "chosen" : ""}
            onClick={() => setTab("paths")}
          >
            <GitBranch size={14} /> Percorsi &amp; Vincoli (M7)
          </button>
          <button
            role="tab"
            aria-selected={tab === "microsim"}
            className={tab === "microsim" ? "chosen" : ""}
            onClick={() => setTab("microsim")}
          >
            <Gauge size={14} /> Microsimulazione &amp; Carburante (M8)
          </button>
          <button
            role="tab"
            aria-selected={tab === "campaigns"}
            className={tab === "campaigns" ? "chosen" : ""}
            onClick={() => setTab("campaigns")}
          >
            <Play size={14} /> Campagne Multi-Scala
          </button>
          <button
            role="tab"
            aria-selected={tab === "models"}
            className={tab === "models" ? "chosen" : ""}
            onClick={() => {
              setTab("models");
              loadModels();
            }}
          >
            <Cpu size={14} /> Modelli Appresi ({models.length})
          </button>
        </div>
      </div>

      {error && (
        <div className="alert" role="alert">
          {error}
          <button onClick={() => setError("")} aria-label="Chiudi errore R&D">
            ×
          </button>
        </div>
      )}

      {tab === "stochastic" && (
        <div className="rnd-card">
          <div className="rnd-controls">
            <label className="field">
              Campioni Monte Carlo
              <input
                type="text"
                inputMode="numeric"
                value={samples}
                onChange={(e) => setSamples(Number(e.target.value.replace(/\D/g, "")) || 1)}
              />
            </label>
            <label className="field">
              Variabilità traffico (CV)
              <input
                type="text"
                inputMode="decimal"
                value={travelCvText}
                onChange={(e) => setTravelCvText(e.target.value)}
              />
            </label>
            <label className="field">
              Variabilità servizio (CV)
              <input
                type="text"
                inputMode="decimal"
                value={serviceCvText}
                onChange={(e) => setServiceCvText(e.target.value)}
              />
            </label>
            <label className="field">
              Seed stocastico
              <input
                type="text"
                inputMode="numeric"
                value={stochSeed}
                onChange={(e) => setStochSeed(Number(e.target.value.replace(/\D/g, "")) || 0)}
              />
            </label>
            <button
              className="primary"
              disabled={!solveJobId || stochBusy}
              onClick={runStochastic}
            >
              <ShieldAlert size={14} />
              {stochBusy ? "Simulazione in corso…" : "Esegui stress test stocastico"}
            </button>
          </div>

          {!solveJobId && (
            <p className="helper">
              Completa prima una ricerca fattibile nello Scenario (CPU o Politica appresa) per valutare la tenuta
              stocastica dei percorsi.
            </p>
          )}

          {stochReport && (
            <div className="stochastic-results">
              <div className="rnd-kpis">
                <div className="rnd-kpi">
                  <span>TENUTA COMPLESSIVA</span>
                  <strong className={stochReport.feasible_rate >= 0.8 ? "kpi-good" : "kpi-warn"}>
                    {fmtNum(stochReport.feasible_rate * 100, 1)}%
                  </strong>
                  <small>
                    Finestre: {fmtNum(stochReport.window_compliance_rate * 100, 1)}% · Durata:{" "}
                    {fmtNum(stochReport.duration_cap_compliance_rate * 100, 1)}%
                  </small>
                </div>
                <div className="rnd-kpi">
                  <span>TEMPO FLOTTA (P50 / P95)</span>
                  <strong>
                    {fmtTime(stochReport.metrics.fleet_elapsed_s.p50)} /{" "}
                    {fmtTime(stochReport.metrics.fleet_elapsed_s.p95)}
                  </strong>
                  <small>Nominale: {fmtTime(stochReport.baseline_metrics.fleet_elapsed_s)}</small>
                </div>
                <div className="rnd-kpi">
                  <span>RITARDO TOTALE (MEDIA / P95)</span>
                  <strong>
                    {fmtNum(stochReport.metrics.total_lateness_s.mean, 1)} s /{" "}
                    {fmtNum(stochReport.metrics.total_lateness_s.p95, 1)} s
                  </strong>
                  <small>Max osservato: {fmtNum(stochReport.metrics.total_lateness_s.max, 1)} s</small>
                </div>
                <div className="rnd-kpi">
                  <span>CARBURANTE STIMATO (P50 / P95)</span>
                  <strong>
                    {fmtNum(stochReport.metrics.estimated_fuel_l.p50, 2)} L /{" "}
                    {fmtNum(stochReport.metrics.estimated_fuel_l.p95, 2)} L
                  </strong>
                  <small>Nominale: {fmtNum(stochReport.baseline_metrics.estimated_fuel_l, 2)} L</small>
                </div>
              </div>

              <p className="helper">{stochReport.model.label}</p>

              <div className="rnd-tables">
                <div className="comparison-table">
                  <h3>Vulnerabilità finestre per fermata (top 10 più critiche)</h3>
                  <table>
                    <thead>
                      <tr>
                        <th>Fermata</th>
                        <th>Finestra</th>
                        <th>Arrivo nom.</th>
                        <th>Margine nom.</th>
                        <th>Prob. ritardo</th>
                        <th>Ritardo P95</th>
                      </tr>
                    </thead>
                    <tbody>
                      {stochReport.stop_risks.slice(0, 10).map((sr) => (
                        <tr key={sr.stop_id}>
                          <td>{sr.stop_id}</td>
                          <td>
                            {fmtTime(sr.window_open_s)} – {fmtTime(sr.window_close_s)}
                          </td>
                          <td>{fmtTime(sr.nominal_arrival_s)}</td>
                          <td>{fmtNum(sr.nominal_slack_s, 0)} s</td>
                          <td>
                            <span className={sr.late_rate > 0.2 ? "case-status failed" : "case-status completed"}>
                              {fmtNum(sr.late_rate * 100, 1)}%
                            </span>
                          </td>
                          <td>{fmtNum(sr.p95_lateness_s, 1)} s</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <div className="comparison-table">
                  <h3>Tenuta durata per furgone</h3>
                  <table>
                    <thead>
                      <tr>
                        <th>Furgone</th>
                        <th>Fermate</th>
                        <th>Durata nom.</th>
                        <th>Durata P95</th>
                        <th>Violazione cap</th>
                      </tr>
                    </thead>
                    <tbody>
                      {stochReport.vehicle_risks.map((vr) => (
                        <tr key={vr.vehicle_id}>
                          <td>{vr.vehicle_id}</td>
                          <td>{vr.stops_count}</td>
                          <td>{fmtTime(vr.nominal_duration_s)}</td>
                          <td>{fmtTime(vr.p95_duration_s)}</td>
                          <td>{fmtNum(vr.cap_violation_rate * 100, 1)}%</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {tab === "campaigns" && (
        <div className="rnd-card">
          <p className="helper">
            Configura e accoda una campagna comparativa multi-preferenza (`1,0 / 0,5 / 0,0`) sullo scenario stradale
            preparato corrente (<strong>{city}</strong> · <strong>{stopCount} fermate</strong>). Per testare la scala a{" "}
            <strong>50</strong> o <strong>100 fermate</strong>, imposta il numero di fermate nel pannello Scenario,
            clicca <em>Crea demo</em> e <em>Prepara percorsi</em>, quindi avvia la campagna qui.
          </p>
          <div className="rnd-controls">
            <label className="field">
              Furgoni (1–5)
              <input
                type="text"
                inputMode="numeric"
                value={campVehicles}
                onChange={(e) =>
                  setCampVehicles(Math.max(1, Math.min(5, Number(e.target.value.replace(/\D/g, "")) || 1)))
                }
              />
            </label>
            <label className="field">
              Modalità servizio
              <select
                value={campServiceMode}
                onChange={(e) => setCampServiceMode(e.target.value as "constant" | "packages")}
              >
                <option value="constant">Durata costante</option>
                <option value="packages">Durata per pacco</option>
              </select>
            </label>
            <label className="field">
              Durata massima (s, opz.)
              <input
                type="text"
                inputMode="numeric"
                placeholder="Nessun limite"
                value={campDurationCap}
                onChange={(e) => setCampDurationCap(e.target.value)}
              />
            </label>
            <div className="field">
              <span>Metodi</span>
              <div className="rnd-checks">
                {(["ordered", "shuffled", "learned"] as const).map((m) => (
                  <label key={m}>
                    <input
                      type="checkbox"
                      checked={!!campMethods[m]}
                      onChange={(e) => setCampMethods({ ...campMethods, [m]: e.target.checked })}
                    />
                    {m}
                  </label>
                ))}
              </div>
            </div>
            <div className="field">
              <span>Budget (s)</span>
              <div className="rnd-checks">
                {[5, 15].map((b) => (
                  <label key={b}>
                    <input
                      type="checkbox"
                      checked={!!campBudgets[b]}
                      onChange={(e) => setCampBudgets({ ...campBudgets, [b]: e.target.checked })}
                    />
                    {b} s
                  </label>
                ))}
              </div>
            </div>
            <button
              className="primary"
              disabled={!preparedJobId || campBusy}
              onClick={launchScaleCampaign}
            >
              <Play size={14} />
              {campBusy ? "Invio campagna…" : "Avvia campagna R&D"}
            </button>
          </div>

          {!preparedJobId && (
            <p className="helper">
              Prepara prima i percorsi stradali nello Scenario per abilitare l'avvio della campagna R&amp;D.
            </p>
          )}

          {launchedCampaign && (
            <div className="rnd-launched">
              <CheckCircle2 size={16} />
              <span>
                Campagna <strong>{launchedCampaign.id.slice(0, 8)}</strong> accodata ({launchedCampaign.total} casi).
              </span>
              <button
                className="text-button"
                onClick={() =>
                  document.getElementById("comparisons")?.scrollIntoView({ behavior: "smooth" })
                }
              >
                Vai ai Confronti →
              </button>
            </div>
          )}
        </div>
      )}

      {tab === "models" && (
        <div className="rnd-card">
          {modelsLoading ? (
            <p className="helper">Caricamento modelli…</p>
          ) : !models.length ? (
            <p className="helper">
              <AlertTriangle size={14} /> Nessun checkpoint trovato in <code>runs/m4-imports</code> o{" "}
              <code>runs/m6-models</code>.
            </p>
          ) : (
            <div className="comparison-table">
              <table>
                <thead>
                  <tr>
                    <th>Sorgente</th>
                    <th>Schema</th>
                    <th>Architettura MLP</th>
                    <th>Feature</th>
                    <th>Device Training</th>
                    <th>Seed / Epoca</th>
                    <th>Gruppi Train / Val</th>
                    <th>Model Hash (SHA-256)</th>
                  </tr>
                </thead>
                <tbody>
                  {models.map((m) => (
                    <tr key={m.model_hash}>
                      <td>{m.source_id.slice(0, 12)}</td>
                      <td>v{m.schema_version}</td>
                      <td>{m.architecture.join(" → ")}</td>
                      <td>
                        v{m.feature_version} ({m.feature_count})
                      </td>
                      <td>{m.training_device}</td>
                      <td>
                        {m.seed ?? "—"} / {m.epoch ?? "—"}
                      </td>
                      <td>
                        {m.training_groups ?? "—"} / {m.validation_groups ?? "—"}
                      </td>
                      <td className="mono">{m.model_hash.slice(0, 16)}…</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {tab === "paths" && (
        <div className="rnd-card">
          <div className="rnd-controls">
            <div className="rnd-summary-pill">
              Città: <strong>{city.toUpperCase()}</strong> · Fermate: <strong>{stopCount}</strong>
            </div>
            <button
              className="primary"
              disabled={!preparedJobId || pathBusy}
              onClick={runPathComparison}
            >
              <GitBranch size={14} />
              {pathBusy ? "Confronto multi-percorso…" : "Confronta Percorso Singolo vs Alternativi OSRM"}
            </button>
          </div>
          {!preparedJobId && (
            <p className="helper">
              Prepara prima i percorsi stradali nello Scenario per eseguire il confronto tra percorso singolo e
              percorsi alternativi OSRM (M7).
            </p>
          )}
          {pathReport && (
            <div className="rnd-results">
              <div className="rnd-kpis">
                <div className="rnd-kpi">
                  <span>Tratte dirette totali</span>
                  <strong>{pathReport.total_legs}</strong>
                  <small>{pathReport.city_id}</small>
                </div>
                <div className="rnd-kpi">
                  <span>Tratte con alternative OSRM</span>
                  <strong>{pathReport.legs_with_alternatives}</strong>
                  <small>
                    {fmtNum(
                      (100 * pathReport.legs_with_alternatives) / Math.max(1, pathReport.total_legs),
                      1,
                    )}
                    % della matrice
                  </small>
                </div>
                <div className="rnd-kpi">
                  <span>Modello carburante</span>
                  <strong style={{ fontSize: "1rem" }}>Linear-Load v1</strong>
                  <small>{pathReport.fuel_label}</small>
                </div>
              </div>
              <div className="comparison-table">
                <h4>Confronto Single-Path vs Multi-Path OSRM al variare dei pesi obiettivo</h4>
                <table>
                  <thead>
                    <tr>
                      <th>Pesi (Tempo / Carb.)</th>
                      <th>Score (Singolo → Multi)</th>
                      <th>Guida s (Singolo → Multi)</th>
                      <th>Distanza km (Singolo → Multi)</th>
                      <th>Carburante L (Singolo → Multi)</th>
                      <th>Alternative scelte</th>
                      <th>Δ Score</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pathReport.comparisons.map((row) => (
                      <tr key={row.time_weight}>
                        <td>
                          <strong>
                            {fmtNum(row.time_weight, 1)} / {fmtNum(row.fuel_weight, 1)}
                          </strong>
                        </td>
                        <td>
                          {row.single_path.objective_score !== null && row.multi_path.objective_score !== null
                            ? `${fmtNum(row.single_path.objective_score, 4)} → ${fmtNum(row.multi_path.objective_score, 4)}`
                            : "—"}
                        </td>
                        <td>
                          {row.single_path.travel_s !== null && row.multi_path.travel_s !== null
                            ? `${row.single_path.travel_s}s → ${row.multi_path.travel_s}s`
                            : "—"}
                        </td>
                        <td>
                          {row.single_path.distance_m !== null && row.multi_path.distance_m !== null
                            ? `${fmtNum(row.single_path.distance_m / 1000, 2)} → ${fmtNum(row.multi_path.distance_m / 1000, 2)}`
                            : "—"}
                        </td>
                        <td>
                          {row.single_path.estimated_fuel_l !== null && row.multi_path.estimated_fuel_l !== null
                            ? `${fmtNum(row.single_path.estimated_fuel_l, 3)} → ${fmtNum(row.multi_path.estimated_fuel_l, 3)}`
                            : "—"}
                        </td>
                        <td>{row.multi_path.alt_paths_selected}</td>
                        <td>
                          {row.delta.objective_score !== null
                            ? `${row.delta.objective_score <= 0 ? "" : "+"}${fmtNum(row.delta.objective_score, 4)}`
                            : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {tab === "microsim" && (
        <div className="rnd-card">
          <div className="rnd-controls">
            <label className="field">
              Fattore congestione (0,8–2,5)
              <input
                type="text"
                inputMode="decimal"
                value={congestionText}
                onChange={(e) => setCongestionText(e.target.value)}
              />
            </label>
            <label className="field">
              Scala code semaforiche (0,0–3,0)
              <input
                type="text"
                inputMode="decimal"
                value={signalScaleText}
                onChange={(e) => setSignalScaleText(e.target.value)}
              />
            </label>
            <label className="field">
              Seed microsimulazione
              <input
                type="text"
                inputMode="numeric"
                value={stochSeed}
                onChange={(e) => setStochSeed(Number(e.target.value.replace(/\D/g, "")) || 0)}
              />
            </label>
            <button
              className="primary"
              disabled={!solveJobId || microsimBusy}
              onClick={runMicrosim}
            >
              <Gauge size={14} />
              {microsimBusy ? "Microsimulazione in corso…" : "Esegui Microsimulazione & Calibrazione (M8)"}
            </button>
          </div>
          {!solveJobId && (
            <p className="helper">
              Completa prima una ricerca fattibile (Avvia ricerca) per eseguire la microsimulazione longitudinale e
              calibrare il modello carburante (M8).
            </p>
          )}
          {microsimReport && (
            <div className="rnd-results">
              <div className="rnd-kpis">
                <div className="rnd-kpi">
                  <span>Deriva Tempo Guida</span>
                  <strong>
                    {fmtTime(microsimReport.timing_drift.nominal_travel_s)} →{" "}
                    {fmtTime(microsimReport.timing_drift.microsim_travel_s)}
                  </strong>
                  <small>
                    {microsimReport.timing_drift.travel_drift_pct >= 0 ? "+" : ""}
                    {fmtNum(microsimReport.timing_drift.travel_drift_pct, 1)}% (code semafori:{" "}
                    {fmtNum(microsimReport.timing_drift.signal_delay_s, 0)}s)
                  </small>
                </div>
                <div className="rnd-kpi">
                  <span>Finestre in Microsim</span>
                  <strong>{fmtNum(microsimReport.timing_drift.window_compliance_rate * 100, 1)}%</strong>
                  <small>
                    Cap durata: {microsimReport.timing_drift.duration_cap_ok ? "Rispettato" : "Superato"}
                  </small>
                </div>
                <div className="rnd-kpi">
                  <span>1. Parametrico Non Calibrato</span>
                  <strong>{fmtNum(microsimReport.fuel_comparison.uncalibrated_parametric.fuel_l, 3)} L</strong>
                  <small>
                    Errore vs Microsim: {fmtNum(microsimReport.fuel_comparison.uncalibrated_parametric.abs_error_pct, 1)}%
                  </small>
                </div>
                <div className="rnd-kpi">
                  <span>2. Riferimento Microsimulato</span>
                  <strong>{fmtNum(microsimReport.fuel_comparison.microsim_reference.fuel_l, 3)} L</strong>
                  <small>
                    Emissioni stimate: {fmtNum(microsimReport.fuel_comparison.microsim_reference.co2_kg, 2)} kg CO₂
                  </small>
                </div>
                <div className="rnd-kpi">
                  <span>3. Parametrico Calibrato (M8)</span>
                  <strong>{fmtNum(microsimReport.fuel_comparison.calibrated_parametric.fuel_l, 3)} L</strong>
                  <small>
                    Errore residuo: {fmtNum(microsimReport.fuel_comparison.calibrated_parametric.abs_error_pct, 1)}% · RMSE{" "}
                    {fmtNum(microsimReport.calibration.residuals.rmse_l, 4)} L
                  </small>
                </div>
              </div>

              <div className="comparison-table">
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <h4>
                    Calibrazione OLS `linear-load-v1` vs Riferimento Fisico (`{microsimReport.simulator.name}`, massa a
                    vuoto {microsimReport.simulator.curb_mass_kg} kg)
                  </h4>
                  {onApplyCalibratedFuel && (
                    <button
                      className="secondary"
                      onClick={() => {
                        onApplyCalibratedFuel(microsimReport.calibration.calibrated_fuel_model);
                        setAppliedCalibrated(true);
                      }}
                    >
                      <CheckCircle2 size={14} />
                      {appliedCalibrated
                        ? "Modello calibrato applicato allo Scenario"
                        : "Applica modello calibrato allo Scenario"}
                    </button>
                  )}
                </div>
                <table>
                  <thead>
                    <tr>
                      <th>Categoria stradale</th>
                      <th>Base non cal. (L/km)</th>
                      <th>Base calibrata (L/km)</th>
                      <th>Carico non cal. (L/km/kg)</th>
                      <th>Carico calibrato (L/km/kg)</th>
                      <th>RMSE (L/km)</th>
                      <th>MAE (L/km)</th>
                      <th>R²</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(microsimReport.calibration.category_diagnostics).map(([cat, d]) => (
                      <tr key={cat}>
                        <td>
                          <strong>{cat}</strong>
                        </td>
                        <td>{fmtNum(d.uncalibrated_base_l_per_km, 4)}</td>
                        <td>
                          <strong>{fmtNum(d.calibrated_base_l_per_km, 4)}</strong>
                        </td>
                        <td>{fmtNum(d.uncalibrated_load_l_per_km_kg, 6)}</td>
                        <td>
                          <strong>{fmtNum(d.calibrated_load_l_per_km_kg, 6)}</strong>
                        </td>
                        <td>{fmtNum(d.rmse_l_per_km, 5)}</td>
                        <td>{fmtNum(d.mae_l_per_km, 5)}</td>
                        <td>{fmtNum(d.r2, 3)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="helper" style={{ marginTop: 8 }}>
                  Limiti di validità calibrazione: carico{" "}
                  <strong>
                    [{fmtNum(microsimReport.calibration.validity_bounds.mass_kg_min, 0)}–
                    {fmtNum(microsimReport.calibration.validity_bounds.mass_kg_max, 0)} kg]
                  </strong>
                  , velocità media{" "}
                  <strong>
                    [{fmtNum(microsimReport.calibration.validity_bounds.speed_kmh_min, 1)}–
                    {fmtNum(microsimReport.calibration.validity_bounds.speed_kmh_max, 1)} km/h]
                  </strong>
                  . {microsimReport.simulator.telemetry_disclaimer}
                </p>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
