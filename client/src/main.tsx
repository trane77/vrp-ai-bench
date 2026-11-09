import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  ArrowRight,
  Check,
  ChevronRight,
  Clock3,
  Database,
  Flag,
  FlaskConical,
  Languages,
  Layers3,
  MapPin,
  MousePointer2,
  Move,
  Pause,
  Play,
  Plus,
  Radio,
  RotateCcw,
  Route,
  Settings2,
  SkipForward,
  Square,
  Trash2,
  Truck,
  Wifi,
  WifiOff,
} from "lucide-react";
import { colors } from "./colors";
const MapView = React.lazy(() => import("./MapView"));
import { cityTime, emptyReplay, mergeEvents, stateAt } from "./replay";
import { I18nProvider, useI18n, SPEED_OPTIONS } from "./i18n";
import type {
  City,
  Coord,
  Draft,
  Editor,
  Event,
  Job,
  Parameters,
  Run,
  Scenario,
  Validation,
} from "./types";
import "./style.css";
import Comparisons from "./Comparisons";
import RnDView from "./RnDView";
import { endpoint, request, streamUrl, token } from "./connection";

async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await request("/api/v1" + path, method, body);
  return response.json();
}
const terminal = (j: Job | null) =>
  !!j && ["completed", "failed", "cancelled"].includes(j.status);
const formatTime = (s: number) =>
  `${Math.floor(s / 60)}:${Math.floor(s % 60)
    .toString()
    .padStart(2, "0")}`;
const number = (n: number, digits = 1) =>
  n.toLocaleString("it-IT", { maximumFractionDigits: digits });
function Field({
  label,
  value,
  onChange,
  min = 0,
  step = 1,
}: {
  label: string;
  value: number | string;
  onChange: (n: number) => void;
  min?: number;
  step?: number;
}) {
  const format = (v: number | string) => String(v).replace(".", ",");
  const [text, setText] = useState(() => format(value));
  useEffect(() => {
    setText(format(value));
  }, [value]);
  const decimals = (String(step).split(".")[1] ?? "").length;
  const round = (n: number) =>
    decimals > 0 ? Number(n.toFixed(decimals)) : Math.round(n);
  return (
    <label className="field">
      {label}
      <input
        type="text"
        inputMode="decimal"
        value={text}
        onChange={(e) => {
          const raw = e.target.value;
          setText(raw);
          const normalized = raw.trim().replace(",", ".");
          if (normalized === "" || normalized === "-" || normalized === ".") {
            onChange(0);
            return;
          }
          const parsed = Number(normalized);
          if (!Number.isNaN(parsed)) {
            onChange(Math.max(min, parsed));
          }
        }}
        onBlur={() => setText(format(value))}
        onKeyDown={(e) => {
          if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
          e.preventDefault();
          const current = Number(String(value).replace(",", ".")) || 0;
          const delta = e.key === "ArrowUp" ? step : -step;
          const next = Math.max(min, round(current + delta));
          setText(format(next));
          onChange(next);
        }}
      />
    </label>
  );
}

