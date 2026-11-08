# Roadmap

M0–M6 are implemented and verified. M5 LAN service deployment on Ai360, reconnection, persistence and main-computer acceptance are verified. M6 adds scale-ladder campaigns (20/50/100 stops, 1–5 vans, constant/packages service), Feature/Policy v2 (`38→64→32→2`), Monte Carlo stochastic robustness evaluation, the interactive R&D laboratory in the client, and map bundle code-splitting. Full pilot acceptance is recorded in the M3 operations document.

## M0 — Requirements and repository

- [x] Capture confirmed scope, defaults and open decisions.
- [x] Define architecture, interface direction and evaluation protocol.
- [x] Establish development guidance and ignore generated artifacts.
- [x] Inspect the development runtime: CPython 3.12.3 used for M1 checks.
- [x] Select Bologna and Modena for M2 demonstration datasets.

Acceptance: documents are consistent, linked, and make no unsupported implementation or performance claims.

## M1 — Domain model and independently validated baseline

- [x] Versioned scenarios/results, time/load accounting and deterministic fuel model.
- [x] CPU classical solver, one depot, 1–5 vans, both service modes, windows and duration limit.
- [x] Small synthetic fixtures with known outcomes and explicit provenance.

Acceptance: independent validation catches duplicate/missing stops, capacity violations, late starts and late depot returns; hand-calculated schedules agree; impossible and timeout cases are distinguished. No UI required to run and save a fixture.

Verification: `python3 -m unittest discover -s tests -v` covers domain invariants,
CPU search and end-to-end CLI publication/revalidation. Impossible-fixture search
failure is reported as unknown feasibility, distinct from budget exhaustion;
M1 provides no infeasibility proof. See README.md for executable commands.

## M2 — First end-to-end city experience

- [x] Prepare Bologna/Modena datasets and routing paths with provenance; installed-city selector.
- [x] Service/worker separation, job lifecycle and durable event recording.
- [x] Styled client, route map, timelines, metrics, search replay and route playback.

Acceptance: launch a real baseline run, inspect its verified solution, close/reopen the client and replay the saved result. Replay terminal state matches saved solution metrics. A second installed city can be selected without mixing datasets. Performance is measured on the actual client.

Verification: both 20-stop seeded city demos ran through real local OSRM and
produced independently validated feasible solutions; terminal snapshots matched
saved results. Browser checks cover closing/reopening during a job, replay position,
map editing and Modena with online tiles blocked and reduced motion. Client frame
intervals are measured separately from solving. The measured SwiftShader playback
p95 is about 100 ms; the provisional 50 ms target remains unmet. Commands, provenance and remaining
limits are documented in [M2 local operations](m2-local.md).

## M3 — Comparative experiments

- [x] Ordered/shuffled custom policies and optional OR-Tools elapsed-time adapter.
- [x] Frozen 84-case main design plus 18 recording controls, isolated queue execution and descriptive exports.
- [x] Multiple time/fuel preferences, found nondominated alternatives, fixed hypervolume references and compatible client replay.

Verification and full-pilot evidence: [M3 operations](m3-comparisons.md). No method superiority is required or inferred from this constructed two-instance pilot.

Acceptance: a reproducible command/configuration produces comparable results and manifests; computation and rendering timings are separated; model estimates and reference solution status are explicit.

## M4 — Learned search

- [x] Train a supervised move-ordering policy over shared classical operators.
- [x] Freeze disjoint scenario splits, export a checked CPU model and record training cost.
- [x] Complete all 336 held-out cases, independent audit, verified repeatable import and browser acceptance.

Current evidence: [M4 operations](m4-learning.md).

Acceptance: reproducible evaluation supports a scoped positive, neutral or negative result; AI superiority is not a release requirement.

## M5 — Ai360 engine deployment

- [x] Inspect host OS, RAM, access, dependencies and supported runtimes for M4 batch execution.
- [x] Prepare authenticated LAN service, browser configuration and local tests.
- [x] Deploy on Ai360; test main-computer reconnection and persistence.
- [x] Benchmark CPU first; evaluate GPU/NPU only where compatible and useful.

Verification and acceptance evidence: [M5 LAN operations](m5-lan.md).

Acceptance: the main computer controls and replays an Ai360 run, client disconnection does not stop computation, hardware and timing provenance are recorded. This deployment can be moved earlier if required; acceleration remains optional.

## M6 — Scale ladder, learned policy v2, stochastic robustness and R&D lab

- [x] Extend scenario generation and learned policy to Feature Schema v2 (38 features, `38→64→32→2`) across 20, 50 and 100 stops, 1–5 vans and both service modes while preserving M4 v1 checkpoint compatibility.
- [x] Implement an independent Monte Carlo stochastic robustness evaluator (`engine/stochastic.py`) over travel-time and service-duration perturbations.
- [x] Activate the client R&D navigation and laboratory view (`client/src/RnDView.tsx`) for stochastic stress-testing, model inspection and multi-scale campaign launching on Ai360.
- [x] Split `maplibre-gl` and `@deck.gl` into separate vendor chunks in the client production build.

Acceptance: independent Monte Carlo evaluation matches deterministic accounting at zero dispersion, v1 and v2 checkpoints run reproducibly on CPU, the R&D laboratory controls stochastic tests and multi-scale campaigns from the client, and production chunks build without size warnings.

## M7 — Alternative road paths and operational constraints

- [x] Extract multiple directed road path alternatives per origin-destination leg from OSRM (e.g., fastest vs. shortest/lower-fuel paths) under an explicit path-policy contract.
- [x] Extend the independent validator, neighborhood operators and learned feature schema to jointly select stop order and leg path alternatives along the time/fuel trade-off front.
- [x] Add optional operational constraints (heterogeneous fleet capacities, mandatory driver breaks or mixed pickup/delivery loads) with independent validation and backward-compatible scenario schemas.

Acceptance: the independent validator verifies path-selection and operational constraints; controlled experiments quantify whether multi-path routing expands the nondominated time/fuel Pareto front compared with fixed single-path routing.

## M8 — Traffic microsimulation and calibrated fuel validation

- [ ] Integrate an offline traffic microsimulation and emissions reference (e.g., Eclipse SUMO) over the frozen city road networks to replay planned schedules under signal timing and congestion dynamics.
- [ ] Calibrate and benchmark the parametric load-dependent fuel model against microsimulated consumption curves, recording calibration parameters, residuals and validity bounds.
- [ ] Separate uncalibrated parametric estimates from simulation-calibrated fuel metrics across run artifacts, exports and client views.

Acceptance: a reproducible pipeline evaluates feasible VRP solutions in the traffic microsimulator and reports travel-time drift, window compliance and fuel-model calibration error without claiming real-fleet telemetry equivalence unless measured on physical vehicles.

## M9 — Desktop packaging and standalone distribution

- [ ] Package the TypeScript/React laboratory client as a standalone desktop application (e.g., Tauri) supporting both local loopback execution and authenticated HTTPS/WSS connection to Ai360.
- [ ] Provide reproducible desktop build scripts, offline static asset bundling and signed release manifests while keeping the Python engine and domain contracts unchanged.

Acceptance: the packaged desktop application passes the same scenario editing, search replay, route playback, R&D and authenticated Ai360 LAN acceptance checks as the browser client.