function App() {
  const { t, lang, setLang } = useI18n();
  const [remoteEndpoint, setRemoteEndpoint] = useState(() => endpoint() === location.origin ? "" : endpoint());
  const [remoteToken, setRemoteToken] = useState(() => token());
  const [cities, setCities] = useState<City[]>([]),
    [city, setCity] = useState("bologna");
  const [citiesLoading, setCitiesLoading] = useState(true);
  const [editor, setEditor] = useState<Editor | null>(null),
    [draft, setDraft] = useState<Draft | null>(null),
    [dirty, setDirty] = useState(false);
  const [scenario, setScenario] = useState<Scenario | null>(null),
    [run, setRun] = useState<Run | null>(null),
    [preparedId, setPreparedId] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null),
    [history, setHistory] = useState<Job[]>([]),
    [events, setEvents] = useState<Event[]>([]);
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [connected, setConnected] = useState(true);
  const [seed, setSeed] = useState(42),
    [count, setCount] = useState(20),
    [budget, setBudget] = useState(10);
  const [policy, setPolicy] = useState<"cpu" | "learned">("cpu");
  const [navSection, setNavSection] = useState<"scenario" | "history" | "rnd">("scenario");
  const [selected, setSelected] = useState<string | null>("depot"),
    [tool, setTool] = useState("select"),
    [mode, setMode] = useState<"search" | "route">("search");
  const [index, setIndex] = useState(0),
    [following, setFollowing] = useState(true),
    [playing, setPlaying] = useState(false),
    [speed, setSpeed] = useState(1),
    [routeTime, setRouteTime] = useState(0);
  const [reduced, setReduced] = useState(
      () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    ),
    [candidate, setCandidate] = useState(false),
    [frameMs, setFrameMs] = useState(0);
  const restoreInfo = useRef({
    id: localStorage.getItem("vrp-job"),
    replay: localStorage.getItem("vrp-replay"),
  });
  const restoring = useRef(Boolean(restoreInfo.current.id));
  const eventsRef = useRef(events);
  eventsRef.current = events;
  const generation = useRef(0);
  const activeCity = cities.find((c) => c.id === city),
    active = !!job && !terminal(job);
  const replay = useMemo(() => stateAt(events, index), [events, index]);
  const display =
    mode === "route"
      ? run
        ? { solution: run.solution, validation: run.validation, partial: false }
        : replay.best
      : candidate
        ? replay.current
        : replay.best;
  const validation = display?.validation ?? null;
  const selectedStop = draft?.stops.find((s) => s.id === selected);
  const selectedVisit = validation?.vehicles
    .flatMap((v) => v.visits)
    .find((v) => v.stop_id === selected);
  const selectedLocation = scenario?.locations.find((p) => p.id === selected);
  const currentEvent = events[index];
  const perform = async (task: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await task();
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  };
  const refreshHistory = useCallback(
    () =>
      api<Job[]>("/jobs").then((jobs) =>
        setHistory(jobs.filter((j) => j.kind !== ("experiment" as string))),
      ),
    [],
  );
  useEffect(() => {
    api<City[]>("/cities")
      .then(setCities)
      .catch((e) => {
        setError(e.message);
        setConnected(false);
      })
      .finally(() => setCitiesLoading(false));
    refreshHistory().catch((e) => setError(e.message));
  }, [refreshHistory]);
  const loadEvents = useCallback(async (id: string) => {
    let all: Event[] = [];
    while (true) {
      const batch = await api<Event[]>(
        `/jobs/${id}/events?after=${all.at(-1)?.seq ?? 0}`,
      );
      all = mergeEvents(all, batch);
      if (batch.length < 500) break;
    }
    return all;
  }, []);
  const openJob = useCallback(
    async (id: string) => {
      const token = ++generation.current;
      localStorage.setItem("vrp-job", id);
      setPlaying(false);
      setRun(null);
      setScenario(null);
      setEvents([]);
      setIndex(0);
      setFollowing(true);
      setRouteTime(0);
      setMode("search");
      setCandidate(false);
      const selectedJob = await api<Job>(`/jobs/${id}`);
      const ed = await api<Editor>(`/drafts/${selectedJob.request.draft_id}`);
      if (token !== generation.current) return;
      setJob(selectedJob);
      setEditor(ed);
      setDraft(ed.data);
      setCity(ed.data.city_id);
      setSeed(
        selectedJob.kind === "solve"
          ? (selectedJob.request.seed ?? ed.data.seed)
          : ed.data.seed,
      );
      setCount(ed.data.stops.length);
      if (selectedJob.kind === "solve") {
        setBudget(selectedJob.request.budget_s ?? 10);
        setPolicy(selectedJob.request.policy === "learned" ? "learned" : "cpu");
      }
      setDirty(false);
      const prep =
        selectedJob.kind === "prepare"
          ? id
          : selectedJob.request.prepared_job_id;
      if (prep) {
        const prepJob = await api<Job>(`/jobs/${prep}`);
        if (prepJob.status === "completed") {
          const s = await api<Scenario>(`/jobs/${prep}/result`);
          const d = await api<Draft>(`/jobs/${prep}/editor`);
          if (token !== generation.current) return;
          setScenario(s);
          setDraft(d);
          setPreparedId(prep);
        } else setPreparedId(null);
      }
      const recorded = await loadEvents(id);
      if (token !== generation.current) return;
      setEvents(recorded);
      setIndex(Math.max(0, recorded.length - 1));
      if (
        selectedJob.kind === "solve" &&
        terminal(selectedJob) &&
        selectedJob.feasibility === "feasible"
      ) {
        const r = await api<Run>(`/jobs/${id}/result`);
        if (token === generation.current) setRun(r);
      }
      localStorage.setItem("vrp-job", id);
      await refreshHistory();
    },
    [loadEvents, refreshHistory],
  );
  useEffect(() => {
    const { id, replay: saved } = restoreInfo.current;
    let disposed = false;
    if (id) {
      setBusy(true);
      openJob(id)
        .then(() => {
          if (disposed) return;
          if (saved) {
            try {
              const p = JSON.parse(saved);
              if (p.id === id) {
                setIndex(p.index);
                setFollowing(false);
                setMode(p.mode);
                setRouteTime(p.routeTime);
              }
            } catch {
              /* ignore obsolete browser state */
            }
          }
        })
        .catch((e) => {
          if (!disposed) {
            localStorage.removeItem("vrp-job");
            setError(e.message);
          }
        })
        .finally(() => {
          if (!disposed) {
            restoring.current = false;
            setBusy(false);
          }
        });
    }
    return () => {
      disposed = true;
    };
  }, [openJob]);
  useEffect(() => {
    if (job && !restoring.current)
      localStorage.setItem(
        "vrp-replay",
        JSON.stringify({ id: job.id, index, mode, routeTime }),
      );
  }, [job?.id, index, mode, routeTime, busy]);
  useEffect(() => {
    if (following) setIndex(Math.max(0, events.length - 1));
  }, [events.length, following]);
  useEffect(() => {
    if (!job || terminal(job)) return;
    const id = job.id;
    let disposed = false,
      socket: WebSocket | null = null,
      retry: ReturnType<typeof setTimeout>;
    const connect = () => {
      if (disposed) return;
      socket = new WebSocket(streamUrl(`/api/v1/jobs/${id}/stream?after=${eventsRef.current.at(-1)?.seq ?? 0}`));
      socket.onopen = () => setConnected(true);
      socket.onmessage = (message) => {
        if (disposed) return;
        const data = JSON.parse(message.data);
        if (data.type === "events")
          setEvents((old) => mergeEvents(old, data.data));
        if (data.type === "snapshot")
          loadEvents(id)
            .then(setEvents)
            .catch((e) => setError(e.message));
        if (data.type === "status") setJob(data.data);
      };
      socket.onerror = () => setConnected(false);
      socket.onclose = () => {
        if (!disposed) {
          setConnected(false);
          retry = setTimeout(connect, 1200);
        }
      };
    };
    connect();
    return () => {
      disposed = true;
      clearTimeout(retry);
      socket?.close();
    };
  }, [job?.id, job?.status, loadEvents]);
  useEffect(() => {
    if (!job || terminal(job)) return;
    let disposed = false;
    const timer = setInterval(async () => {
      try {
        const latest = await api<Job>(`/jobs/${job.id}`);
        if (disposed) return;
        setConnected(true);
        setJob(latest);
        if (terminal(latest)) {
          const recorded = await loadEvents(job.id);
          if (disposed) return;
          setEvents(recorded);
          await refreshHistory();
          if (latest.status === "completed" && latest.kind === "prepare") {
            const s = await api<Scenario>(`/jobs/${latest.id}/result`);
            const d = await api<Draft>(`/jobs/${latest.id}/editor`);
            if (disposed) return;
            setScenario(s);
            setDraft(d);
            setPreparedId(latest.id);
          }
          if (latest.kind === "solve" && latest.feasibility === "feasible") {
            const r = await api<Run>(`/jobs/${latest.id}/result`);
            if (!disposed) setRun(r);
          }
        }
      } catch {
        if (!disposed) setConnected(false);
      }
    }, 600);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, [job?.id, job?.status, loadEvents, refreshHistory]);
  // Terminal status can arrive through WebSocket before the polling tick.
  useEffect(() => {
    if (!job || !terminal(job)) return;
    let disposed = false;
    const finish = async () => {
      const recorded = await loadEvents(job.id);
      if (disposed) return;
      setEvents(recorded);
      setConnected(true);
      await refreshHistory();
      if (job.kind === "prepare" && job.status === "completed") {
        const s = await api<Scenario>(`/jobs/${job.id}/result`),
          d = await api<Draft>(`/jobs/${job.id}/editor`);
        if (!disposed) {
          setScenario(s);
          setDraft(d);
          setPreparedId(job.id);
        }
      }
      if (job.kind === "solve" && job.feasibility === "feasible") {
        const r = await api<Run>(`/jobs/${job.id}/result`);
        if (!disposed) setRun(r);
      }
    };
    finish().catch((e) => {
      if (!disposed) setError(e.message);
    });
    return () => {
      disposed = true;
    };
  }, [job?.id, job?.status, loadEvents, refreshHistory]);
  useEffect(() => {
    if (!playing || reduced) return;
    let last = performance.now(),
      acc = 0;
    const tick = setInterval(() => {
      const now = performance.now();
      acc += ((now - last) / 1000) * speed;
      last = now;
      if (mode === "route") {
        setRouteTime((old) => {
          const end = run?.validation?.metrics.completion_s ?? 0;
          const next = Math.min(end, old + acc);
          acc = 0;
          if (next >= end) setPlaying(false);
          return next;
        });
      } else {
        setFollowing(false);
        setIndex((old) => {
          const list = eventsRef.current;
          let next = old;
          while (
            next < list.length - 1 &&
            acc >=
              Math.max(0.05, list[next + 1].elapsed_s - list[next].elapsed_s)
          ) {
            acc -= Math.max(
              0.05,
              list[next + 1].elapsed_s - list[next].elapsed_s,
            );
            next++;
          }
          if (next >= list.length - 1) setPlaying(false);
          return next;
        });
      }
    }, 50);
    return () => clearInterval(tick);
  }, [playing, speed, mode, reduced, run]);
  function edit(change: (draft: Draft) => void, preserveMode = false) {
    if (!draft || active) return;
    const copy = structuredClone(draft);
    change(copy);
    if (!preserveMode) copy.mode = "edited_demo";
    setDraft(copy);
    setDirty(true);
    setScenario(null);
    setRun(null);
    setPreparedId(null);
    setPlaying(false);
    setEvents([]);
    setJob(null);
    setIndex(0);
    localStorage.removeItem("vrp-job");
  }
  function params<K extends keyof Parameters>(key: K, value: Parameters[K]) {
    edit((d) => {
      d.parameters[key] = value;
      if (key === "vehicle_ids") d.mode = "witness_demo";
    }, true);
  }
  async function createDemo() {
    await perform(async () => {
      const ed = await api<Editor>("/drafts", "POST", {
        city_id: city,
        seed,
        count,
      });
      generation.current++;
      setEditor(ed);
      setDraft(ed.data);
      setScenario(null);
      setRun(null);
      setJob(null);
      setPreparedId(null);
      setEvents([]);
      setDirty(false);
      setSelected("depot");
      setTool("select");
      localStorage.removeItem("vrp-job");
    });
  }
  async function prepare() {
    await perform(async () => {
      if (!editor || !draft) return;
      let ed = editor;
      if (dirty) {
        ed = await api<Editor>(`/drafts/${editor.id}`, "PUT", {
          revision: editor.revision,
          data: draft,
        });
        setEditor(ed);
        setDraft(ed.data);
        setDirty(false);
      }
      const queued = await api<Job>("/jobs", "POST", {
        kind: "prepare",
        draft_id: ed.id,
        revision: ed.revision,
      });
      await openJob(queued.id);
    });
  }
  async function start() {
    await perform(async () => {
      if (!editor || !preparedId) return;
      const queued = await api<Job>("/jobs", "POST", {
        kind: "solve",
        draft_id: editor.id,
        revision: editor.revision,
        prepared_job_id: preparedId,
        seed,
        budget_s: budget,
        policy,
      });
      await openJob(queued.id);
    });
  }
  function point(coordinate: Coord) {
    if (tool === "add") {
      if ((draft?.stops.length ?? 0) >= 100) {
        setError("Limite di 100 fermate");
        return;
      }
      const id = `stop-${crypto.randomUUID().slice(0, 6)}`;
      edit((d) =>
        d.stops.push({
          id,
          coordinate,
          window_open_s: 0,
          window_close_s: 28800,
          packages: 1,
          mass_kg: 2,
          volume_m3: 0.01,
        }),
      );
      setSelected(id);
    } else if (tool === "move" && selected) {
      edit((d) => {
        if (selected === "depot") d.depot = coordinate;
        else {
          const s = d.stops.find((s) => s.id === selected);
          if (s) s.coordinate = coordinate;
        }
      });
    }
    setTool("select");
  }
  function stopField(
    key:
      | "window_open_s"
      | "window_close_s"
      | "packages"
      | "mass_kg"
      | "volume_m3"
      | "delivery_mass_kg"
      | "delivery_volume_m3",
    value: number,
  ) {
    edit((d) => {
      const s = d.stops.find((s) => s.id === selected);
      if (s) s[key] = value;
    });
  }
  const bestUpdates = events.filter(
    (e) => e.kind === "feasible_best" && e.payload.validation,
  );
  const progress = events.filter((e) => e.kind === "metric").at(-1)?.payload;
  const metrics = validation?.metrics;
  const center = activeCity?.center ?? ([11.3426, 44.4949] as Coord);
  const f = draft?.parameters;
  return (
    <div className="app">
      <aside className="rail">
        <div className="brand-mark">
          <Route size={26} />
        </div>
        <button
          className={`rail-button ${navSection === "scenario" ? "chosen" : ""}`}
          title={t.scenario}
          onClick={() => {
            setNavSection("scenario");
            window.scrollTo({ top: 0, behavior: "smooth" });
          }}
        >
          <Layers3 size={20} />
        </button>
        <button
          className={`rail-button ${navSection === "history" ? "chosen" : ""}`}
          title={t.history}
          onClick={() => {
            setNavSection("history");
            document
              .getElementById("history")
              ?.scrollIntoView({ behavior: "smooth" });
          }}
        >
          <Database size={20} />
        </button>
        <button
          className={`rail-button rail-bottom ${navSection === "rnd" ? "chosen" : ""}`}
          title="Laboratorio R&D"
          aria-label="Laboratorio R&D"
          onClick={() => {
            setNavSection("rnd");
            document
              .getElementById("rnd-lab")
              ?.scrollIntoView({ behavior: "smooth" });
          }}
        >
          <FlaskConical size={20} />
          <span>R&amp;D</span>
        </button>
      </aside>
      <div className="workspace">
        <header>
          <div className="brand">
            VRP<span>AI</span>
            <span className="brand-divider" />
            <small>{t.app}</small>
            <span className="version">M8 / CPU+M4+R&amp;D</span>
          </div>
          <div className="header-status">
            <span className="local-pill">
              <span className="live-dot" />
              {t.subtitle}
            </span>
            <span className={connected ? "connection" : "connection warning"}>
              {connected ? <Wifi size={15} /> : <WifiOff size={15} />}
              <span>{connected ? t.online : t.disconnected}</span>
            </span>
            <div className="lang-selector" role="group" aria-label={t.language}>
              <Languages size={15} className="lang-icon" />
              <button
                type="button"
                className={`lang-btn ${lang === "it" ? "active" : ""}`}
                onClick={() => setLang("it")}
                title="Italiano"
                aria-label="Italiano"
              >
                IT
              </button>
              <span className="lang-sep">/</span>
              <button
                type="button"
                className={`lang-btn ${lang === "en" ? "active" : ""}`}
                onClick={() => setLang("en")}
                title="English"
                aria-label="English"
              >
                EN
              </button>
            </div>
          </div>
        </header>
        <form className="connection-settings" onSubmit={(e) => {
          e.preventDefault();
          const value = remoteEndpoint.trim().replace(/\/$/, "");
          if (value && (!value.startsWith("https://") || new URL(value).pathname !== "/")) {
            setError("L'endpoint Ai360 deve essere un'origine HTTPS senza percorso."); return;
          }
          sessionStorage.setItem("vrp-endpoint", value || location.origin);
          sessionStorage.setItem("vrp-token", remoteToken.trim());
          location.reload();
        }}>
          <label>Ai360 HTTPS <input aria-label="Ai360 HTTPS" placeholder="https://ai360.lan:8000" value={remoteEndpoint} onChange={(e) => setRemoteEndpoint(e.target.value)} /></label>
          <label>Token <input aria-label="Token Ai360" type="password" autoComplete="off" value={remoteToken} onChange={(e) => setRemoteToken(e.target.value)} /></label>
          <button type="submit">Connetti</button>
        </form>
        <div className="intro">
          <div>
            <div className="eyebrow">
              EXPERIMENT WORKSPACE <span>/ 01</span>
            </div>
            <h1>{t.tagline}</h1>
            <p>{t.synthetic}</p>
          </div>
          <div className="intro-note">
            <FlaskConical size={18} />
            <div>
              CLASSICAL BASELINE<small>{t.notCertified}</small>
            </div>
          </div>
        </div>
        {error && (
          <div className="alert" role="alert">
            {error}
            <button onClick={() => setError("")} aria-label="Chiudi errore">
              ×
            </button>
          </div>
        )}
        <main>
          <section className="panel config">
            <div className="panel-title">
              <span>
                <Settings2 size={16} />
                {t.scenario}
              </span>
              <span className="step">01</span>
            </div>
            <div className="config-body">
              <label className="field">
                {t.city}
                <select
                  aria-label={t.city}
                  value={city}
                  disabled={active || busy}
                  onChange={(e) => {
                    setCity(e.target.value);
                    setDraft(null);
                    setEditor(null);
                    setScenario(null);
                    setRun(null);
                    setPreparedId(null);
                    setJob(null);
                    setEvents([]);
                    localStorage.removeItem("vrp-job");
                  }}
                >
                  {(cities.length
                    ? cities
                    : [
                        { id: "bologna", name: "Bologna", installed: false },
                        { id: "modena", name: "Modena", installed: false },
                      ]
                  ).map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </label>
              <div
                className={`dataset-status ${activeCity?.installed ? "ready" : ""}`}
              >
                <Database size={13} />
                {citiesLoading
                  ? t.loading
                  : activeCity?.installed
                    ? t.installed
                    : t.notInstalled}
                <small>
                  {activeCity?.manifest?.version.slice(0, 8) ?? "—"}
                </small>
              </div>
              <fieldset className="fields" disabled={active || busy}>
                <Field label={t.seed} value={seed} onChange={setSeed} />
                <Field
                  label={t.stops}
                  value={count}
                  min={1}
                  onChange={setCount}
                />
              </fieldset>
              <button
                className="secondary full"
                disabled={!activeCity?.installed || active || busy}
                onClick={createDemo}
              >
                <Plus size={15} />
                {t.demo}
              </button>
              {!activeCity?.installed && !citiesLoading && (
                <p className="helper">{t.unavailable}</p>
              )}
              {draft && f && (
                <fieldset disabled={active || busy}>
                  <div className="section-line">
                    <span>FLOTTA E VINCOLI</span>
                  </div>
                  <div className="fields">
                    <Field
                      label={t.fleet}
                      value={f.vehicle_ids.length}
                      min={1}
                      onChange={(n) =>
                        params(
                          "vehicle_ids",
                          Array.from(
                            { length: Math.max(1, Math.min(5, Math.floor(n))) },
                            (_, i) => `van-${i + 1}`,
                          ),
                        )
                      }
                    />
                    <Field
                      label={t.budget}
                      value={budget}
                      onChange={setBudget}
                    />
                  </div>
                  <label className="field">
                    {t.serviceMode}
                    <select
                      value={f.service.mode}
                      onChange={(e) =>
                        params("service", {
                          ...f.service,
                          mode: e.target.value as "constant" | "packages",
                        })
                      }
                    >
                      <option value="constant">{t.constant}</option>
                      <option value="packages">{t.packages}</option>
                    </select>
                  </label>
                  <label className="field">
                    {t.duration}
                    <input
                      type="number"
                      min="0"
                      value={f.duration_cap_s ?? ""}
                      onChange={(e) =>
                        params(
                          "duration_cap_s",
                          e.target.value === "" ? null : Number(e.target.value),
                        )
                      }
                    />
                  </label>
                  <div className="field-caption">{t.capacity}</div>
                  <div className="fields">
                    <Field
                      label={t.mass}
                      value={f.capacity.mass_kg}
                      onChange={(n) =>
                        params("capacity", { ...f.capacity, mass_kg: n })
                      }
                    />
                    <Field
                      label={t.volume}
                      value={f.capacity.volume_m3}
                      step={0.01}
                      onChange={(n) =>
                        params("capacity", { ...f.capacity, volume_m3: n })
                      }
                    />
                  </div>
                  <div className="section-line">
                    <span>{t.weights.toUpperCase()}</span>
                  </div>
                  <div className="fields">
                    <Field
                      label={t.timeWeight}
                      value={f.objective.time_weight}
                      step={0.1}
                      onChange={(n) =>
                        params("objective", { ...f.objective, time_weight: n })
                      }
                    />
                    <Field
                      label={t.fuelWeight}
                      value={f.objective.fuel_weight}
                      step={0.1}
                      onChange={(n) =>
                        params("objective", { ...f.objective, fuel_weight: n })
                      }
                    />
                    <Field
                      label={t.timeScale}
                      value={f.objective.time_scale_s}
                      onChange={(n) =>
                        params("objective", { ...f.objective, time_scale_s: n })
                      }
                    />
                    <Field
                      label={t.fuelScale}
                      value={f.objective.fuel_scale_l}
                      step={0.1}
                      onChange={(n) =>
                        params("objective", { ...f.objective, fuel_scale_l: n })
                      }
                    />
                  </div>
                  <details>
                    <summary>{t.advanced}</summary>
                    <div
                      className="helper"
                      style={{
                        marginBottom: 8,
                        color:
                          f.fuel_model.calibration === "microsim_calibrated"
                            ? "#49b796"
                            : "#a08049",
                      }}
                    >
                      Stato modello carburante:{" "}
                      <strong>
                        {f.fuel_model.calibration === "microsim_calibrated"
                          ? "Calibrato da microsimulazione (M8)"
                          : "Parametrico non calibrato"}
                      </strong>
                    </div>
                    <Field
                      label={t.constantSeconds}
                      value={f.service.constant_s}
                      onChange={(n) =>
                        params("service", { ...f.service, constant_s: n })
                      }
                    />
                    <Field
                      label={t.fixedSeconds}
                      value={f.service.fixed_s}
                      onChange={(n) =>
                        params("service", { ...f.service, fixed_s: n })
                      }
                    />
                    <Field
                      label={t.packageSeconds}
                      value={f.service.per_package_s}
                      onChange={(n) =>
                        params("service", { ...f.service, per_package_s: n })
                      }
                    />
                    {Object.entries(f.fuel_model.categories).map(
                      ([key, value]) => (
                        <div key={key}>
                          <span className="category">{key}</span>
                          <div className="fields">
                            <Field
                              label={t.baseRate}
                              value={value.base_l_per_km}
                              step={0.001}
                              onChange={(n) =>
                                params("fuel_model", {
                                  ...f.fuel_model,
                                  categories: {
                                    ...f.fuel_model.categories,
                                    [key]: { ...value, base_l_per_km: n },
                                  },
                                })
                              }
                            />
                            <Field
                              label={t.loadRate}
                              value={value.load_l_per_km_kg}
                              step={0.0001}
                              onChange={(n) =>
                                params("fuel_model", {
                                  ...f.fuel_model,
                                  categories: {
                                    ...f.fuel_model.categories,
                                    [key]: { ...value, load_l_per_km_kg: n },
                                  },
                                })
                              }
                            />
                          </div>
                        </div>
                      ),
                    )}
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={f.fuel_model.idle_policy !== "off"}
                        onChange={(e) =>
                          params("fuel_model", {
                            ...f.fuel_model,
                            idle_policy: e.target.checked
                              ? "waiting_and_service"
                              : "off",
                            idle_l_per_s: e.target.checked ? 0.001 : 0,
                          })
                        }
                      />
                      {t.idle}
                    </label>
                    <Field
                      label={t.idleRate}
                      value={f.fuel_model.idle_l_per_s}
                      step={0.0001}
                      onChange={(n) =>
                        params("fuel_model", {
                          ...f.fuel_model,
                          idle_l_per_s: n,
                          idle_policy: n ? "waiting_and_service" : "off",
                        })
                      }
                    />
                    <div className="section-line" style={{ marginTop: 8 }}>
                      <span>PERCORSI E VINCOLI (M7)</span>
                    </div>
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={Boolean(f.path_optimization)}
                        onChange={(e) =>
                          params("path_optimization", e.target.checked)
                        }
                      />
                      Ottimizza percorsi alternativi OSRM (M7)
                    </label>
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={Boolean(f.driver_break?.enabled)}
                        onChange={(e) =>
                          params("driver_break", {
                            enabled: e.target.checked,
                            threshold_s: f.driver_break?.threshold_s ?? 14400,
                            duration_s: f.driver_break?.duration_s ?? 900,
                          })
                        }
                      />
                      Pausa conducente obbligatoria (M7)
                    </label>
                    {f.driver_break?.enabled && (
                      <div className="fields">
                        <Field
                          label="Soglia guida/serv. (s)"
                          value={f.driver_break.threshold_s}
                          min={60}
                          onChange={(n) =>
                            params("driver_break", {
                              ...f.driver_break!,
                              threshold_s: Math.max(60, n),
                            })
                          }
                        />
                        <Field
                          label="Durata pausa (s)"
                          value={f.driver_break.duration_s}
                          min={0}
                          onChange={(n) =>
                            params("driver_break", {
                              ...f.driver_break!,
                              duration_s: Math.max(0, Math.floor(n)),
                            })
                          }
                        />
                      </div>
                    )}
                  </details>
                </fieldset>
              )}
            </div>
            <div className="config-actions">
              <span className="helper">
                {dirty
                  ? t.dirty
                  : scenario
                    ? t.immutable
                    : "OSM → OSRM → VALIDATOR"}
              </span>
              <button
                className="secondary full"
                disabled={!draft || active || busy}
                onClick={prepare}
              >
                <Layers3 size={15} />
                {t.prepare}
              </button>
              <label className="field">
                {t.policyLabel}
                <select
                  aria-label={t.policyLabel}
                  value={policy}
                  disabled={active || busy}
                  onChange={(e) =>
                    setPolicy(e.target.value as "cpu" | "learned")
                  }
                >
                  <option value="cpu">{t.policyCpu}</option>
                  <option value="learned">{t.policyLearned}</option>
                </select>
              </label>
              <button
                className="primary full"
                disabled={!preparedId || dirty || active || busy}
                onClick={start}
              >
                {busy ? t.loading : t.solve}
                <ArrowRight size={16} />
              </button>
            </div>
          </section>
          <section className="center-column">
            <div className="map-topline">
              <div className="city-name">
                <MapPin size={16} />
                {activeCity?.name ?? "Bologna"}
                <span>ITALIA / OSM</span>
              </div>
              <div className="toolbar">
                <button
                  className={tool === "select" ? "selected" : ""}
                  title={t.select}
                  aria-label={t.select}
                  onClick={() => setTool("select")}
                >
                  <MousePointer2 size={15} />
                </button>
                <button
                  disabled={!draft || active}
                  className={tool === "add" ? "selected" : ""}
                  aria-label={t.add}
                  title={t.add}
                  onClick={() => setTool("add")}
                >
                  <Plus size={17} />
                </button>
                <button
                  disabled={!draft || !selected || active}
                  className={tool === "move" ? "selected" : ""}
                  aria-label={t.move}
                  title={t.move}
                  onClick={() => setTool("move")}
                >
                  <Move size={16} />
                </button>
              </div>
            </div>
            <div className="map-container">
              <React.Suspense fallback={<p>Caricamento mappa…</p>}>
                <MapView
                  center={center}
                  draft={draft}
                  scenario={scenario}
                  solution={display?.solution ?? null}
                  validation={validation}
                  elapsed={mode === "route" ? routeTime : null}
                  selected={selected}
                  tool={tool}
                  onSelect={setSelected}
                  onPoint={point}
                  onFrame={setFrameMs}
                />
              </React.Suspense>
              {!draft && (
                <div className="empty-state">
                  <div className="empty-icon">
                    <Route size={30} />
                  </div>
                  <h2>{t.empty}</h2>
                  <p>{t.emptyText}</p>
                  <button
                    className="primary"
                    disabled={!activeCity?.installed || busy}
                    onClick={createDemo}
                  >
                    {t.demo}
                    <ArrowRight size={16} />
                  </button>
                </div>
              )}
              {job && (
                <div className="job-card">
                  <div>
                    <span className={`live-dot ${active ? "pulse" : ""}`} />
                    <strong>{t.state[job.status]}</strong>
                    <span className="mono">{job.id.slice(0, 8)}</span>
                  </div>
                  <p>
                    {job.error ??
                      t.reason[job.termination_reason ?? ""] ??
                      (job.kind === "prepare"
                        ? "Preparazione dati stradali"
                        : job.request?.policy === "learned"
                          ? "Politica appresa (M4)"
                          : "Ricerca CPU")}
                  </p>
                  {job.kind === "solve" && job.request?.policy === "learned" && (
                    <p className="mono" style={{ fontSize: "0.75em", opacity: 0.7 }}>M4 learned policy</p>
                  )}
                  {job.kind === "solve" &&
                    job.status === "completed" &&
                    job.feasibility === "unknown" && <p>{t.noRoute}</p>}
                  {active && job.status === "queued" && (
                    <p className="warning" style={{ fontSize: "0.8em" }}>{t.queuedWarning}</p>
                  )}
                  {active && progress?.total && (
                    <div className="progress">
                      <span
                        style={{
                          width: `${(100 * (progress.done ?? 0)) / progress.total}%`,
                        }}
                      />
                    </div>
                  )}
                  {active && (
                    <button
                      className="text-button"
                      onClick={() =>
                        perform(async () => {
                          setJob(
                            await api<Job>(`/jobs/${job.id}/cancel`, "POST"),
                          );
                        })
                      }
                    >
                      <Square size={11} />
                      {t.cancel}
                    </button>
                  )}
                </div>
              )}
            </div>
            <div className="metric-strip">
              {[
                {
                  icon: <Clock3 size={16} />,
                  label: t.time,
                  value: metrics ? formatTime(metrics.fleet_elapsed_s) : "—",
                  unit: "min:s",
                },
                {
                  icon: <Flag size={16} />,
                  label: t.completion,
                  value: metrics ? formatTime(metrics.completion_s) : "—",
                  unit: "min:s",
                },
                {
                  icon: <Route size={16} />,
                  label: t.distance,
                  value: metrics ? number(metrics.distance_m / 1000) : "—",
                  unit: "km",
                },
                {
                  icon: <Activity size={16} />,
                  label: t.fuel,
                  value: metrics ? number(metrics.estimated_fuel_l, 3) : "—",
                  unit:
                    validation?.fuel_label.includes("microsim_calibrated") ||
                    f?.fuel_model.calibration === "microsim_calibrated"
                      ? "L calibrati (M8)"
                      : "L stimati (non cal.)",
                },
              ].map((m) => (
                <div className="metric" key={m.label}>
                  <div>
                    {m.icon}
                    <span>{m.label}</span>
                  </div>
                  <strong>
                    {m.value}
                    <small>{m.unit}</small>
                  </strong>
                </div>
              ))}
            </div>
            <section className="panel replay-panel">
              <div className="replay-heading">
                <div className="tabs">
                  <button
                    className={mode === "search" ? "active" : ""}
                    onClick={() => {
                      setMode("search");
                      setPlaying(false);
                    }}
                  >
                    <Radio size={14} />
                    {t.search}
                  </button>
                  <button
                    className={mode === "route" ? "active" : ""}
                    disabled={!run?.validation}
                    onClick={() => {
                      setMode("route");
                      setPlaying(false);
                    }}
                  >
                    <Truck size={15} />
                    {t.route}
                  </button>
                </div>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={reduced}
                    onChange={(e) => {
                      setReduced(e.target.checked);
                      setPlaying(false);
                    }}
                  />
                  {t.reduce}
                </label>
              </div>
              <div className="replay-controls">
                <button
                  className="play"
                  disabled={
                    reduced ||
                    (mode === "search" ? events.length === 0 : !run?.validation)
                  }
                  aria-label={playing ? t.pause : t.play}
                  onClick={() => {
                    setFollowing(false);
                    if (mode === "search" && index === events.length - 1)
                      setIndex(0);
                    if (
                      mode === "route" &&
                      routeTime >= (run?.validation?.metrics.completion_s ?? 0)
                    )
                      setRouteTime(0);
                    setPlaying(!playing);
                  }}
                >
                  {playing ? <Pause size={18} /> : <Play size={18} />}
                </button>
                <button
                  title={t.next}
                  aria-label={t.next}
                  disabled={mode !== "search" || index >= events.length - 1}
                  onClick={() => {
                    setFollowing(false);
                    setIndex((i) => Math.min(events.length - 1, i + 1));
                  }}
                >
                  <SkipForward size={16} />
                </button>
                <input
                  aria-label={t.seek}
                  type="range"
                  min="0"
                  max={
                    mode === "search"
                      ? Math.max(0, events.length - 1)
                      : (run?.validation?.metrics.completion_s ?? 0)
                  }
                  value={
                    mode === "search"
                      ? Math.min(index, Math.max(0, events.length - 1))
                      : routeTime
                  }
                  step={mode === "search" ? 1 : 0.1}
                  onChange={(e) => {
                    setFollowing(false);
                    setPlaying(false);
                    if (mode === "search") setIndex(Number(e.target.value));
                    else setRouteTime(Number(e.target.value));
                  }}
                />
                <span className="mono replay-time">
                  {mode === "search"
                    ? `${Math.min(index + 1, events.length)} / ${events.length}`
                    : formatTime(routeTime)}
                </span>
                <select
                  aria-label={t.speed}
                  value={speed}
                  onChange={(e) => setSpeed(Number(e.target.value))}
                >
                  {SPEED_OPTIONS.map((s) => (
                    <option value={s} key={s}>
                      {s}×
                    </option>
                  ))}
                </select>
              </div>
              <div className="replay-info">
                <span>
                  {mode === "search" ? t.unrecorded : t.interpolation}
                </span>
                <span className="mono">
                  {mode === "search"
                    ? `${t.compute} ${number(currentEvent?.elapsed_s ?? 0, 2)} s`
                    : `${t.simulation} ${scenario ? cityTime(scenario, routeTime) : formatTime(routeTime)} · ${scenario?.timezone ?? ""}`}
                </span>
              </div>
              {mode === "search" && (
                <div className="event-bar">
                  <span className="event-kind">
                    {currentEvent ? t.event[currentEvent.kind] : "—"}
                    {currentEvent?.payload.move_type
                      ? ` · ${t.moveType[currentEvent.payload.move_type] ?? currentEvent.payload.move_type}`
                      : ""}
                    {currentEvent?.payload.delta
                      ? ` / Δ ${number(currentEvent.payload.delta.fleet_elapsed_s)} s · ${number(currentEvent.payload.delta.estimated_fuel_l, 3)} L`
                      : ""}
                  </span>
                  <div>
                    <button
                      className={!candidate ? "active" : ""}
                      onClick={() => setCandidate(false)}
                    >
                      {t.best}
                    </button>
                    <button
                      className={candidate ? "active" : ""}
                      onClick={() => setCandidate(true)}
                    >
                      {t.candidate}
                    </button>
                    <button
                      title={t.nextBest}
                      aria-label={t.nextBest}
                      onClick={() => {
                        const i = events.findIndex(
                          (e, i) => i > index && e.kind === "feasible_best",
                        );
                        if (i >= 0) {
                          setFollowing(false);
                          setIndex(i);
                        }
                      }}
                    >
                      <ChevronRight size={15} />
                    </button>
                    <button
                      title="Torna in diretta"
                      aria-label="Torna in diretta"
                      onClick={() => setFollowing(true)}
                    >
                      <RotateCcw size={13} />
                    </button>
                  </div>
                </div>
              )}
              {display?.partial && (
                <div className="candidate-note">
                  {t.partial} · {validation?.violations.join("; ")}
                </div>
              )}
              {!replay.best &&
                events.length > 0 &&
                mode === "search" &&
                !candidate && <div className="helper no-best">{t.noBest}</div>}
              <Timelines
                validation={validation}
                start={scenario?.start_s ?? 0}
              />
            </section>
          </section>
          <aside className="right-column">
            <section className="panel details-panel">
              <div className="panel-title">
                <span>
                  <MapPin size={16} />
                  {t.node}
                </span>
                <span className="step">02</span>
              </div>
              <div className="details-body">
                <div className="node-title">
                  <span className="node-symbol">
                    {selected === "depot"
                      ? "D"
                      : (selectedStop?.id.replace("stop-", "") ?? "—")}
                  </span>
                  <div>
                    <h3>
                      {selected === "depot"
                        ? t.depot
                        : (selectedStop?.id ?? t.select)}
                    </h3>
                    <small>
                      {selected === "depot" ? "PARTENZA E RITORNO" : t.window}
                    </small>
                  </div>
                </div>
                <fieldset disabled={active || busy}>
                  {draft && selected && (
                    <div className="fields">
                      <Field
                        label={t.longitude}
                        value={
                          (selected === "depot"
                            ? draft.depot
                            : selectedStop?.coordinate)?.[0] ?? 0
                        }
                        min={-180}
                        step={0.00001}
                        onChange={(n) =>
                          edit((d) => {
                            const p =
                              selected === "depot"
                                ? d.depot
                                : d.stops.find((s) => s.id === selected)
                                    ?.coordinate;
                            if (p) p[0] = n;
                          })
                        }
                      />
                      <Field
                        label={t.latitude}
                        value={
                          (selected === "depot"
                            ? draft.depot
                            : selectedStop?.coordinate)?.[1] ?? 0
                        }
                        min={-90}
                        step={0.00001}
                        onChange={(n) =>
                          edit((d) => {
                            const p =
                              selected === "depot"
                                ? d.depot
                                : d.stops.find((s) => s.id === selected)
                                    ?.coordinate;
                            if (p) p[1] = n;
                          })
                        }
                      />
                    </div>
                  )}
                  {selectedStop && (
                    <>
                      <div className="fields">
                        <Field
                          label={t.windowOpen}
                          value={selectedStop.window_open_s}
                          onChange={(n) => stopField("window_open_s", n)}
                        />
                        <Field
                          label={t.windowClose}
                          value={selectedStop.window_close_s}
                          onChange={(n) => stopField("window_close_s", n)}
                        />
                      </div>
                      <Field
                        label={t.packagesCount}
                        value={selectedStop.packages}
                        onChange={(n) => stopField("packages", n)}
                      />
                      <div className="fields">
                        <Field
                          label={t.mass}
                          value={selectedStop.mass_kg}
                          step={0.1}
                          onChange={(n) => stopField("mass_kg", n)}
                        />
                        <Field
                          label={t.volume}
                          value={selectedStop.volume_m3}
                          step={0.01}
                          onChange={(n) => stopField("volume_m3", n)}
                        />
                      </div>
                      <Field
                        label="Consegna da deposito (kg, M7)"
                        value={selectedStop.delivery_mass_kg ?? 0}
                        step={0.1}
                        onChange={(n) => stopField("delivery_mass_kg", n)}
                      />
                      <button
                        className="danger full"
                        onClick={() => {
                          edit((d) => {
                            d.stops = d.stops.filter((s) => s.id !== selected);
                          });
                          setSelected("depot");
                        }}
                      >
                        <Trash2 size={13} />
                        {t.remove}
                      </button>
                    </>
                  )}
                </fieldset>
                {selectedLocation && (
                  <div className="data-row">
                    <span>{t.snap}</span>
                    <strong>
                      {number(selectedLocation.snap_distance_m)} m
                    </strong>
                  </div>
                )}
                {selectedVisit && (
                  <div className="visit-data">
                    {[
                      [t.arrival, selectedVisit.arrival_s],
                      [t.start, selectedVisit.service_start_s],
                      ...(selectedVisit.break_s
                        ? [[t.driverBreak, selectedVisit.break_s]]
                        : []),
                      [t.departure, selectedVisit.departure_s],
                      [
                        t.slack,
                        (selectedStop?.window_close_s ?? 0) -
                          selectedVisit.service_start_s,
                      ],
                    ].map(([label, value]) => (
                      <div className="data-row" key={label}>
                        <span>{label}</span>
                        <strong>{formatTime(Number(value))}</strong>
                      </div>
                    ))}
                    <div className="data-row">
                      <span>{t.load}</span>
                      <strong>{number(selectedVisit.load_kg)} kg</strong>
                    </div>
                  </div>
                )}
                <div className="section-line">
                  <span>
                    {t.stops.toUpperCase()} / {draft?.stops.length ?? 0}
                  </span>
                </div>
                <div className="stop-list">
                  {draft?.stops.map((s) => (
                    <button
                      key={s.id}
                      className={selected === s.id ? "chosen" : ""}
                      onClick={() => setSelected(s.id)}
                    >
                      <span className="stop-dot" />
                      {s.id}
                      <small>{s.packages} p.</small>
                    </button>
                  ))}
                </div>
              </div>
            </section>
            <section className="panel insight">
              <div className="eyebrow">ANYTIME / FLEET TIME</div>
              <div className="insight-title">
                <strong>{bestUpdates.length}</strong>
                <span>
                  miglioramenti
                  <br />
                  fattibili registrati
                </span>
                <Activity size={22} />
              </div>
              <svg
                viewBox="0 0 240 55"
                role="img"
                aria-label="Andamento del tempo flotta registrato"
              >
                <path d="M0 50H240 M0 25H240" stroke="#27313e" fill="none" />
                {bestUpdates.length > 0 && (
                  <polyline
                    fill="none"
                    stroke="#3cdbb7"
                    strokeWidth="2"
                    points={bestUpdates
                      .map(
                        (e, i) =>
                          `${(i / Math.max(1, bestUpdates.length - 1)) * 240},${50 - (e.payload.validation!.metrics.fleet_elapsed_s / (Math.max(...bestUpdates.map((e) => e.payload.validation!.metrics.fleet_elapsed_s)) || 1)) * 40}`,
                      )
                      .join(" ")}
                  />
                )}
              </svg>
              <small>
                {validation?.fuel_label.includes("microsim_calibrated") ||
                f?.fuel_model.calibration === "microsim_calibrated"
                  ? "Carburante calibrato da microsimulazione (M8)"
                  : t.estimate}
              </small>
            </section>
          </aside>
        </main>
        <section className="history-section" id="history">
          <div className="history-title">
            <span>
              <Database size={15} />
              {t.history}
            </span>
            <small>SESSIONI PERSISTENTI</small>
            <button
              className="text-button"
              onClick={() =>
                perform(async () => {
                  await refreshHistory();
                })
              }
            >
              Aggiorna
            </button>
          </div>
          {!history.length ? (
            <p className="helper">{t.noJobs}</p>
          ) : (
            <div className="history-list">
              {history.map((h) => (
                <button
                  key={h.id}
                  className={job?.id === h.id ? "current" : ""}
                  onClick={() => perform(() => openJob(h.id))}
                >
                  <span className={`status-dot ${h.status}`} />
                  <span>
                    <strong>
                      {h.kind === "prepare"
                        ? "Preparazione"
                        : h.request?.policy === "learned"
                          ? t.policyLearned
                          : "Ricerca CPU"}
                    </strong>
                    <small>
                      {h.id.slice(0, 8)} ·{" "}
                      {new Date(h.created * 1000).toLocaleString("it-IT")}
                    </small>
                  </span>
                  <span className="history-status">{t.state[h.status]}</span>
                  <ChevronRight size={15} />
                </button>
              ))}
            </div>
          )}
        </section>
        <RnDView
          solveJobId={
            job?.kind === "solve" && job.status === "completed" && job.feasibility === "feasible"
              ? job.id
              : run?.run_id ?? null
          }
          preparedJobId={preparedId}
          city={draft?.city_id ?? city}
          stopCount={draft?.stops.length ?? count}
          defaultVehicles={f?.vehicle_ids.length ?? 2}
          defaultServiceMode={f?.service.mode ?? "constant"}
          onApplyCalibratedFuel={(calibratedFuel) =>
            params("fuel_model", calibratedFuel)
          }
        />
        <Comparisons />
        <footer>
          <span>
            <Check size={12} />
            VALIDATORE INDIPENDENTE <span className="footer-dot">·</span>{" "}
            {t.notCertified}
          </span>
          <span>
            {t.performance}: {frameMs ? `${number(frameMs)} ms` : "—"}{" "}
            <span className="footer-dot">·</span> LOCAL FIRST
          </span>
        </footer>
      </div>
    </div>
  );
}
function Timelines({
  validation,
  start,
}: {
  validation: Validation | null;
  start: number;
}) {
  const { t } = useI18n();
  const end = validation?.metrics.completion_s ?? 0;
  const kindLabel: Record<string, string> = {
    travel: t.travel,
    wait: t.wait,
    service: t.service,
    break: t.driverBreak,
  };
  return (
    <div className="timelines">
      <div className="timeline-title">
        <span>{t.vehicles}</span>
        <div>
          <i className="travel-key" />
          {t.travel}
          <i className="wait-key" />
          {t.wait}
          <i className="service-key" />
          {t.service}
          <i className="break-key" />
          {t.driverBreak}
        </div>
      </div>
      {validation?.vehicles.map((v, index) => {
        let departure = start;
        const segments: { from: number; to: number; kind: string }[] = [];
        v.legs.forEach((leg, i) => {
          const arrival = departure + leg.travel_s;
          segments.push({ from: departure, to: arrival, kind: "travel" });
          const visit = v.visits[i];
          if (visit) {
            const breakS = visit.break_s ?? 0;
            const serviceEnd = visit.departure_s - breakS;
            segments.push(
              { from: arrival, to: visit.service_start_s, kind: "wait" },
              {
                from: visit.service_start_s,
                to: serviceEnd,
                kind: "service",
              },
            );
            if (breakS > 0) {
              segments.push({
                from: serviceEnd,
                to: visit.departure_s,
                kind: "break",
              });
            }
            departure = visit.departure_s;
          }
        });
        return (
          <div className="timeline-row" key={v.vehicle_id}>
            <span style={{ color: `rgb(${colors[index % 5].join(",")})` }}>
              <Truck size={12} />
              {v.vehicle_id}
            </span>
            <div className="timeline-track">
              {segments.map((s, i) => (
                <span
                  key={i}
                  className={`timeline-segment ${s.kind}`}
                  title={`${kindLabel[s.kind] ?? s.kind}: ${s.to - s.from} s`}
                  style={{
                    left: `${((s.from - start) / Math.max(1, end)) * 100}%`,
                    width: `${((s.to - s.from) / Math.max(1, end)) * 100}%`,
                  }}
                />
              ))}
            </div>
            <small>{formatTime(v.metrics.fleet_elapsed_s)}</small>
          </div>
        );
      })}
      {!validation && (
        <div className="timeline-empty">
          <div />
          <div />
          <div />
        </div>
      )}
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <I18nProvider>
      <App />
    </I18nProvider>
  </React.StrictMode>,
);
